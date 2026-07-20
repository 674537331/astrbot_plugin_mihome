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


class TranslateControlOperationTests(unittest.TestCase):
    """测试 _translate_control_operation：把 LLM 提供的 prop+value 翻译为云端 key+value。"""

    def _make_plugin(self):
        from astrbot_plugin_mihome.main import MiHomeControlPlugin
        plugin = MiHomeControlPlugin.__new__(MiHomeControlPlugin)
        plugin.config = {}
        plugin.data_manager = MagicMock()
        plugin.client = MagicMock()
        plugin.action_alias = {}
        return plugin

    def test_translates_chinese_prop_and_value(self):
        plugin = self._make_plugin()
        result = plugin._translate_control_operation(
            model="lumi.acpartner.mcn02",
            category="",
            prop="模式",
            value="制冷",
        )
        self.assertEqual(result["prop"], "mode")
        self.assertEqual(result["value"], 1)  # Integer cloud value (matches /米家控制 behavior)
        self.assertTrue(result["ok"])

    def test_passes_through_integer_cloud_value(self):
        """LLM 直接传整数 cloud API 值时应该透传。"""
        plugin = self._make_plugin()
        result = plugin._translate_control_operation(
            model="lumi.acpartner.mcn02",
            category="",
            prop="mode",
            value=1,  # Integer cloud value
        )
        self.assertEqual(result["prop"], "mode")
        self.assertEqual(result["value"], 1)
        self.assertTrue(result["ok"])

    def test_returns_valid_values_hint_when_value_invalid(self):
        plugin = self._make_plugin()
        result = plugin._translate_control_operation(
            model="lumi.acpartner.mcn02",
            category="",
            prop="模式",
            value="送风模式",  # 不在枚举内
        )
        self.assertFalse(result["ok"])
        self.assertIn("valid_values", result)
        self.assertIn("制冷", result["valid_values"])

    def test_returns_valid_props_hint_when_prop_unknown(self):
        plugin = self._make_plugin()
        result = plugin._translate_control_operation(
            model="lumi.acpartner.mcn02",
            category="",
            prop="不存在属性",
            value="任意值",
        )
        self.assertFalse(result["ok"])
        self.assertIn("valid_props", result)
        # 应该列出该设备的可写属性中文名
        prop_names = result["valid_props"]
        self.assertIn("模式", prop_names)

    def test_passes_through_numeric_value_when_help_examples_is_not_exhaustive(self):
        """温度属性的 help_examples 是示例值（如 ["26", "24"]）而非枚举，
        数值应该透传，cloud 端做范围校验。"""
        plugin = self._make_plugin()
        result = plugin._translate_control_operation(
            model="lumi.acpartner.mcn02",
            category="",
            prop="温度",
            value="26",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["prop"], "target_temperature")
        self.assertEqual(result["value"], 26)  # Integer, parsed from string "26"


class FormatControlResultTests(unittest.TestCase):
    """测试 _format_control_result：把执行结果列表格式化为 LLM 友好的多行字符串。"""

    def _make_plugin(self):
        from astrbot_plugin_mihome.main import MiHomeControlPlugin
        plugin = MiHomeControlPlugin.__new__(MiHomeControlPlugin)
        plugin.config = {}
        plugin.data_manager = MagicMock()
        plugin.client = MagicMock()
        plugin.action_alias = {}
        return plugin

    def test_all_success(self):
        plugin = self._make_plugin()
        executed = [
            {"prop": "on", "value": True, "status": "success"},
            {"prop": "mode", "value": "cool", "status": "success"},
        ]
        result = plugin._format_control_result(alias="客厅空调", executed=executed)
        self.assertIn("客厅空调", result)
        self.assertIn("成功", result)
        self.assertIn("on", result)
        self.assertIn("mode", result)

    def test_mixed_success_and_failure(self):
        plugin = self._make_plugin()
        executed = [
            {"prop": "on", "value": True, "status": "success"},
            {
                "prop": "mode",
                "value": "送风模式",
                "status": "failed",
                "error": "无效值",
                "valid_values": ["制冷", "制热"],
            },
        ]
        result = plugin._format_control_result(alias="客厅空调", executed=executed)
        self.assertIn("成功", result)
        self.assertIn("失败", result)
        self.assertIn("制冷", result)
        self.assertIn("制热", result)

    def test_all_failed(self):
        plugin = self._make_plugin()
        executed = [
            {
                "prop": "未知属性",
                "value": "任意",
                "status": "failed",
                "error": "未知属性",
                "valid_props": ["电源", "模式"],
            },
        ]
        result = plugin._format_control_result(alias="测试设备", executed=executed)
        self.assertIn("失败", result)
        self.assertIn("电源", result)
        self.assertIn("模式", result)


class IsIrDeviceTests(unittest.TestCase):
    """测试 _is_ir_device 静态方法。"""

    def _make_plugin(self):
        from astrbot_plugin_mihome.main import MiHomeControlPlugin
        plugin = MiHomeControlPlugin.__new__(MiHomeControlPlugin)
        plugin.config = {}
        plugin.data_manager = MagicMock()
        plugin.client = MagicMock()
        plugin.action_alias = {}
        return plugin

    def test_detects_ir_did_prefix(self):
        plugin = self._make_plugin()
        self.assertTrue(plugin._is_ir_device("ir.2078708785907654658", "anything"))

    def test_detects_miir_model_prefix(self):
        plugin = self._make_plugin()
        self.assertTrue(plugin._is_ir_device("12345", "miir.aircondition.ir02"))

    def test_returns_false_for_native_device(self):
        plugin = self._make_plugin()
        self.assertFalse(plugin._is_ir_device("573207651", "cuco.plug.v3"))

    def test_returns_false_for_empty_inputs(self):
        plugin = self._make_plugin()
        self.assertFalse(plugin._is_ir_device("", ""))


class GuessCategoryFromModelTests(unittest.TestCase):
    """测试 _guess_category_from_model 启发式识别。"""

    def _make_plugin(self):
        from astrbot_plugin_mihome.main import MiHomeControlPlugin
        plugin = MiHomeControlPlugin.__new__(MiHomeControlPlugin)
        plugin.config = {}
        plugin.data_manager = MagicMock()
        plugin.client = MagicMock()
        plugin.action_alias = {}
        return plugin

    def test_detects_smart_plug(self):
        plugin = self._make_plugin()
        self.assertEqual(plugin._guess_category_from_model("cuco.plug.v3"), "开关类别")
        self.assertEqual(plugin._guess_category_from_model("chuangmi.plug.v1"), "开关类别")

    def test_detects_ac_partner(self):
        plugin = self._make_plugin()
        self.assertEqual(plugin._guess_category_from_model("lumi.acpartner.mcn02"), "空调类别")

    def test_detects_vacuum(self):
        plugin = self._make_plugin()
        self.assertEqual(plugin._guess_category_from_model("xiaomi.vacuum.ov21cn"), "扫地机类别")

    def test_returns_empty_for_unknown_model(self):
        plugin = self._make_plugin()
        self.assertEqual(plugin._guess_category_from_model("unknown.brand.xyz"), "")

    def test_returns_empty_for_empty_input(self):
        plugin = self._make_plugin()
        self.assertEqual(plugin._guess_category_from_model(""), "")


class ParseRwFieldTests(unittest.TestCase):
    """测试 MiHomeClient._parse_rw_field 静态方法。"""

    def test_string_rw_returns_both_true(self):
        from astrbot_plugin_mihome.mihome_client import MiHomeClient
        self.assertEqual(MiHomeClient._parse_rw_field("rw"), (True, True))

    def test_string_r_returns_read_only(self):
        from astrbot_plugin_mihome.mihome_client import MiHomeClient
        self.assertEqual(MiHomeClient._parse_rw_field("r"), (True, False))

    def test_string_w_returns_write_only(self):
        from astrbot_plugin_mihome.mihome_client import MiHomeClient
        self.assertEqual(MiHomeClient._parse_rw_field("w"), (False, True))

    def test_uppercase_rw_works(self):
        from astrbot_plugin_mihome.mihome_client import MiHomeClient
        self.assertEqual(MiHomeClient._parse_rw_field("RW"), (True, True))

    def test_list_format_read_write(self):
        from astrbot_plugin_mihome.mihome_client import MiHomeClient
        self.assertEqual(MiHomeClient._parse_rw_field(["read", "write"]), (True, True))

    def test_list_format_read_only(self):
        from astrbot_plugin_mihome.mihome_client import MiHomeClient
        self.assertEqual(MiHomeClient._parse_rw_field(["read"]), (True, False))

    def test_empty_string_returns_false_false(self):
        from astrbot_plugin_mihome.mihome_client import MiHomeClient
        self.assertEqual(MiHomeClient._parse_rw_field(""), (False, False))

    def test_none_returns_false_false(self):
        from astrbot_plugin_mihome.mihome_client import MiHomeClient
        self.assertEqual(MiHomeClient._parse_rw_field(None), (False, False))


class SpeakerCategoryTests(unittest.TestCase):
    """测试音箱类别适配。"""

    def _make_plugin(self):
        from astrbot_plugin_mihome.main import MiHomeControlPlugin
        plugin = MiHomeControlPlugin.__new__(MiHomeControlPlugin)
        plugin.config = {}
        plugin.data_manager = MagicMock()
        plugin.client = MagicMock()
        plugin.action_alias = {}
        return plugin

    def test_speaker_category_constant_exists(self):
        from astrbot_plugin_mihome.device_profiles import CATEGORY_SPEAKER
        self.assertEqual(CATEGORY_SPEAKER, "音箱类别")

    def test_speaker_category_in_valid_categories(self):
        from astrbot_plugin_mihome.device_profiles import VALID_CATEGORIES, CATEGORY_SPEAKER
        self.assertIn(CATEGORY_SPEAKER, VALID_CATEGORIES)

    def test_speaker_model_profile_exists(self):
        from astrbot_plugin_mihome.device_profiles import MODEL_PROFILES
        self.assertIn("xiaomi.wifispeaker.oh2p", MODEL_PROFILES)
        profile = MODEL_PROFILES["xiaomi.wifispeaker.oh2p"]
        self.assertEqual(profile.get("category"), "音箱类别")
        # Verify key props are mapped
        prop_map = profile.get("prop_map", {})
        self.assertIn("音量", prop_map)
        self.assertEqual(prop_map["音量"], "volume")

    def test_speaker_category_profile_has_chinese_action_map(self):
        from astrbot_plugin_mihome.device_profiles import CATEGORY_PROFILES, CATEGORY_SPEAKER
        profile = CATEGORY_PROFILES[CATEGORY_SPEAKER]
        action_map = profile.get("action_map", {})
        # Verify key actions are mapped
        self.assertEqual(action_map.get("播放"), "play")
        self.assertEqual(action_map.get("暂停"), "pause")
        self.assertEqual(action_map.get("播放文本"), "play-text")
        self.assertEqual(action_map.get("播放音乐"), "play-music")

    def test_aggregate_capabilities_for_speaker_model(self):
        """验证 _aggregate_device_capabilities 对音箱型号返回完整中文 schema。"""
        plugin = self._make_plugin()
        result = plugin._aggregate_device_capabilities(
            alias="卧室音箱",
            did="2181495268",
            model="xiaomi.wifispeaker.oh2p",
            category="",
        )
        # Should have writable props with Chinese names
        writable_keys = [p["key"] for p in result["writable_props"]]
        self.assertIn("volume", writable_keys)
        self.assertIn("mute", writable_keys)
        # Verify Chinese name mapping
        volume_prop = next(p for p in result["writable_props"] if p["key"] == "volume")
        self.assertEqual(volume_prop["name"], "音量")
        # Should have actions
        action_keys = [a["key"] for a in result["actions"]]
        self.assertIn("play", action_keys)
        self.assertIn("play-text", action_keys)

    def test_guess_category_detects_speaker_model(self):
        """验证 _guess_category_from_model 识别音箱型号。"""
        plugin = self._make_plugin()
        self.assertEqual(plugin._guess_category_from_model("xiaomi.wifispeaker.oh2p"), "音箱类别")
        self.assertEqual(plugin._guess_category_from_model("xiaomi.wifispeaker.l09a"), "音箱类别")


if __name__ == "__main__":
    unittest.main()
