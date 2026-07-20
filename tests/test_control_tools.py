# -*- coding: utf-8 -*-
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]

# 注入 astrbot 桩模块，使 main.py 可在没有真实 AstrBot 框架时被导入
_astrbot = types.ModuleType("astrbot")
_astrbot_api = types.ModuleType("astrbot.api")
_astrbot_api.logger = MagicMock()
_astrbot_api.AstrBotConfig = dict
_astrbot_api.event = types.ModuleType("astrbot.api.event")
_astrbot_api.event.filter = MagicMock()
_astrbot_api.event.AstrMessageEvent = MagicMock
_astrbot_api.star = types.ModuleType("astrbot.api.star")


class _StubStar:
    """AstrBot Star 的最小桩实现，仅用于让 MiHomeControlPlugin 有真实父类。"""

    def __init__(self, *args, **kwargs):
        pass


_astrbot_api.star.Context = MagicMock
_astrbot_api.star.Star = _StubStar
_astrbot_api.star.register = lambda *a, **kw: lambda cls: cls
sys.modules["astrbot"] = _astrbot
sys.modules["astrbot.api"] = _astrbot_api
sys.modules["astrbot.api.event"] = _astrbot_api.event
sys.modules["astrbot.api.star"] = _astrbot_api.star

# 注入 mijiaAPI 桩模块，使 mihome_client.py 可在无真实 mijiaAPI 包时被导入
_mijiaapi = types.ModuleType("mijiaAPI")
for _name in (
    "mijiaAPI",
    "mijiaDevice",
    "LoginError",
    "DeviceNotFoundError",
    "DeviceSetError",
    "DeviceGetError",
    "DeviceActionError",
    "APIError",
):
    setattr(_mijiaapi, _name, MagicMock)
sys.modules["mijiaAPI"] = _mijiaapi

# 将项目根目录注册为命名空间包，使 main.py 的相对导入
# （from .data_manager / from .mihome_client / from .device_profiles）生效
_PKG_NAME = ROOT.name
if _PKG_NAME not in sys.modules:
    _pkg = types.ModuleType(_PKG_NAME)
    _pkg.__path__ = [str(ROOT)]
    sys.modules[_PKG_NAME] = _pkg

sys.path.insert(0, str(ROOT))


class AggregateDeviceCapabilitiesTests(unittest.TestCase):
    """测试 _aggregate_device_capabilities 纯函数。

    该函数只依赖 device_profiles.py 的静态字典，不调云端，
    可在无 mijiaAPI 依赖环境下测试。
    """

    def _make_plugin(self):
        # 通过 __new__ 跳过 __init__（避免触发 MiHomeDataManager 初始化）
        from astrbot_plugin_mihome.main import MiHomeControlPlugin
        plugin = MiHomeControlPlugin.__new__(MiHomeControlPlugin)
        plugin.config = {}
        plugin.data_manager = MagicMock()
        plugin.client = MagicMock()
        plugin.action_alias = {}
        return plugin

    def test_returns_writable_props_with_chinese_names_and_valid_values(self):
        plugin = self._make_plugin()
        # lumi.acpartner.mcn02 是 device_profiles.py 中已注册的空调型号
        result = plugin._aggregate_device_capabilities(
            alias="客厅空调",
            did="12345",
            model="lumi.acpartner.mcn02",
            category="",
        )
        self.assertEqual(result["alias"], "客厅空调")
        self.assertEqual(result["model"], "lumi.acpartner.mcn02")
        writable_keys = [p["key"] for p in result["writable_props"]]
        self.assertIn("on", writable_keys)
        self.assertIn("mode", writable_keys)
        self.assertIn("target_temperature", writable_keys)

        mode_prop = next(p for p in result["writable_props"] if p["key"] == "mode")
        self.assertEqual(mode_prop["name"], "模式")
        self.assertIn("制冷", mode_prop["valid_values"])

    def test_falls_back_to_category_when_model_unknown(self):
        plugin = self._make_plugin()
        result = plugin._aggregate_device_capabilities(
            alias="测试空调",
            did="99999",
            model="unknown.model.xyz",
            category="空调类别",
        )
        writable_keys = [p["key"] for p in result["writable_props"]]
        self.assertIn("mode", writable_keys)

    def test_includes_actions_when_defined(self):
        # xiaomi.vacuum.ov21cn 在 device_profiles.py 中有 action_map
        plugin = self._make_plugin()
        result = plugin._aggregate_device_capabilities(
            alias="扫地机",
            did="11111",
            model="xiaomi.vacuum.ov21cn",
            category="",
        )
        self.assertTrue(len(result["actions"]) > 0)
        action_keys = [a["key"] for a in result["actions"]]
        self.assertIn("start_sweep", action_keys)


if __name__ == "__main__":
    unittest.main()
