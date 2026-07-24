import importlib
import inspect
import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MijiaAPI413CompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # unittest discovery 会先导入其他使用桩模块的测试文件；这里重新加载
        # 测试环境真正安装的 mijiaAPI，以验证 requirements 中的公开契约。
        for name in list(sys.modules):
            if name == "mijiaAPI" or name.startswith("mijiaAPI."):
                sys.modules.pop(name, None)
        importlib.invalidate_caches()
        try:
            from mijiaAPI import mijiaAPI, mijiaDevice
            from mijiaAPI.devices import DevProp
            from mijiaAPI.version import version
        except ImportError as exc:
            raise unittest.SkipTest(
                f"当前测试环境未安装 mijiaAPI：{exc}"
            ) from exc
        cls.api_class = mijiaAPI
        cls.device_class = mijiaDevice
        cls.dev_prop_class = DevProp
        cls.version = version

    def test_installed_version_matches_pinned_requirement(self):
        requirement = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        pinned = re.search(r"(?m)^mijiaAPI==([0-9.]+)$", requirement)
        self.assertIsNotNone(pinned)
        self.assertEqual(self.version, pinned.group(1))

    def test_public_signatures_used_by_plugin_are_available(self):
        self.assertIn(
            "home_id",
            inspect.signature(self.api_class.get_devices_list).parameters,
        )
        self.assertIn(
            "home_id",
            inspect.signature(self.api_class.get_scenes_list).parameters,
        )
        scene_parameters = inspect.signature(self.api_class.run_scene).parameters
        self.assertIn("scene_id", scene_parameters)
        self.assertIn("home_id", scene_parameters)
        self.assertIn(
            "value",
            inspect.signature(self.device_class.run_action).parameters,
        )

    def test_business_requests_do_not_start_qr_login(self):
        client_source = (ROOT / "mihome_client.py").read_text(encoding="utf-8")
        self.assertNotIn("self.api.login(", client_source)
        self.assertNotIn('getattr(self.api, "device_list"', client_source)
        self.assertNotRegex(
            client_source,
            r"wait_for\(\s*asyncio\.to_thread",
        )

    def test_current_rw_shape_is_supported(self):
        prop = self.dev_prop_class(
            {
                "name": "on",
                "description": "开关",
                "type": "bool",
                "rw": "rw",
                "range": [],
                "unit": "none",
                "method": {"siid": 2, "piid": 1},
            }
        )
        self.assertEqual(prop.rw, "rw")


if __name__ == "__main__":
    unittest.main()
