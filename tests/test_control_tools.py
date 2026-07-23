# -*- coding: utf-8 -*-
import asyncio
import ast
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_mihome_control_test_package"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _install_stubs():
    astrbot = types.ModuleType("astrbot")
    astrbot_api = types.ModuleType("astrbot.api")
    astrbot_api.logger = MagicMock()
    astrbot.api = astrbot_api
    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = astrbot_api

    mijia = types.ModuleType("mijiaAPI")

    class _API:
        def __init__(self, *_args, **_kwargs):
            pass

    class _Device:
        pass

    mijia.mijiaAPI = _API
    mijia.mijiaDevice = _Device
    for name in (
        "LoginError",
        "DeviceNotFoundError",
        "DeviceSetError",
        "DeviceGetError",
        "DeviceActionError",
        "APIError",
    ):
        setattr(mijia, name, type(name, (Exception,), {}))
    sys.modules["mijiaAPI"] = mijia

    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(ROOT)]
    sys.modules[PACKAGE] = package


_install_stubs()
_load_module(f"{PACKAGE}.data_manager", ROOT / "data_manager.py")
profiles = _load_module(f"{PACKAGE}.device_profiles", ROOT / "device_profiles.py")
client_module = _load_module(f"{PACKAGE}.mihome_client", ROOT / "mihome_client.py")
control_module = _load_module(f"{PACKAGE}.control_tools", ROOT / "control_tools.py")


class _Event:
    def __init__(self, admin: bool = True):
        self.admin = admin


class _Plugin:
    def __init__(
        self,
        *,
        model: str = "lumi.acpartner.mcn02",
        category: str = "空调类别",
        allowed=None,
        enabled: bool = True,
        admin_only: bool = True,
    ):
        self.config = {
            "control_tool": {
                "enable": enabled,
                "admin_only": admin_only,
                "allowed_devices": ["客厅空调"] if allowed is None else allowed,
            }
        }
        self.client = types.SimpleNamespace(
            set_property=AsyncMock(return_value=True),
            run_action=AsyncMock(),
            run_action_with_in=AsyncMock(),
            get_device_capabilities=AsyncMock(
                return_value={
                    "readable": ["temperature", "wifi_ssid"],
                    "writable": ["on", "unsupported_raw_prop"],
                    "actions": ["start"],
                }
            ),
        )
        self._model = model
        self._category = category

    def _event_is_admin(self, event):
        return bool(event.admin)

    def _parse_device_map(self):
        return {"客厅空调": "sensitive-did-123"}

    def _parse_category_map(self):
        return {"客厅空调": self._category}

    def _get_model_by_did(self, _did):
        return self._model

    @staticmethod
    def _parse_value(value):
        if isinstance(value, (bool, int, float)):
            return value
        raw = str(value).strip()
        if raw.lower() in {"true", "false"}:
            return raw.lower() == "true"
        try:
            return int(raw)
        except ValueError:
            return raw


class AccessAndDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_control_tool_fails_closed_when_allowlist_is_empty(self):
        plugin = _Plugin(allowed=[])
        service = control_module.MiHomeControlTools(plugin)
        result = await service.list_devices(_Event())
        self.assertIn("白名单", result)

    async def test_non_admin_is_denied_by_plugin_admin_semantics(self):
        plugin = _Plugin(admin_only=True)
        service = control_module.MiHomeControlTools(plugin)
        result = await service.list_devices(_Event(admin=False))
        self.assertIn("AstrBot 管理员", result)

    async def test_list_and_inspect_never_expose_did(self):
        plugin = _Plugin()
        service = control_module.MiHomeControlTools(plugin)
        listed = await service.list_devices(_Event())
        inspected = await service.inspect_device(_Event(), "客厅空调")
        self.assertNotIn("sensitive-did-123", listed)
        self.assertNotIn("sensitive-did-123", inspected)
        self.assertNotIn("wifi_ssid", inspected)
        self.assertIn("observed_capabilities", inspected)

    async def test_unknown_profile_is_inspection_only(self):
        plugin = _Plugin(model="unknown.model", category="空调类别")
        service = control_module.MiHomeControlTools(plugin)
        result = await service.control_device(
            _Event(),
            "客厅空调",
            [{"prop": "on", "value": True}],
        )
        self.assertIn("未配置受信任", result)
        plugin.client.set_property.assert_not_awaited()


class PropertyControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_operation_limit_is_hard_capped(self):
        plugin = _Plugin()
        service = control_module.MiHomeControlTools(plugin)
        operations = [{"prop": "开关", "value": True}] * 6
        result = await service.control_device(_Event(), "客厅空调", operations)
        self.assertIn("最多允许 5", result)
        plugin.client.set_property.assert_not_awaited()

    async def test_duplicate_properties_are_rejected(self):
        plugin = _Plugin()
        service = control_module.MiHomeControlTools(plugin)
        result = await service.control_device(
            _Event(),
            "客厅空调",
            [
                {"prop": "开关", "value": True},
                {"prop": "on", "value": False},
            ],
        )
        self.assertIn("不能重复", result)
        plugin.client.set_property.assert_not_awaited()

    async def test_nested_or_non_finite_values_are_rejected(self):
        for invalid in ({"unexpected": True}, [1, 2], float("nan")):
            plugin = _Plugin()
            service = control_module.MiHomeControlTools(plugin)
            result = await service.control_device(
                _Event(),
                "客厅空调",
                [{"prop": "开关", "value": invalid}],
            )
            self.assertIn("属性值", result)
            plugin.client.set_property.assert_not_awaited()

    async def test_partial_result_does_not_echo_values(self):
        plugin = _Plugin()
        plugin.client.set_property.side_effect = [
            True,
            client_module.MiHomeControlError("device_rejected"),
        ]
        service = control_module.MiHomeControlTools(plugin)
        result = await service.control_device(
            _Event(),
            "客厅空调",
            [
                {"prop": "开关", "value": True},
                {"prop": "温度", "value": 26},
            ],
        )
        payload = json.loads(result)
        self.assertEqual(payload["summary"]["success"], 1)
        self.assertEqual(payload["summary"]["failed"], 1)
        self.assertFalse(payload["summary"]["atomic"])
        self.assertNotIn('"value"', result)

    async def test_repeated_physical_control_is_rate_limited(self):
        plugin = _Plugin()
        service = control_module.MiHomeControlTools(plugin)
        operation = [{"prop": "开关", "value": True}]
        first = await service.control_device(_Event(), "客厅空调", operation)
        second = await service.control_device(_Event(), "客厅空调", operation)
        self.assertIn('"success": 1', first)
        self.assertIn("操作过于频繁", second)
        self.assertEqual(plugin.client.set_property.await_count, 1)

    async def test_gateway_accepted_is_reported_as_unconfirmed(self):
        plugin = _Plugin()
        plugin.client.set_property.return_value = False
        service = control_module.MiHomeControlTools(plugin)
        result = await service.control_device(
            _Event(),
            "客厅空调",
            [{"prop": "开关", "value": True}],
        )
        payload = json.loads(result)
        self.assertEqual(payload["summary"]["success"], 0)
        self.assertEqual(payload["summary"]["unconfirmed"], 1)
        self.assertEqual(payload["summary"]["failed"], 0)
        self.assertEqual(payload["results"][0]["status"], "unconfirmed")

    def test_cooldown_uses_device_identity_not_alias(self):
        service = control_module.MiHomeControlTools(_Plugin())
        self.assertIsNone(service._reserve_execution("same-did", "别名一"))
        self.assertIn(
            "操作过于频繁",
            service._reserve_execution("same-did", "别名二"),
        )


class ActionTests(unittest.IsolatedAsyncioTestCase):
    async def test_parameterized_action_is_scoped_and_output_hides_text(self):
        plugin = _Plugin(
            model="xiaomi.wifispeaker.oh2p",
            category="音箱类别",
        )
        service = control_module.MiHomeControlTools(plugin)
        secret_text = "今晚十点提醒我关窗"
        result = await service.call_action(
            _Event(),
            "客厅空调",
            "播放文本",
            [secret_text],
        )
        plugin.client.run_action_with_in.assert_awaited_once()
        self.assertIn("成功", result)
        self.assertNotIn(secret_text, result)

    async def test_unverified_action_parameters_are_not_forwarded(self):
        plugin = _Plugin(model="xiaomi.vacuum.ov21cn", category="扫地机类别")
        service = control_module.MiHomeControlTools(plugin)
        result = await service.call_action(
            _Event(),
            "客厅空调",
            "start_sweep",
            ["unexpected"],
        )
        self.assertIn("不会把参数直接透传", result)
        plugin.client.run_action.assert_not_awaited()

    def test_strict_bool_parameter_rejects_unknown_strings(self):
        with self.assertRaises(ValueError):
            control_module.MiHomeControlTools._coerce_parameter(
                "随便",
                {"type": "bool", "name": "silent"},
            )


class ClientCompatibilityTests(unittest.TestCase):
    def test_rw_parser_handles_current_and_legacy_shapes(self):
        parser = client_module.MiHomeClient._parse_rw_field
        self.assertEqual(parser("rw"), (True, True))
        self.assertEqual(parser(["read", "write"]), (True, True))
        self.assertEqual(parser(["write"]), (False, True))
        self.assertEqual(parser(None), (False, False))

    def test_nonzero_action_code_is_rejected(self):
        with self.assertRaises(client_module.MiHomeControlError):
            client_module.MiHomeClient._validate_action_response(
                {"result": [{"code": -704042011}]}
            )
        self.assertTrue(
            client_module.MiHomeClient._validate_action_response({"code": 0})
        )
        self.assertFalse(
            client_module.MiHomeClient._validate_action_response({"code": 1})
        )
        self.assertFalse(
            client_module.MiHomeClient._validate_action_response(None)
        )
        self.assertFalse(
            client_module.MiHomeClient._validate_action_response({})
        )


class ClientCloudResultTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _client_with_property_response(response):
        client = object.__new__(client_module.MiHomeClient)
        client._login_status = client_module.LOGIN_IDLE
        client._api_lock = asyncio.Lock()
        client.data_manager = MagicMock()
        client.api = types.SimpleNamespace(
            set_devices_prop=MagicMock(return_value=response),
        )
        prop = types.SimpleNamespace(
            rw="rw",
            type="int",
            range=[1, 100, 1],
            value_list=None,
            method={"siid": 2, "piid": 1},
        )
        device = types.SimpleNamespace(prop_list={"volume": prop})
        client._prepare_device_sync = MagicMock(return_value=device)
        return client

    async def test_property_cloud_codes_are_three_state(self):
        confirmed = self._client_with_property_response({"code": 0})
        self.assertTrue(
            await confirmed.set_property("did", "volume", 20, "音箱")
        )

        unconfirmed = self._client_with_property_response({"code": 1})
        self.assertFalse(
            await unconfirmed.set_property("did", "volume", 20, "音箱")
        )

        rejected = self._client_with_property_response({"code": -1})
        with self.assertRaises(client_module.MiHomeControlError):
            await rejected.set_property("did", "volume", 20, "音箱")

    async def test_scene_requires_explicit_true_result(self):
        async def run_case(result):
            client = object.__new__(client_module.MiHomeClient)
            client._login_status = client_module.LOGIN_IDLE
            client._api_lock = asyncio.Lock()
            client.data_manager = MagicMock()
            client.api = types.SimpleNamespace(
                login=MagicMock(),
                run_scene=MagicMock(return_value=result),
            )
            return await client.run_scene(
                "scene-id",
                "home-id",
                "晚安",
            )

        self.assertIsNone(await run_case(True))
        for result in (False, None, {}):
            with self.assertRaises(client_module.MiHomeSceneError) as raised:
                await run_case(result)
            self.assertEqual(str(raised.exception), "scene_unconfirmed")


class AstrBotToolSchemaTests(unittest.TestCase):
    def test_control_tools_use_structured_array_docstrings(self):
        tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
        plugin = next(
            item
            for item in tree.body
            if isinstance(item, ast.ClassDef)
            and item.name == "MiHomeControlPlugin"
        )
        docs = {
            item.name: ast.get_docstring(item) or ""
            for item in plugin.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertIn(
            "operations(array[object])",
            docs["control_mihome_device_tool"],
        )
        self.assertIn("params(array)", docs["call_mihome_action_tool"])
        self.assertNotIn("JSON 字符串", docs["control_mihome_device_tool"])


if __name__ == "__main__":
    unittest.main()
