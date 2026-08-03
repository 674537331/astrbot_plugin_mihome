# -*- coding: utf-8 -*-
import asyncio
import ast
import base64
import importlib.util
import json
import os
import stat
import sys
import tempfile
import threading
import types
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


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

    class _DevProp:
        def __init__(self, data):
            # 与 mijiaAPI 4.1.3 DevProp 保持一致的严格取键行为，
            # 以覆盖插件对缺失字段属性的跳过逻辑。
            self.name = data["name"]
            self.desc = data["description"]
            self.type = data["type"]
            if self.type not in ["bool", "int", "uint", "float", "string"]:
                raise ValueError(
                    f"不支持的类型: {self.type}, 可选类型: "
                    "bool, int, uint, float, string"
                )
            self.rw = data["rw"]
            self.range = data["range"]
            self.value_list = data.get("value-list", None)
            self.method = data["method"]

    class _DevAction:
        def __init__(self, data):
            # 与 mijiaAPI 4.1.3 DevAction 保持一致的严格取键行为。
            self.name = data["name"]
            self.desc = data["description"]
            self.method = data["method"]

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
    devices = types.ModuleType("mijiaAPI.devices")
    devices.DevProp = _DevProp
    devices.DevAction = _DevAction
    sys.modules["mijiaAPI.devices"] = devices

    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(ROOT)]
    sys.modules[PACKAGE] = package


_install_stubs()
data_manager_module = _load_module(
    f"{PACKAGE}.data_manager",
    ROOT / "data_manager.py",
)
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

    def test_cooldown_tracking_is_bounded_without_dropping_active_entries(self):
        service = control_module.MiHomeControlTools(_Plugin())
        with patch.object(
            control_module.time,
            "monotonic",
            return_value=100.0,
        ):
            for index in range(
                control_module.MAX_COOLDOWN_TRACKED_DEVICES
            ):
                self.assertIsNone(
                    service._reserve_execution(
                        f"did-{index}",
                        f"设备 {index}",
                    )
                )
            self.assertIn(
                "请求过多",
                service._reserve_execution("overflow", "额外设备"),
            )
            self.assertIn(
                "操作过于频繁",
                service._reserve_execution("did-0", "设备 0"),
            )

        self.assertEqual(
            len(service._last_execution_at),
            control_module.MAX_COOLDOWN_TRACKED_DEVICES,
        )

    def test_cooldown_tracking_removes_only_expired_entries(self):
        service = control_module.MiHomeControlTools(_Plugin())
        service._last_execution_at = {
            "expired": 96.0,
            "active": 99.0,
        }
        with patch.object(
            control_module.time,
            "monotonic",
            return_value=100.0,
        ):
            self.assertIsNone(
                service._reserve_execution("new", "新设备")
            )

        self.assertNotIn("expired", service._last_execution_at)
        self.assertIn("active", service._last_execution_at)
        self.assertIn("new", service._last_execution_at)


class ViomiAirConditionProfileTests(unittest.TestCase):
    def setUp(self):
        self.plugin = _Plugin(
            model="viomi.aircondition.y71",
            category=profiles.CATEGORY_AC,
        )
        self.service = control_module.MiHomeControlTools(self.plugin)
        self.device, error = self.service._resolve_allowed_device("客厅空调")
        self.assertIsNone(error)
        self.assertIsNotNone(self.device)

    def test_profile_uses_property_scoped_enum_values(self):
        mode = self.service._translate_property(
            self.device,
            "模式",
            "自动模式",
        )
        fan = self.service._translate_property(
            self.device,
            "风速",
            "自动风",
        )
        wrong_enum = self.service._translate_property(
            self.device,
            "风速",
            "自动模式",
        )

        self.assertTrue(mode["ok"])
        self.assertEqual(mode["value"], 1)
        self.assertTrue(fan["ok"])
        self.assertEqual(fan["value"], 0)
        self.assertFalse(wrong_enum["ok"])

    def test_profile_excludes_internal_coordination_writes(self):
        capabilities = self.service._aggregate_capabilities(self.device)
        writable = {
            item["key"] for item in capabilities["writable_properties"]
        }

        self.assertTrue(capabilities["direct_control_supported"])
        self.assertIn("target_temperature", writable)
        self.assertNotIn("average_temperature", writable)
        self.assertNotIn("average_humidity", writable)
        self.assertNotIn("temp_unbalance", writable)
        self.assertEqual(capabilities["actions"], [])


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

    async def test_cross_device_speaker_actions_are_never_exposed(self):
        plugin = _Plugin(
            model="xiaomi.wifispeaker.oh2p",
            category="音箱类别",
        )
        service = control_module.MiHomeControlTools(plugin)
        device, error = service._resolve_allowed_device("客厅空调")
        self.assertIsNone(error)

        capabilities = service._aggregate_capabilities(device)
        action_keys = {item["key"] for item in capabilities["actions"]}
        self.assertNotIn("execute-text-directive", action_keys)
        self.assertNotIn("tv-switchon", action_keys)

        for action in ("execute-text-directive", "tv-switchon"):
            result = await service.call_action(
                _Event(),
                "客厅空调",
                action,
                [],
            )
            self.assertIn("不支持动作", result)
        plugin.client.run_action.assert_not_awaited()
        plugin.client.run_action_with_in.assert_not_awaited()

    async def test_unverified_action_parameters_are_not_forwarded(self):
        plugin = _Plugin(model="xiaomi.vacuum.ov21cn", category="扫地机类别")
        service = control_module.MiHomeControlTools(plugin)
        result = await service.call_action(
            _Event(),
            "客厅空调",
            "start_sweep",
            ["unexpected"],
        )
        self.assertIn("仅接受无参调用", result)
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

    def test_integer_properties_reject_fractional_values(self):
        client = object.__new__(client_module.MiHomeClient)
        prop = types.SimpleNamespace(
            rw="rw",
            type="int",
            range=[16, 31, 1],
            value_list=None,
            method={"siid": 2, "piid": 1},
        )
        device = types.SimpleNamespace(prop_list={"target_temperature": prop})

        for invalid in (26.9, Decimal("26.9"), "26.9"):
            with self.assertRaises(ValueError):
                client._build_property_method(
                    device,
                    "did",
                    "target_temperature",
                    invalid,
                )

        method = client._build_property_method(
            device,
            "did",
            "target_temperature",
            Decimal("26.0"),
        )
        self.assertEqual(method["value"], 26)

    def test_api_session_receives_default_network_timeout(self):
        client = object.__new__(client_module.MiHomeClient)
        original_request = MagicMock(return_value="ok")
        session = types.SimpleNamespace(request=original_request)
        client.api = types.SimpleNamespace(session=session)

        client._configure_api_session()
        result = session.request("POST", "https://example.invalid")

        self.assertEqual(result, "ok")
        original_request.assert_called_once_with(
            "POST",
            "https://example.invalid",
            timeout=client_module.HTTP_REQUEST_TIMEOUT,
        )

    def test_api_session_timeout_survives_upstream_session_rebuild(self):
        class FakeAPI:
            def __init__(self):
                self.session = types.SimpleNamespace(
                    request=MagicMock(return_value="first")
                )

            def _init_session(self):
                self.session = types.SimpleNamespace(
                    request=MagicMock(return_value="rebuilt")
                )

            def _get_location(self):
                return {}

        client = object.__new__(client_module.MiHomeClient)
        api = FakeAPI()
        client._configure_api_instance(api)

        api._init_session()
        rebuilt_request = api.session.request.func
        result = api.session.request("POST", "https://example.invalid")

        self.assertEqual(result, "rebuilt")
        rebuilt_request.assert_called_once_with(
            "POST",
            "https://example.invalid",
            timeout=client_module.HTTP_REQUEST_TIMEOUT,
        )

    def test_token_location_request_has_explicit_timeout(self):
        response = types.SimpleNamespace(status_code=200, text="ok")
        service_session = MagicMock()
        service_session.__enter__.return_value = service_session
        service_session.get.return_value = response
        api_session = types.SimpleNamespace(
            request=MagicMock(),
            get=MagicMock(),
        )
        api = types.SimpleNamespace(
            user_agent="ua",
            deviceId="device",
            pass_o="pass",
            auth_data={
                "passToken": "pass-token",
                "userId": "user",
                "cUserId": "c-user",
            },
            locale="zh_CN",
            service_login_url="https://account.example.invalid",
            session=api_session,
            _handle_ret=MagicMock(
                return_value={
                    "code": 1,
                    "location": "https://callback.invalid/?code=1",
                }
            ),
            _init_session=MagicMock(),
            _get_location=MagicMock(),
        )
        client = object.__new__(client_module.MiHomeClient)

        with patch.object(
            client_module.requests,
            "Session",
            return_value=service_session,
        ):
            client._configure_api_instance(api)
            result = api._get_location()

        self.assertEqual(result, {"code": "1"})
        service_session.get.assert_called_once()
        self.assertEqual(
            service_session.get.call_args.kwargs["timeout"],
            client_module.HTTP_REQUEST_TIMEOUT,
        )

    def test_prepare_device_uses_validated_local_spec_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            auth_path = Path(temp_dir) / "auth.json"
            model = "example.switch.v1"
            spec_path = Path(temp_dir) / f"{model}.json"
            spec_path.write_text(
                json.dumps(
                    {
                        "name": "测试开关",
                        "model": model,
                        "properties": [
                            {
                                "name": "on",
                                "description": "开关",
                                "type": "bool",
                                "rw": "rw",
                                "range": None,
                                "value-list": None,
                                "method": {"siid": 2, "piid": 1},
                            }
                        ],
                        "actions": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            client = object.__new__(client_module.MiHomeClient)
            client.data_manager = MagicMock()
            client.data_manager.get_auth_path.return_value = str(auth_path)
            client.api = types.SimpleNamespace(
                get_devices_list=MagicMock(
                    return_value=[
                        {
                            "did": "did-1",
                            "name": "客厅开关",
                            "model": model,
                        }
                    ]
                )
            )

            with patch.object(
                client_module.subprocess,
                "run",
                side_effect=AssertionError("valid cache must skip worker"),
            ):
                with patch.object(
                    client_module.time,
                    "monotonic",
                    return_value=100.0,
                ):
                    device = client._prepare_device_sync("did-1")
                with patch.object(
                    client_module.time,
                    "monotonic",
                    return_value=110.0,
                ):
                    cached_device = client._prepare_device_sync("did-1")
                with patch.object(
                    client_module.time,
                    "monotonic",
                    return_value=161.0,
                ):
                    refreshed_device = client._prepare_device_sync("did-1")

            self.assertEqual(device.name, "客厅开关")
            self.assertIn("on", device.prop_list)
            self.assertIs(cached_device, device)
            self.assertIsNot(refreshed_device, device)
            self.assertEqual(client.api.get_devices_list.call_count, 2)

    def test_prepared_device_cache_is_bounded_and_scoped_to_api(self):
        devices = [
            {
                "did": f"did-{index}",
                "name": f"设备 {index}",
                "model": "example.switch.v1",
            }
            for index in range(client_module.PREPARED_DEVICE_CACHE_MAX_SIZE + 1)
        ]
        client = object.__new__(client_module.MiHomeClient)
        client.api = types.SimpleNamespace(
            get_devices_list=MagicMock(return_value=devices)
        )
        client._load_or_fetch_device_spec = MagicMock(
            return_value={
                "name": "测试开关",
                "properties": [],
                "actions": [],
            }
        )

        with patch.object(
            client_module.time,
            "monotonic",
            return_value=100.0,
        ):
            for row in devices:
                client._prepare_device_sync(row["did"])

        self.assertEqual(
            len(client._prepared_device_cache),
            client_module.PREPARED_DEVICE_CACHE_MAX_SIZE,
        )
        self.assertNotIn("did-0", client._prepared_device_cache)

        previous_api = client.api
        client.api = types.SimpleNamespace(
            get_devices_list=MagicMock(return_value=[devices[-1]])
        )
        with patch.object(
            client_module.time,
            "monotonic",
            return_value=110.0,
        ):
            refreshed = client._prepare_device_sync(devices[-1]["did"])

        self.assertIs(refreshed.api, client.api)
        self.assertIsNot(refreshed.api, previous_api)
        client.api.get_devices_list.assert_called_once_with()

    def test_shared_list_key_error_degrades_to_device_not_found(self):
        # 上游共享列表接口对部分账号返回缺少 key 的响应（KeyError: 'list'），
        # 只读状态准备阶段应容错而不是把 KeyError 抛给调用方。
        client = object.__new__(client_module.MiHomeClient)
        client.api = types.SimpleNamespace(
            get_devices_list=MagicMock(return_value=[]),
            get_shared_devices_list=MagicMock(side_effect=KeyError("list")),
        )
        client._load_or_fetch_device_spec = MagicMock()

        with self.assertRaises(client_module.DeviceNotFoundError):
            client._prepare_device_sync("shared-did")

        client.api.get_devices_list.assert_called_once_with()
        client.api.get_shared_devices_list.assert_called_once_with()
        client._load_or_fetch_device_spec.assert_not_called()

    def test_shared_list_network_error_degrades_to_device_not_found(self):
        client = object.__new__(client_module.MiHomeClient)
        client.api = types.SimpleNamespace(
            get_devices_list=MagicMock(return_value=[]),
            get_shared_devices_list=MagicMock(
                side_effect=client_module.RequestsTimeout("offline")
            ),
        )
        client._load_or_fetch_device_spec = MagicMock()

        with self.assertRaises(client_module.DeviceNotFoundError):
            client._prepare_device_sync("shared-did")

        client.api.get_shared_devices_list.assert_called_once_with()

    def test_malformed_spec_property_is_skipped_not_fatal(self):
        # 个别型号规格属性缺少 rw/method 等字段或带未知类型，
        # 应跳过该属性而不是让整台设备的状态读取崩溃。
        client = object.__new__(client_module.MiHomeClient)
        client.api = types.SimpleNamespace(
            get_devices_list=MagicMock(
                return_value=[
                    {
                        "did": "did-1",
                        "name": "异常设备",
                        "model": "example.odd.v1",
                    }
                ]
            )
        )
        client._load_or_fetch_device_spec = MagicMock(
            return_value={
                "name": "异常设备",
                "properties": [
                    {
                        "name": "on",
                        "description": "开关",
                        "type": "bool",
                        "rw": "rw",
                        "range": None,
                        "value-list": None,
                        "method": {"siid": 2, "piid": 1},
                    },
                    {
                        "name": "broken",
                        # 缺少 description/type/rw/range/method，DevProp 应被跳过
                    },
                ],
                "actions": [
                    {
                        "name": "toggle",
                        "description": "切换",
                        "method": {"siid": 2, "aiid": 1},
                    },
                    {
                        "name": "broken-action",
                        # 缺少 method，DevAction 应被跳过
                    },
                ],
            }
        )

        device = client._prepare_device_sync("did-1")

        self.assertIn("on", device.prop_list)
        self.assertNotIn("broken", device.prop_list)
        self.assertIn("toggle", device.action_list)
        self.assertNotIn("broken-action", device.action_list)


class DataManagerCredentialTests(unittest.TestCase):
    def test_windows_reparse_point_is_rejected(self):
        path = MagicMock()
        path.is_symlink.return_value = False
        path.lstat.return_value = types.SimpleNamespace(
            st_file_attributes=0x0400
        )
        with patch.object(data_manager_module.os, "name", "nt"):
            self.assertTrue(
                data_manager_module.MiHomeDataManager
                ._is_link_or_reparse_point(path)
            )

    def test_clear_auth_is_idempotent_when_file_is_absent(self):
        manager = object.__new__(data_manager_module.MiHomeDataManager)
        manager.auth_path = MagicMock()
        manager.auth_path.exists.return_value = False
        manager.auth_path.is_symlink.return_value = False

        self.assertTrue(manager.clear_auth_file())
        manager.auth_path.unlink.assert_not_called()

    def test_clear_auth_reports_successful_removal(self):
        manager = object.__new__(data_manager_module.MiHomeDataManager)
        manager.auth_path = MagicMock()
        manager.auth_path.exists.return_value = True

        self.assertTrue(manager.clear_auth_file())
        manager.auth_path.unlink.assert_called_once_with()

    def test_clear_auth_reports_permission_failure(self):
        manager = object.__new__(data_manager_module.MiHomeDataManager)
        manager.auth_path = MagicMock()
        manager.auth_path.exists.return_value = True
        manager.auth_path.unlink.side_effect = PermissionError("locked")

        self.assertFalse(manager.clear_auth_file())

    def test_auth_file_is_hardened_to_owner_only_on_posix(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            auth_path = Path(temp_dir) / "auth.json"
            auth_path.write_text("{}", encoding="utf-8")
            manager = object.__new__(data_manager_module.MiHomeDataManager)
            manager.auth_path = auth_path

            self.assertTrue(manager.harden_auth_file())
            if os.name != "nt":
                self.assertEqual(
                    stat.S_IMODE(auth_path.stat().st_mode),
                    0o600,
                )

    def test_broken_auth_symlink_is_rejected_before_use(self):
        manager = object.__new__(data_manager_module.MiHomeDataManager)
        manager.data_dir = MagicMock()
        manager.auth_path = MagicMock()
        manager.auth_path.is_symlink.return_value = True
        manager.auth_path.exists.return_value = False
        manager._harden_path = MagicMock(return_value=True)

        self.assertFalse(manager.auth_storage_is_secure())
        manager.auth_path.is_file.assert_not_called()

    def test_atomic_state_replace_failure_preserves_previous_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = object.__new__(data_manager_module.MiHomeDataManager)
            manager.data_dir = Path(temp_dir)
            manager.state_path = manager.data_dir / "state.json"
            manager._state_lock = threading.RLock()
            manager._state_corrupt = False
            manager.state_path.write_text('{"old": true}', encoding="utf-8")

            with patch.object(
                data_manager_module.os,
                "replace",
                side_effect=PermissionError("locked"),
            ):
                self.assertFalse(manager.save_state({"new": True}))

            self.assertEqual(
                manager.state_path.read_text(encoding="utf-8"),
                '{"old": true}',
            )
            self.assertEqual(list(manager.data_dir.glob(".state-*.tmp")), [])

    def test_unchanged_state_update_skips_atomic_write(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = object.__new__(data_manager_module.MiHomeDataManager)
            manager.data_dir = Path(temp_dir)
            manager.state_path = manager.data_dir / "state.json"
            manager._state_lock = threading.RLock()
            manager._state_corrupt = False
            manager.state_path.write_text(
                '{"login_status": "success", "devices": ["did-1"]}',
                encoding="utf-8",
            )

            with patch.object(manager, "save_state") as save_state:
                self.assertTrue(
                    manager.update_state(
                        login_status="success",
                        devices=["did-1"],
                    )
                )

            save_state.assert_not_called()

    def test_compare_and_update_state_does_not_overwrite_newer_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = object.__new__(data_manager_module.MiHomeDataManager)
            manager.data_dir = Path(temp_dir)
            manager.state_path = manager.data_dir / "state.json"
            manager._state_lock = threading.RLock()
            manager._state_corrupt = False
            manager.state_path.write_text(
                '{"revision": 2, "devices": ["did-new"]}',
                encoding="utf-8",
            )

            result, observed = manager.compare_and_update_state(
                {"revision": 1, "devices": ["did-old"]},
                last_login_error="",
            )

            self.assertEqual(result, "changed")
            self.assertEqual(
                observed,
                {"revision": 2, "devices": ["did-new"]},
            )
            self.assertEqual(manager.load_state(), observed)

    def test_compare_and_update_keeps_observed_replace_on_harden_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = object.__new__(data_manager_module.MiHomeDataManager)
            manager.data_dir = Path(temp_dir)
            manager.state_path = manager.data_dir / "state.json"
            manager._state_lock = threading.RLock()
            manager._state_corrupt = False
            baseline = {"last_login_error": "鉴权失效"}
            manager.state_path.write_text(
                json.dumps(baseline, ensure_ascii=False),
                encoding="utf-8",
            )

            with patch.object(
                manager,
                "_harden_path",
                side_effect=[True, False],
            ):
                result, observed = manager.compare_and_update_state(
                    baseline,
                    last_login_error="",
                )

            self.assertEqual(result, "failed")
            self.assertEqual(observed, {"last_login_error": ""})
            self.assertEqual(manager.load_state(), observed)

    def test_corrupt_state_is_backed_up_before_atomic_recovery(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = object.__new__(data_manager_module.MiHomeDataManager)
            manager.data_dir = Path(temp_dir)
            manager.state_path = manager.data_dir / "state.json"
            manager._state_lock = threading.RLock()
            manager._state_corrupt = False
            manager.state_path.write_text("{broken", encoding="utf-8")

            self.assertEqual(manager.load_state(), {})
            self.assertTrue(manager.update_state(recovered=True))

            recovered = json.loads(
                manager.state_path.read_text(encoding="utf-8")
            )
            backups = list(
                manager.data_dir.glob("state.json.corrupt-*")
            )
            self.assertEqual(recovered, {"recovered": True})
            self.assertEqual(len(backups), 1)
            self.assertEqual(
                backups[0].read_text(encoding="utf-8"),
                "{broken",
            )

    def test_empty_update_still_recovers_corrupt_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = object.__new__(data_manager_module.MiHomeDataManager)
            manager.data_dir = Path(temp_dir)
            manager.state_path = manager.data_dir / "state.json"
            manager._state_lock = threading.RLock()
            manager._state_corrupt = False
            manager.state_path.write_text("{broken", encoding="utf-8")

            self.assertTrue(manager.update_state())
            self.assertEqual(
                json.loads(manager.state_path.read_text(encoding="utf-8")),
                {},
            )
            self.assertEqual(
                len(list(manager.data_dir.glob("state.json.corrupt-*"))),
                1,
            )

    def test_duplicate_corrupt_state_backup_is_reused(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = object.__new__(data_manager_module.MiHomeDataManager)
            manager.data_dir = Path(temp_dir)
            manager.state_path = manager.data_dir / "state.json"
            manager._state_lock = threading.RLock()
            manager._state_corrupt = False

            for recovered_value in (1, 2):
                manager.state_path.write_text("{same-broken", encoding="utf-8")
                self.assertEqual(manager.load_state(), {})
                self.assertTrue(
                    manager.update_state(recovered=recovered_value)
                )

            backups = list(
                manager.data_dir.glob("state.json.corrupt-*")
            )
            self.assertEqual(len(backups), 1)
            self.assertEqual(
                backups[0].read_text(encoding="utf-8"),
                "{same-broken",
            )

    def test_corrupt_state_backups_keep_only_latest_five(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = object.__new__(data_manager_module.MiHomeDataManager)
            manager.data_dir = Path(temp_dir)
            manager.state_path = manager.data_dir / "state.json"
            manager._state_lock = threading.RLock()
            manager._state_corrupt = False

            with patch.object(
                data_manager_module.time,
                "time_ns",
                side_effect=range(100, 107),
            ):
                for index in range(7):
                    manager.state_path.write_text(
                        f"{{broken-{index}",
                        encoding="utf-8",
                    )
                    self.assertEqual(manager.load_state(), {})
                    self.assertTrue(manager.update_state(recovered=index))

            backups = sorted(
                manager.data_dir.glob("state.json.corrupt-*")
            )
            self.assertEqual(
                [path.name for path in backups],
                [
                    "state.json.corrupt-102",
                    "state.json.corrupt-103",
                    "state.json.corrupt-104",
                    "state.json.corrupt-105",
                    "state.json.corrupt-106",
                ],
            )

    def test_explicit_logout_cleanup_removes_corrupt_state_backups(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = object.__new__(data_manager_module.MiHomeDataManager)
            manager.data_dir = Path(temp_dir)
            manager.state_path = manager.data_dir / "state.json"
            manager._state_lock = threading.RLock()
            backup = manager.data_dir / "state.json.corrupt-123"
            abandoned_temp = manager.data_dir / ".state-crashed.tmp"
            backup.write_text('{"did_to_name":{"secret":"设备"}}', encoding="utf-8")
            abandoned_temp.write_text(
                '{"scenes":[{"scene_id":"private"}]}',
                encoding="utf-8",
            )

            self.assertTrue(manager.clear_state_backups())
            self.assertFalse(backup.exists())
            self.assertFalse(abandoned_temp.exists())


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

    async def test_login_status_accepts_existing_state_snapshot(self):
        client = object.__new__(client_module.MiHomeClient)
        client._login_status = client_module.LOGIN_IDLE
        client.data_manager = MagicMock()
        client.data_manager.auth_exists.return_value = True

        result = await client.get_login_status(
            {
                "last_login_at": "2026-07-24 12:00:00",
                "last_login_error": "",
            }
        )

        self.assertTrue(result["auth_exists"])
        self.assertEqual(result["last_login_at"], "2026-07-24 12:00:00")
        client.data_manager.load_state.assert_not_called()

    async def test_device_sync_invalidates_prepared_device_cache(self):
        client = object.__new__(client_module.MiHomeClient)
        client._login_status = client_module.LOGIN_IDLE
        client._api_lock = asyncio.Lock()
        client._prepared_device_cache = {
            "did-1": (100.0, object(), object()),
        }
        client.data_manager = MagicMock()
        client.data_manager.auth_exists.return_value = True
        client.data_manager.update_state.return_value = True
        client.data_manager.load_state.return_value = {
            "last_login_error": "网络异常: ConnectTimeout",
        }
        client.api = types.SimpleNamespace(
            get_devices_list=MagicMock(return_value=[]),
        )

        self.assertEqual(await client.get_devices(), [])
        self.assertEqual(client._prepared_device_cache, {})
        client.data_manager.update_state.assert_called_once_with(
            last_login_error="",
            last_shared_error="",
            did_to_name={},
            did_to_model={},
        )

        client.data_manager.update_state.reset_mock()
        client.data_manager.load_state.return_value = {
            "last_login_error": "登录凭证文件无法读取，请检查文件权限",
        }
        self.assertEqual(await client.get_devices(), [])
        client.data_manager.update_state.assert_called_once_with(
            last_shared_error="",
            did_to_name={},
            did_to_model={},
        )

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

    async def test_logout_preserves_client_state_when_auth_removal_fails(self):
        client = object.__new__(client_module.MiHomeClient)
        client._api_lock = asyncio.Lock()
        client._login_process = None
        client._login_status = client_module.LOGIN_IDLE
        client.data_manager = MagicMock()
        client.data_manager.clear_auth_file.return_value = False
        original_api = object()
        client.api = original_api

        with self.assertRaises(client_module.MiHomeClientError):
            await client.logout()

        self.assertIs(client.api, original_api)
        client.data_manager.update_state.assert_called_once_with(
            last_login_error="本地登录凭证移除失败，请检查文件权限",
        )

    async def test_logout_keeps_auth_when_login_worker_cannot_stop(self):
        class StuckProcess:
            returncode = None

            def kill(self):
                raise PermissionError("still running")

            async def wait(self):
                raise OSError("wait failed")

        process = StuckProcess()
        client = object.__new__(client_module.MiHomeClient)
        client._api_lock = asyncio.Lock()
        client._login_process = process
        client._login_status = client_module.LOGIN_RUNNING
        client.data_manager = MagicMock()
        original_api = object()
        client.api = original_api

        with self.assertRaises(client_module.MiHomeClientError):
            await client.logout()

        self.assertIs(client._login_process, process)
        self.assertEqual(client._login_status, client_module.LOGIN_RUNNING)
        self.assertIs(client.api, original_api)
        client.data_manager.clear_auth_file.assert_not_called()

    async def test_old_login_generation_cannot_overwrite_new_login_state(self):
        old_process = types.SimpleNamespace(returncode=-9)
        new_process = types.SimpleNamespace(returncode=None)
        client = object.__new__(client_module.MiHomeClient)
        client._api_lock = asyncio.Lock()
        client._login_generation = 2
        client._login_process = new_process
        client._login_status = client_module.LOGIN_RUNNING

        async with client._api_lock:
            client._finalize_login_generation_locked(
                old_process,
                generation=1,
            )

        self.assertIs(client._login_process, new_process)
        self.assertEqual(
            client._login_status,
            client_module.LOGIN_RUNNING,
        )

    async def test_callback_error_stops_worker_and_cleans_new_auth(self):
        class FakeStdout:
            def __init__(self):
                login_url = (
                    "https://ak.account.xiaomi.com/longPolling/login?"
                    "opaque=direct-login"
                )
                encoded = base64.urlsafe_b64encode(
                    login_url.encode("utf-8")
                ).decode("ascii")
                payload = (
                    f"{client_module.WORKER_QR_PAYLOAD_START}{encoded}"
                    f"{client_module.WORKER_QR_PAYLOAD_END}"
                ).encode("ascii")
                split_at = len(payload) // 2
                self.chunks = [
                    payload[:split_at],
                    payload[split_at:],
                    b"",
                ]

            async def read(self, _size):
                return self.chunks.pop(0) if self.chunks else b""

        class FakeProcess:
            def __init__(self):
                self.returncode = None
                self.stdout = FakeStdout()
                self.finished = asyncio.Event()
                self.kill_count = 0

            def kill(self):
                self.kill_count += 1
                self.returncode = -9
                self.finished.set()

            async def wait(self):
                await self.finished.wait()
                return self.returncode

        auth_present = {"value": False}
        data_manager = MagicMock()
        data_manager.auth_storage_is_secure.return_value = True
        data_manager.auth_exists.side_effect = (
            lambda: auth_present["value"]
        )

        def clear_auth():
            auth_present["value"] = False
            return True

        data_manager.clear_auth_file.side_effect = clear_auth
        data_manager.get_auth_path.return_value = "auth.json"
        process = FakeProcess()
        client = object.__new__(client_module.MiHomeClient)
        client.data_manager = data_manager
        client._api_lock = asyncio.Lock()
        client._login_generation = 0
        client._login_process = None
        client._login_status = client_module.LOGIN_IDLE
        client._worker_script = "_login_worker.py"

        class RaisingAsyncCallback:
            def __call__(self, _url):
                async def fail():
                    auth_present["value"] = True
                    raise RuntimeError("二维码渲染失败")

                return fail()

        with patch.object(
            client_module.asyncio,
            "create_subprocess_exec",
            AsyncMock(return_value=process),
        ):
            result = await client.login(RaisingAsyncCallback())

        self.assertEqual(result["status"], "error")
        self.assertEqual(process.kill_count, 1)
        self.assertFalse(auth_present["value"])
        data_manager.clear_auth_file.assert_called_once_with()
        self.assertIsNone(client._login_process)
        self.assertEqual(client._login_status, client_module.LOGIN_IDLE)

    async def test_login_reports_state_persistence_failure(self):
        class EmptyStdout:
            async def read(self, _size):
                return b""

        class CompleteProcess:
            returncode = 0
            stdout = EmptyStdout()

            async def wait(self):
                return 0

        client = object.__new__(client_module.MiHomeClient)
        client.data_manager = MagicMock()
        client.data_manager.auth_storage_is_secure.return_value = True
        client.data_manager.auth_exists.side_effect = [
            False,
            True,
            True,
            True,
        ]
        client.data_manager.get_auth_path.return_value = "auth.json"
        client.data_manager.harden_auth_file.return_value = True
        client.data_manager.update_state.return_value = False
        client.data_manager.load_state.return_value = {
            "last_login_error": "鉴权失效",
        }
        client.data_manager.compare_and_update_state.return_value = (
            "failed",
            {"last_login_error": "鉴权失效"},
        )
        client._api_lock = asyncio.Lock()
        client._login_generation = 0
        client._login_process = None
        client._login_status = client_module.LOGIN_IDLE
        client._worker_script = "_login_worker.py"
        client._initialize_api = MagicMock()

        with patch.object(
            client_module.asyncio,
            "create_subprocess_exec",
            AsyncMock(return_value=CompleteProcess()),
        ):
            result = await client.login(MagicMock())

        self.assertEqual(result["status"], "error")
        self.assertIn("插件状态记录保存失败", result["message"])
        client._initialize_api.assert_called_once_with()
        pending_status = await client.get_login_status(
            {"last_login_error": "鉴权失效"},
        )
        self.assertIn(
            "插件状态记录保存失败",
            pending_status["credential_storage_error"],
        )
        recovered_state = {
            "last_login_at": client._pending_login_state["last_login_at"],
            "last_login_error": "",
        }
        client.data_manager.compare_and_update_state.return_value = (
            "saved",
            recovered_state,
        )
        recovered_status = await client.get_login_status(
            {"last_login_error": "鉴权失效"},
        )
        self.assertTrue(recovered_status["login_state_recovered"])
        self.assertEqual(recovered_status["credential_storage_error"], "")

        client._pending_login_state = {
            "last_login_at": "2026-07-24 12:00:00",
            "last_login_error": "",
        }
        client._pending_login_state_baseline = {
            "last_login_error": "鉴权失效",
        }
        replaced_status = await client.get_login_status(
            {"last_login_error": "网络异常: ConnectTimeout"},
        )
        self.assertEqual(replaced_status["credential_storage_error"], "")
        self.assertEqual(
            replaced_status["last_login_error"],
            "网络异常: ConnectTimeout",
        )

    async def test_login_timeout_cleans_residual_new_auth(self):
        class EmptyStdout:
            async def read(self, _size):
                return b""

        class FakeProcess:
            def __init__(self):
                self.returncode = None
                self.stdout = EmptyStdout()
                self.finished = asyncio.Event()
                self.kill_count = 0

            def kill(self):
                self.kill_count += 1
                self.returncode = -9
                self.finished.set()

            async def wait(self):
                await self.finished.wait()
                return self.returncode

        auth_present = {"value": False}
        data_manager = MagicMock()
        data_manager.auth_storage_is_secure.return_value = True
        data_manager.auth_exists.side_effect = (
            lambda: auth_present["value"]
        )

        def clear_auth():
            auth_present["value"] = False
            return True

        data_manager.clear_auth_file.side_effect = clear_auth
        data_manager.get_auth_path.return_value = "auth.json"
        process = FakeProcess()
        client = object.__new__(client_module.MiHomeClient)
        client.data_manager = data_manager
        client._api_lock = asyncio.Lock()
        client._login_generation = 0
        client._login_process = None
        client._login_status = client_module.LOGIN_IDLE
        client._worker_script = "_login_worker.py"
        original_wait_for = asyncio.wait_for

        async def force_login_timeout(awaitable, timeout):
            if timeout == 120.0:
                auth_present["value"] = True
                awaitable.cancel()
                try:
                    await awaitable
                except asyncio.CancelledError:
                    pass
                raise asyncio.TimeoutError
            return await original_wait_for(awaitable, timeout=timeout)

        with (
            patch.object(
                client_module.asyncio,
                "create_subprocess_exec",
                AsyncMock(return_value=process),
            ),
            patch.object(
                client_module.asyncio,
                "wait_for",
                side_effect=force_login_timeout,
            ),
        ):
            result = await client.login(MagicMock())

        self.assertEqual(result["status"], "qrcode_not_found")
        self.assertEqual(process.kill_count, 1)
        self.assertFalse(auth_present["value"])
        data_manager.clear_auth_file.assert_called_once_with()
        self.assertIsNone(client._login_process)
        self.assertEqual(client._login_status, client_module.LOGIN_IDLE)

    def test_failed_login_cleanup_preserves_preexisting_auth(self):
        client = object.__new__(client_module.MiHomeClient)
        client.data_manager = MagicMock()
        client.data_manager.auth_exists.return_value = True

        self.assertTrue(client._cleanup_new_login_auth(True))
        client.data_manager.clear_auth_file.assert_not_called()

    async def test_logout_clears_old_api_before_reinitialization_failure(self):
        client = object.__new__(client_module.MiHomeClient)
        client._api_lock = asyncio.Lock()
        client._login_process = None
        client._login_status = client_module.LOGIN_IDLE
        client.data_manager = MagicMock()
        client.data_manager.clear_auth_file.return_value = True
        client.data_manager.auth_storage_is_secure.return_value = False
        session = MagicMock()
        client.api = types.SimpleNamespace(session=session)

        with self.assertRaises(client_module.MiHomeClientError):
            await client.logout()

        self.assertIsNone(client.api)
        session.close.assert_called_once_with()

    async def test_corrupt_auth_keeps_client_recoverable_via_logout(self):
        data_manager = MagicMock()
        data_manager.get_auth_path.return_value = "auth.json"
        data_manager.clear_auth_file.return_value = True
        recovered_api = types.SimpleNamespace()
        original_factory = client_module.mijiaAPI
        client_module.mijiaAPI = MagicMock(
            side_effect=[
                json.JSONDecodeError("invalid", "", 0),
                recovered_api,
            ]
        )
        try:
            client = client_module.MiHomeClient(data_manager)
            self.assertIsNone(client.api)
            data_manager.clear_auth_file.assert_not_called()

            self.assertTrue(await client.logout())
            self.assertIs(client.api, recovered_api)
            data_manager.clear_auth_file.assert_called_once_with()
            reset_payload = data_manager.update_state.call_args_list[-1].kwargs
            self.assertEqual(reset_payload["scenes"], [])
            self.assertEqual(reset_payload["did_to_name"], {})
            self.assertEqual(reset_payload["did_to_model"], {})
        finally:
            client_module.mijiaAPI = original_factory

    async def test_insecure_auth_storage_blocks_api_initialization(self):
        data_manager = MagicMock()
        data_manager.auth_storage_is_secure.return_value = False
        original_factory = client_module.mijiaAPI
        client_module.mijiaAPI = MagicMock()
        try:
            client = client_module.MiHomeClient(data_manager)
            self.assertIsNone(client.api)
            client_module.mijiaAPI.assert_not_called()
            data_manager.update_state.assert_called_once()
        finally:
            client_module.mijiaAPI = original_factory

    async def test_slow_sync_call_keeps_serial_order_until_thread_finishes(self):
        client = object.__new__(client_module.MiHomeClient)
        lock = asyncio.Lock()
        started = threading.Event()
        release = threading.Event()
        order = []

        def first_call():
            started.set()
            release.wait(timeout=1)
            return "first"

        def second_call():
            return "second"

        async def guarded(label, func):
            async with lock:
                order.append(f"{label}:start")
                result = await client._run_sync_call(
                    func,
                    warn_after=0.01,
                    operation=label,
                )
                order.append(f"{label}:done")
                return result

        first = asyncio.create_task(guarded("first", first_call))
        self.assertTrue(await asyncio.to_thread(started.wait, 0.5))
        second = asyncio.create_task(guarded("second", second_call))
        await asyncio.sleep(0.03)

        self.assertFalse(first.done())
        self.assertEqual(order, ["first:start"])

        release.set()
        self.assertEqual(await first, "first")
        self.assertEqual(await second, "second")
        self.assertEqual(
            order,
            ["first:start", "first:done", "second:start", "second:done"],
        )

    async def test_device_status_uses_one_batch_request(self):
        client = object.__new__(client_module.MiHomeClient)
        client._login_status = client_module.LOGIN_IDLE
        client._api_lock = asyncio.Lock()
        client.data_manager = MagicMock()
        client.api = types.SimpleNamespace(
            get_devices_prop=MagicMock(
                return_value=[
                    {
                        "did": "did",
                        "siid": 2,
                        "piid": 1,
                        "code": 0,
                        "value": 26.123,
                    },
                    {
                        "did": "did",
                        "siid": 3,
                        "piid": 1,
                        "code": -1,
                    },
                ]
            )
        )
        temperature = types.SimpleNamespace(
            rw="r",
            method={"siid": 2, "piid": 1},
            unit="celsius",
        )
        humidity = types.SimpleNamespace(
            rw="r",
            method={"siid": 3, "piid": 1},
            unit="percentage",
        )
        device = types.SimpleNamespace(
            prop_list={
                "temperature": temperature,
                "relative-humidity": humidity,
            }
        )
        client._prepare_device_sync = MagicMock(return_value=device)

        result = await client.get_device_props(
            "did",
            ["temperature", "relative_humidity", "unknown"],
        )

        client.api.get_devices_prop.assert_called_once()
        request = client.api.get_devices_prop.call_args.args[0]
        self.assertEqual(len(request), 2)
        self.assertEqual(result["readable"]["temperature"], "26.12°C")
        self.assertIn("relative_humidity", result["readable_keys"])
        self.assertIn("unknown", result["readable_keys"])

    async def test_batch_status_propagates_auth_failure(self):
        client = object.__new__(client_module.MiHomeClient)
        client._login_status = client_module.LOGIN_IDLE
        client._api_lock = asyncio.Lock()
        client.data_manager = MagicMock()
        client.api = types.SimpleNamespace(
            get_devices_prop=MagicMock(
                side_effect=client_module.LoginError("expired")
            )
        )
        prop = types.SimpleNamespace(
            rw="r",
            method={"siid": 2, "piid": 1},
            unit="celsius",
        )
        client._prepare_device_sync = MagicMock(
            return_value=types.SimpleNamespace(
                prop_list={"temperature": prop}
            )
        )

        result = await client.get_device_props("did", ["temperature"])

        self.assertEqual(result, {"__error__": "鉴权失效"})
        self.assertNotIn("temperature", result.get("readable_keys", []))

    async def test_dynamic_action_keeps_raw_key_for_execution(self):
        client = object.__new__(client_module.MiHomeClient)
        client._login_status = client_module.LOGIN_IDLE
        client._api_lock = asyncio.Lock()
        client.data_manager = MagicMock()
        client.data_manager.auth_exists.return_value = True
        client.data_manager.update_state.return_value = True
        action = types.SimpleNamespace(
            method={"siid": 2, "aiid": 1},
        )
        device = types.SimpleNamespace(
            prop_list={},
            action_list={"start-clean": action},
        )
        client._prepare_device_sync = MagicMock(return_value=device)
        client.api = types.SimpleNamespace(
            run_action=MagicMock(return_value={"code": 0}),
        )

        capabilities = await client.get_device_capabilities("did")
        raw_action = capabilities["actions"][0]
        confirmed = await client.run_action("did", raw_action, "清洁设备")

        self.assertEqual(raw_action, "start-clean")
        self.assertTrue(confirmed)
        self.assertEqual(
            client.api.run_action.call_args.args[0]["aiid"],
            1,
        )

    async def test_key_error_from_upstream_becomes_clear_diagnostic(self):
        # 上游对部分型号/接口会返回缺少 key 的响应并抛出 KeyError；
        # 插件不应把“接口异常:KeyError”直接暴露给用户。
        client = object.__new__(client_module.MiHomeClient)
        client._login_status = client_module.LOGIN_IDLE
        client._api_lock = asyncio.Lock()
        client.data_manager = MagicMock()
        client.data_manager.auth_exists.return_value = True
        client.api = types.SimpleNamespace()
        client._prepare_device_sync = MagicMock(side_effect=KeyError("code"))

        capabilities = await client.get_device_capabilities("did")
        status = await client.get_device_props("did", ["temperature"])

        expected = {
            "__error__": "云端响应结构异常（KeyError），接口返回不完整，请稍后重试"
        }
        self.assertEqual(capabilities, expected)
        self.assertEqual(status, expected)

    async def test_device_not_found_becomes_clear_diagnostic(self):
        client = object.__new__(client_module.MiHomeClient)
        client._login_status = client_module.LOGIN_IDLE
        client._api_lock = asyncio.Lock()
        client.data_manager = MagicMock()
        client.data_manager.auth_exists.return_value = True
        client.api = types.SimpleNamespace()
        client._prepare_device_sync = MagicMock(
            side_effect=client_module.DeviceNotFoundError("gone")
        )

        capabilities = await client.get_device_capabilities("did")
        status = await client.get_device_props("did", ["temperature"])

        expected = {
            "__error__": (
                "设备未在米家云端设备列表中找到，请先在米家管理中"
                "重新同步设备并核对别名与 DID 配置"
            )
        }
        self.assertEqual(capabilities, expected)
        self.assertEqual(status, expected)

    async def test_shared_device_capabilities_use_shared_directory_fallback(self):
        client = object.__new__(client_module.MiHomeClient)
        client._login_status = client_module.LOGIN_IDLE
        client._api_lock = asyncio.Lock()
        client.data_manager = MagicMock()
        client.data_manager.auth_exists.return_value = True
        client.api = types.SimpleNamespace(
            get_devices_list=MagicMock(return_value=[]),
            get_shared_devices_list=MagicMock(
                return_value=[
                    {
                        "did": "shared-did",
                        "name": "共享空气净化器",
                        "model": "example.shared.airp",
                    }
                ]
            ),
        )
        client._load_or_fetch_device_spec = MagicMock(
            return_value={
                "name": "共享设备",
                "properties": [
                    {
                        "name": "on",
                        "description": "开关",
                        "type": "bool",
                        "rw": "rw",
                        "range": None,
                        "value-list": None,
                        "method": {"siid": 2, "piid": 1},
                    }
                ],
                "actions": [],
            }
        )

        capabilities = await client.get_device_capabilities("shared-did")

        self.assertNotIn("__error__", capabilities)
        self.assertIn("on", capabilities["writable"])
        client.api.get_devices_list.assert_called_once_with()
        client.api.get_shared_devices_list.assert_called_once_with()
        client._load_or_fetch_device_spec.assert_called_once_with(
            "example.shared.airp"
        )

    async def test_read_timeout_has_stable_capability_and_status_diagnostic(self):
        client = object.__new__(client_module.MiHomeClient)
        client._login_status = client_module.LOGIN_IDLE
        client._api_lock = asyncio.Lock()
        client.data_manager = MagicMock()
        client.data_manager.auth_exists.return_value = True
        client.api = types.SimpleNamespace()
        client._prepare_device_sync = MagicMock(
            side_effect=client_module.RequestsTimeout("secret upstream detail")
        )

        capability = await client.get_device_capabilities("did")
        status = await client.get_device_props("did", ["temperature"])

        expected = {"__error__": "请求超时 (设备离线或深度休眠)"}
        self.assertEqual(capability, expected)
        self.assertEqual(status, expected)

    async def test_sync_error_does_not_expose_upstream_secret_text(self):
        secret = "https://example.invalid/?serviceToken=secret-value"

        def fail_sync():
            raise client_module.RequestException(secret)

        client = object.__new__(client_module.MiHomeClient)
        client._login_status = client_module.LOGIN_IDLE
        client._api_lock = asyncio.Lock()
        client.data_manager = MagicMock()
        client.data_manager.update_state.return_value = True
        client.api = types.SimpleNamespace(get_devices_list=fail_sync)

        with self.assertRaises(client_module.MiHomeClientError) as raised:
            await client.get_devices()

        self.assertEqual(str(raised.exception), "network_error")
        self.assertNotIn("secret-value", str(raised.exception))

    async def test_business_request_requires_saved_credential(self):
        client = object.__new__(client_module.MiHomeClient)
        client._login_status = client_module.LOGIN_IDLE
        client._api_lock = asyncio.Lock()
        client.data_manager = MagicMock()
        client.data_manager.auth_exists.return_value = False
        get_devices_list = MagicMock()
        client.api = types.SimpleNamespace(get_devices_list=get_devices_list)

        with self.assertRaises(client_module.MiHomeAuthError) as raised:
            await client.get_devices()

        self.assertEqual(str(raised.exception), "login_required")
        get_devices_list.assert_not_called()

    async def test_invalid_json_has_stable_diagnostic(self):
        client = object.__new__(client_module.MiHomeClient)
        client._login_status = client_module.LOGIN_IDLE
        client._api_lock = asyncio.Lock()
        client.data_manager = MagicMock()
        client.api = types.SimpleNamespace()
        client._prepare_device_sync = MagicMock(
            side_effect=json.JSONDecodeError("invalid", "", 0)
        )

        capability = await client.get_device_capabilities("did")
        status = await client.get_device_props("did", ["temperature"])

        self.assertIn("没有返回有效数据", capability["__error__"])
        self.assertIn("没有返回有效数据", status["__error__"])

    def test_invalid_json_maps_to_stable_control_and_scene_codes(self):
        client = object.__new__(client_module.MiHomeClient)
        client.data_manager = MagicMock()
        error = json.JSONDecodeError("invalid", "", 0)

        with self.assertRaises(client_module.MiHomeControlError) as control:
            client._handle_control_exception(error, "空调")
        with self.assertRaises(client_module.MiHomeSceneError) as scene:
            client._handle_scene_exception(error, "回家")

        self.assertEqual(str(control.exception), "cloud_no_response")
        self.assertEqual(str(scene.exception), "cloud_no_response")

    def test_cloud_no_response_has_friendly_tool_error(self):
        message = control_module.MiHomeControlTools._format_client_error(
            client_module.MiHomeControlError("cloud_no_response")
        )
        self.assertIn("没有返回有效数据", message)

    def test_local_control_errors_are_not_mislabeled_as_device_rejection(self):
        cases = {
            "invalid_value_or_capability": "属性值",
            "action_schema_missing:play": "动作规格",
            "internal_error": "内部错误",
        }
        for reason, expected in cases.items():
            with self.subTest(reason=reason):
                message = (
                    control_module.MiHomeControlTools._format_client_error(
                        client_module.MiHomeControlError(reason)
                    )
                )
                self.assertIn(expected, message)
                self.assertNotIn("设备拒绝", message)


class AstrBotToolSchemaTests(unittest.TestCase):
    def test_chat_action_paths_share_cross_device_deny_guard(self):
        for action in ("execute-text-directive", "tv-switchon"):
            with self.subTest(action=action):
                self.assertTrue(
                    control_module.is_denied_device_action(action)
                )

        tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
        plugin = next(
            item
            for item in tree.body
            if isinstance(item, ast.ClassDef)
            and item.name == "MiHomeControlPlugin"
        )
        control = next(
            item
            for item in plugin.body
            if isinstance(item, ast.AsyncFunctionDef)
            and item.name == "control_mihome_device"
        )

        action_map_assignment = next(
            item
            for item in ast.walk(control)
            if isinstance(item, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "action_map"
                for target in item.targets
            )
        )
        self.assertIsInstance(action_map_assignment.value, ast.DictComp)
        self.assertTrue(
            any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "is_denied_device_action"
                for generator in action_map_assignment.value.generators
                for condition in generator.ifs
                for node in ast.walk(condition)
            ),
            "静态 action_map 必须通过统一 predicate 过滤",
        )

        capability_filter = [
            node
            for node in ast.walk(control)
            if isinstance(node, ast.If)
            and any(
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "is_denied_device_action"
                and call.args
                and isinstance(call.args[0], ast.Name)
                and call.args[0].id == "act"
                for call in ast.walk(node.test)
            )
            and any(isinstance(item, ast.Continue) for item in node.body)
        ]
        self.assertTrue(
            capability_filter,
            "动态 capability 动作必须通过统一 predicate 过滤",
        )

        run_action_calls = [
            node
            for node in ast.walk(control)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run_action"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Name)
        ]
        self.assertEqual(len(run_action_calls), 2)
        deny_guards = [
            node
            for node in ast.walk(control)
            if isinstance(node, ast.If)
            and isinstance(node.test, ast.Call)
            and isinstance(node.test.func, ast.Name)
            and node.test.func.id == "is_denied_device_action"
            and node.test.args
            and isinstance(node.test.args[0], ast.Name)
            and any(isinstance(item, ast.Return) for item in node.body)
        ]
        for call in run_action_calls:
            action_name = call.args[1].id
            self.assertTrue(
                any(
                    guard.test.args[0].id == action_name
                    and guard.end_lineno < call.lineno
                    and call.lineno - guard.end_lineno <= 4
                    for guard in deny_guards
                ),
                f"run_action({action_name}) 前缺少统一拒绝保护",
            )

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
