# -*- coding: utf-8 -*-
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def _load_worker():
    spec = importlib.util.spec_from_file_location(
        "_mihome_spec_worker_test",
        ROOT / "_spec_worker.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _page(i18n):
    content = {
        "props": {
            "product": {"name": "Yeelight LED 灯丝灯", "model": "yeelink.light.mono5"},
            "i18n": i18n,
            "tree": {
                "services": [
                    {
                        "iid": 2,
                        "type": "urn:miot-spec-v2:service:light:00007802:yeelink",
                        "properties": [
                            {
                                "iid": 1,
                                "type": "on",
                                "description": "Switch Status",
                                "format": "bool",
                                "access": ["read", "write"],
                                "valueRange": None,
                            },
                            {
                                "iid": 2,
                                "type": "brightness",
                                "description": "Brightness",
                                "format": "uint8",
                                "access": ["read", "write"],
                                "valueRange": [1, 100, 1],
                            },
                        ],
                        "actions": [
                            {
                                "iid": 1,
                                "type": "toggle",
                                "description": "Toggle",
                            }
                        ],
                    }
                ]
            },
        }
    }
    return (
        '<!doctype html><html><head></head><body>'
        '<script data-page="app" type="application/json">'
        + json.dumps(content)
        + "</script></body></html>"
    )


class SpecWorkerI18nFallbackTests(unittest.TestCase):
    def setUp(self):
        self.worker = _load_worker()

    def _fetch(self, page):
        fake_response = types.SimpleNamespace(status_code=200, text=page)
        with patch.object(
            self.worker.requests,
            "get",
            return_value=fake_response,
        ) as mocked_get:
            spec = self.worker.fetch_device_spec("yeelink.light.mono5")
        self.assertEqual(
            mocked_get.call_args.args[0],
            "https://home.miot-spec.com/spec/yeelink.light.mono5",
        )
        return spec

    def test_en_only_i18n_is_tolerated(self):
        # issue #17：yeelink.light.mono5 的 i18n 只有 en，上游 get_device_info
        # 硬取 zh_cn 抛 KeyError。worker 应回退到 en 并正常解析。
        i18n = {
            "en": {
                "service:002:property:001": "Switch Status",
                "service:002:property:002": "Brightness",
                "service:002:action:001": "Toggle",
            }
        }
        spec = self._fetch(_page(i18n))

        self.assertEqual(spec["model"], "yeelink.light.mono5")
        self.assertEqual(len(spec["properties"]), 2)
        self.assertEqual(spec["properties"][0]["name"], "on")
        self.assertEqual(spec["properties"][0]["rw"], "rw")
        self.assertEqual(spec["properties"][0]["method"], {"siid": 2, "piid": 1})
        self.assertIn("Switch Status", spec["properties"][0]["description"])
        self.assertEqual(spec["properties"][1]["type"], "uint")
        self.assertEqual(spec["properties"][1]["range"], [1, 100, 1])
        self.assertEqual(len(spec["actions"]), 1)
        self.assertEqual(spec["actions"][0]["name"], "toggle")
        self.assertEqual(spec["actions"][0]["method"], {"siid": 2, "aiid": 1})

    def test_missing_i18n_is_tolerated(self):
        spec = self._fetch(_page({}))
        self.assertEqual(len(spec["properties"]), 2)
        # 无 zh/en 时保留原始英文描述，不附加分隔符
        self.assertEqual(spec["properties"][0]["description"], "Switch Status")

    def test_zh_cn_is_preferred_over_en(self):
        i18n = {
            "zh_cn": {
                "service:002:property:001": "开关状态",
            },
            "en": {
                "service:002:property:001": "Switch Status",
            },
        }
        spec = self._fetch(_page(i18n))
        self.assertIn("开关状态", spec["properties"][0]["description"])

    def test_http_error_raises_value_error(self):
        fake_response = types.SimpleNamespace(status_code=404, text="not found")
        with patch.object(
            self.worker.requests,
            "get",
            return_value=fake_response,
        ):
            with self.assertRaises(ValueError):
                self.worker.fetch_device_spec("yeelink.light.mono5")

    def test_main_writes_validated_cache_file(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            with patch.object(
                self.worker.requests,
                "get",
                return_value=types.SimpleNamespace(
                    status_code=200,
                    text=_page({"en": {}}),
                ),
            ):
                with patch.object(
                    sys,
                    "argv",
                    [
                        "_spec_worker.py",
                        "yeelink.light.mono5",
                        temp_dir,
                    ],
                ):
                    self.worker.main()

            cache_path = cache_dir / "yeelink.light.mono5.json"
            self.assertTrue(cache_path.is_file())
            persisted = json.loads(cache_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["model"], "yeelink.light.mono5")
            self.assertEqual(len(persisted["properties"]), 2)


if __name__ == "__main__":
    unittest.main()
