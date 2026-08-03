# -*- coding: utf-8 -*-
import importlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]


def _load_worker():
    """移除其他测试的轻量 stub，确保使用锁定的真实 mijiaAPI。"""

    for name in list(sys.modules):
        if name == "mijiaAPI" or name.startswith("mijiaAPI."):
            sys.modules.pop(name, None)
    importlib.invalidate_caches()
    spec = importlib.util.spec_from_file_location(
        "_mihome_spec_worker_test",
        ROOT / "_spec_worker.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


worker = _load_worker()


def _spec_page(i18n, model="yeelink.light.mono5"):
    content = {
        "props": {
            "product": {"name": "测试灯", "model": model},
            "i18n": i18n,
            "tree": {
                "services": [
                    {
                        "iid": 2,
                        "type": "light",
                        "properties": [
                            {
                                "iid": 1,
                                "type": "on",
                                "description": "Switch Status",
                                "format": "bool",
                                "access": ["read", "write", "notify"],
                            },
                            {
                                "iid": 2,
                                "type": "brightness",
                                "description": "Brightness",
                                "format": "uint8",
                                "access": ["read", "write", "notify"],
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
    payload = json.dumps(content, ensure_ascii=False)
    return (
        '<html><script data-page="app" type="application/json">'
        f"{payload}</script></html>"
    )


class SpecWorkerTests(unittest.TestCase):
    def test_missing_chinese_table_falls_back_to_english(self):
        page = _spec_page(
            {
                "en": {
                    "service:002:property:001": "Power",
                    "service:002:property:002": "Brightness",
                }
            }
        )

        patched = worker._patch_i18n_fallback(
            page,
            "yeelink.light.mono5",
        )
        content = json.loads(worker.APP_JSON_PATTERN.search(patched).group(1))

        self.assertEqual(
            content["props"]["i18n"]["zh_cn"]["service:002:property:001"],
            "Power",
        )

    def test_existing_chinese_values_override_english_fallback(self):
        page = _spec_page(
            {
                "en": {
                    "service:002:property:001": "Power",
                    "service:002:property:002": "Brightness",
                },
                "zh_cn": {
                    "service:002:property:001": "开关",
                },
            }
        )

        patched = worker._patch_i18n_fallback(
            page,
            "yeelink.light.mono5",
        )
        content = json.loads(worker.APP_JSON_PATTERN.search(patched).group(1))
        chinese = content["props"]["i18n"]["zh_cn"]

        self.assertEqual(chinese["service:002:property:001"], "开关")
        self.assertEqual(
            chinese["service:002:property:002"],
            "Brightness",
        )

    def test_returned_model_must_match_requested_model(self):
        with self.assertRaisesRegex(ValueError, "型号不匹配"):
            worker._patch_i18n_fallback(
                _spec_page({}, model="other.light.v1"),
                "yeelink.light.mono5",
            )

    def test_pinned_upstream_parser_builds_and_persists_schema(self):
        page = _spec_page(
            {
                "en": {
                    "service:002:property:001": "Power",
                    "service:002:property:002": "Brightness",
                }
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)

            result = worker._parse_with_pinned_upstream(
                "yeelink.light.mono5",
                cache_dir,
                page,
            )

            self.assertEqual(result["model"], "yeelink.light.mono5")
            self.assertEqual(len(result["properties"]), 2)
            self.assertEqual(result["properties"][0]["name"], "on")
            self.assertEqual(result["properties"][0]["rw"], "rw")
            self.assertEqual(result["properties"][1]["type"], "uint")
            self.assertEqual(len(result["actions"]), 1)
            persisted = json.loads(
                (cache_dir / "yeelink.light.mono5.json").read_text(encoding="utf-8")
            )
            self.assertEqual(persisted, result)

    def test_compat_fetch_is_passed_to_pinned_parser(self):
        page = _spec_page({"en": {}})
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(
                worker,
                "_fetch_spec_page",
                AsyncMock(return_value=page),
            ) as fetch:
                result = worker.get_device_info_compat(
                    "yeelink.light.mono5",
                    Path(temp_dir),
                )

        fetch.assert_awaited_once_with("yeelink.light.mono5")
        self.assertEqual(result["model"], "yeelink.light.mono5")


if __name__ == "__main__":
    unittest.main()
