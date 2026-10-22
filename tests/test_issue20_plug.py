"""Issue #20: use the reporter's public spec across control and status paths."""

import __future__
import asyncio
import ast
import json
import re
import shlex
import types
import unittest
from datetime import datetime
from unittest.mock import MagicMock

from test_control_tools import (
    ROOT, _Event, _Plugin, client_module, control_module, profiles,
)


SPEC_PATH = ROOT / "tests" / "fixtures" / "cuco_plug_v3_spec.json"


def load_plugin_class():
    """Run production methods without requiring an AstrBot server or decorators."""
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    plugin_class = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    plugin_class.decorator_list = []
    plugin_class.bases = []
    for node in plugin_class.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            node.decorator_list = []
    readonly_categories = next(
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "READONLY_ALLOWED_CATEGORIES"
                for target in node.targets)
    )
    namespace = {
        **vars(profiles), **vars(client_module),
        "json": json, "re": re, "shlex": shlex, "datetime": datetime,
        "is_denied_device_action": control_module.is_denied_device_action,
    }
    exec(compile(
        ast.Module(body=[readonly_categories, plugin_class], type_ignores=[]),
        str(ROOT / "main.py"), "exec", flags=__future__.annotations.compiler_flag,
    ), namespace)
    return namespace["MiHomeControlPlugin"]


class PlugIntegrationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
        cls.plugin_class = load_plugin_class()

    def build_client(self, *, energy=123, power=18.75, failed_energy=False):
        state = {"did_to_model": {"test-plug": "cuco.plug.v3"}}
        client = object.__new__(client_module.MiHomeClient)
        client._login_status = client_module.LOGIN_IDLE
        client._api_lock = asyncio.Lock()
        client.data_manager = types.SimpleNamespace(
            auth_exists=lambda: True,
            load_state=lambda: state,
            update_state=lambda **_values: True,
        )
        values = {
            "on": True, "electric-power": power, "power-consumption": energy,
            "temperature": 32, "fault": 0, "physical-controls-locked": False,
            "indicator-light-on": True, "default-power-on-state": 0,
        }
        response = []
        for prop in self.spec["properties"]:
            if prop["name"] in values:
                response.append({
                    "did": "test-plug", **prop["method"],
                    "code": -1 if failed_energy and prop["name"] == "power-consumption" else 0,
                    "value": values[prop["name"]],
                })
        client.api = types.SimpleNamespace(
            get_devices_list=MagicMock(return_value=[{
                "did": "test-plug", "model": "cuco.plug.v3", "name": "米家智能插座3",
            }]),
            get_devices_prop=MagicMock(return_value=list(reversed(response))),
            set_devices_prop=MagicMock(return_value={"code": 0}),
            run_action=MagicMock(return_value={"code": 0}),
        )
        client._load_or_fetch_device_spec = MagicMock(return_value=self.spec)
        return client

    def build_plugin(self):
        plugin = object.__new__(self.plugin_class)
        plugin.config = {"device_map": '{"书房插座": "test-plug"}'}
        plugin.client = self.build_client()
        plugin.data_manager = plugin.client.data_manager
        plugin.action_alias = {"开": True, "关": False}
        return plugin

    async def test_power_energy_and_status_share_one_batch(self):
        client = self.build_client()
        result = await client.get_device_props(
            "test-plug", profiles.get_device_detail_readable_keys(model="cuco.plug.v3"),
        )
        self.assertEqual(result["readable"]["power_consumption"], "1.23 kWh")
        self.assertEqual(result["readable"]["electric_power"], "18.75 W")
        self.assertEqual(result["readable"]["temperature"], "32°C")
        self.assertEqual(result["readable_keys"], [])
        client.api.get_devices_prop.assert_called_once()

    async def test_zero_and_maximum_energy_are_not_missing(self):
        for raw, expected in ((0, "0 kWh"), (65535, "655.35 kWh")):
            with self.subTest(raw=raw):
                result = await self.build_client(energy=raw, power=0).get_device_props(
                    "test-plug", ["power_consumption", "electric_power"],
                )
                self.assertEqual(result["readable"]["power_consumption"], expected)
                self.assertEqual(result["readable"]["electric_power"], "0 W")

    async def test_invalid_or_rejected_energy_does_not_hide_other_states(self):
        for raw, failed in ((True, False), ("123", False), (float("nan"), False), (123, True)):
            with self.subTest(raw=raw, failed=failed):
                result = await self.build_client(energy=raw, failed_energy=failed).get_device_props(
                    "test-plug", ["power_consumption", "electric_power"],
                )
                self.assertIn("power_consumption", result["readable_keys"])
                self.assertNotIn("power_consumption", result["readable"])
                self.assertEqual(result["readable"]["electric_power"], "18.75 W")

    async def test_conversion_is_scoped_to_exact_model(self):
        client = self.build_client()
        device = client._prepare_device_sync("test-plug")
        device.model = "other.plug.v1"
        result = await client.get_device_props("test-plug", ["power_consumption"])
        self.assertEqual(result["readable"]["power_consumption"], "123")

    async def test_manual_on_off_targets_relay_not_indicator(self):
        plugin = self.build_plugin()
        for command, value in (("开", True), ("关", False)):
            with self.subTest(command=command):
                event = types.SimpleNamespace(
                    message_str=f"/米家控制 书房插座 {command}",
                    plain_result=lambda value: value,
                )
                messages = [message async for message in plugin.control_mihome_device(event)]
                self.assertIn("成功", messages[-1])
                plugin.client.api.set_devices_prop.assert_called_with({
                    "did": "test-plug", "siid": 2, "piid": 1, "value": value,
                })

    async def test_toggle_uses_spec_action(self):
        client = self.build_client()
        self.assertTrue(await client.run_action("test-plug", "toggle"))
        client.api.run_action.assert_called_once_with({
            "did": "test-plug", "siid": 2, "aiid": 1,
        })

    async def test_readonly_renderer_includes_units_and_chinese_states(self):
        plugin = self.build_plugin()
        rendered = await plugin._render_readonly_status_by_alias("书房插座")
        for expected in ("插座类别", "实时功率: 18.75 W", "用电量: 1.23 kWh", "故障状态: 无故障", "插座电源: 开启"):
            self.assertIn(expected, rendered)

    async def test_llm_can_control_exact_model_but_cannot_write_energy(self):
        plugin = _Plugin(model="cuco.plug.v3", category="无类别")
        service = control_module.MiHomeControlTools(plugin)
        listed = json.loads(await service.list_devices(_Event()))
        self.assertTrue(listed["devices"][0]["direct_control_supported"])
        self.assertEqual(listed["devices"][0]["category"], "插座类别")
        result = await service.control_device(_Event(), "客厅空调", [{"prop": "开关", "value": "开"}])
        self.assertEqual(json.loads(result)["summary"]["success"], 1)
        plugin.client.set_property.reset_mock()
        denied = await service.control_device(_Event(), "客厅空调", [{"prop": "power_consumption", "value": 0}])
        self.assertIn("不可写", denied)
        plugin.client.set_property.assert_not_awaited()

    async def test_unadapted_plug_remains_inspection_only(self):
        plugin = _Plugin(model="cuco.plug.other", category="插座类别")
        service = control_module.MiHomeControlTools(plugin)
        result = await service.control_device(_Event(), "客厅空调", [{"prop": "on", "value": True}])
        self.assertIn("未配置受信任", result)
        plugin.client.set_property.assert_not_awaited()
