import asyncio
import ast
import importlib.util
import json
import re
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB_API_PATH = ROOT / "web_api.py"
MAIN_PATH = ROOT / "main.py"
METADATA_PATH = ROOT / "metadata.yaml"
CHANGELOG_PATH = ROOT / "CHANGELOG.md"
PAGE_DIR = ROOT / "pages" / "mihome"


class _Logger:
    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


class _Request:
    query = {}

    async def json(self, default=None):
        return default


def load_web_api_module():
    package_name = "_mihome_webui_test_package"
    package = types.ModuleType(package_name)
    package.__path__ = [str(ROOT)]
    sys.modules[package_name] = package

    profiles_name = f"{package_name}.device_profiles"
    profiles_spec = importlib.util.spec_from_file_location(
        profiles_name,
        ROOT / "device_profiles.py",
    )
    profiles = importlib.util.module_from_spec(profiles_spec)
    sys.modules[profiles_name] = profiles
    profiles_spec.loader.exec_module(profiles)

    client_module = types.ModuleType(f"{package_name}.mihome_client")

    class MiHomeAuthError(Exception):
        pass

    class MiHomeClientError(Exception):
        pass

    client_module.MiHomeAuthError = MiHomeAuthError
    client_module.MiHomeClientError = MiHomeClientError
    sys.modules[client_module.__name__] = client_module

    astrbot = types.ModuleType("astrbot")
    astrbot_api = types.ModuleType("astrbot.api")
    astrbot_web = types.ModuleType("astrbot.api.web")
    astrbot_api.logger = _Logger()
    astrbot_web.request = _Request()
    astrbot_web.json_response = lambda payload, status_code=200: {
        "payload": payload,
        "status_code": status_code,
    }
    astrbot.api = astrbot_api
    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = astrbot_api
    sys.modules["astrbot.api.web"] = astrbot_web

    module_name = f"{package_name}.web_api"
    spec = importlib.util.spec_from_file_location(module_name, WEB_API_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def load_mihome_client_module():
    package_name = "_mihome_client_test_package"
    package = types.ModuleType(package_name)
    package.__path__ = [str(ROOT)]
    sys.modules[package_name] = package

    data_module = types.ModuleType(f"{package_name}.data_manager")
    data_module.MiHomeDataManager = type("MiHomeDataManager", (), {})
    sys.modules[data_module.__name__] = data_module

    mijia_module = types.ModuleType("mijiaAPI")
    mijia_module.mijiaAPI = type("mijiaAPI", (), {})
    mijia_module.mijiaDevice = type("mijiaDevice", (), {})
    for name in (
        "LoginError",
        "DeviceNotFoundError",
        "DeviceSetError",
        "DeviceGetError",
        "DeviceActionError",
        "APIError",
    ):
        setattr(mijia_module, name, type(name, (Exception,), {}))
    sys.modules["mijiaAPI"] = mijia_module

    module_name = f"{package_name}.mihome_client"
    spec = importlib.util.spec_from_file_location(
        module_name,
        ROOT / "mihome_client.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class _Config(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.save_count = 0

    def save_config(self):
        self.save_count += 1


class _DataManager:
    def __init__(self, state=None):
        self.state = state or {}

    def load_state(self):
        return dict(self.state)


class _Client:
    async def terminate(self):
        return None


class WebAPIBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.web = load_web_api_module()
        cls.client_module = load_mihome_client_module()

    def build_api(self, config=None, state=None):
        plugin = types.SimpleNamespace(
            config=_Config(config or {}),
            data_manager=_DataManager(state),
            client=_Client(),
        )
        return self.web.MiHomeWebAPI(plugin), plugin

    def set_request_payload(self, payload):
        async def read_json(default=None):
            return payload if payload is not None else default

        self.web.request.json = read_json

    def test_route_surface_is_management_only(self):
        api, _plugin = self.build_api()
        registered = []

        class Context:
            def register_web_api(
                self,
                path,
                handler,
                methods,
                description,
            ):
                registered.append((path, tuple(methods), description, handler))

        api.register_routes(Context())
        route_methods = {
            (path.removeprefix("/astrbot_plugin_mihome/"), methods[0])
            for path, methods, _description, _handler in registered
        }
        self.assertEqual(
            route_methods,
            {
                ("status", "GET"),
                ("auth/start", "POST"),
                ("auth/status", "GET"),
                ("auth/logout", "POST"),
                ("devices", "GET"),
                ("devices/sync", "POST"),
                ("devices/mappings", "POST"),
                ("devices/status", "GET"),
                ("scenes", "GET"),
                ("scenes/sync", "POST"),
                ("tools", "GET"),
                ("tools", "POST"),
                ("diagnostics", "GET"),
                ("diagnostics/check", "POST"),
            },
        )
        route_text = "\n".join(path for path, *_rest in registered).lower()
        for forbidden in ("control", "execute", "run_scene", "set_prop", "action"):
            self.assertNotIn(forbidden, route_text)

    def test_mapping_validation_accepts_multiple_aliases_for_one_did(self):
        api, _plugin = self.build_api()
        device_map, category_map = api._parse_mapping_rows(
            {
                "mappings": [
                    {"alias": "客厅灯", "did": "123", "category": "开关类别"},
                    {"alias": "阅读灯", "did": "123", "category": "开关类别"},
                ]
            }
        )
        self.assertEqual(
            device_map,
            {"客厅灯": "123", "阅读灯": "123"},
        )
        self.assertEqual(category_map["客厅灯"], "开关类别")

    def test_mapping_save_is_confirmed_once_and_preserves_orphan_category(self):
        api, plugin = self.build_api(
            {
                "device_map": '{"旧别名": "123"}',
                "device_category_map": (
                    '{"旧别名": "开关类别", "待绑定设备": "空调类别"}'
                ),
            }
        )
        payload = {
            "mappings": [
                {
                    "alias": "客厅灯",
                    "did": "123",
                    "category": "开关类别",
                }
            ],
            "confirm": True,
        }
        self.set_request_payload(payload)
        response = asyncio.run(api.save_device_mappings())

        self.assertTrue(response["payload"]["saved"])
        self.assertEqual(plugin.config.save_count, 1)
        self.assertIn("客厅灯", plugin.config["device_map"])
        self.assertNotIn("\\u5ba2", plugin.config["device_map"])
        saved_categories = json.loads(plugin.config["device_category_map"])
        self.assertEqual(saved_categories["待绑定设备"], "空调类别")

    def test_invalid_mapping_does_not_modify_config(self):
        original = {
            "device_map": '{"客厅灯": "123"}',
            "device_category_map": '{"客厅灯": "开关类别"}',
        }
        api, plugin = self.build_api(original)
        self.set_request_payload(
            {
                "mappings": [
                    {
                        "alias": " 客厅灯",
                        "did": "123",
                        "category": "开关类别",
                    }
                ],
                "confirm": True,
            }
        )
        with self.assertRaises(self.web.WebAPIError):
            asyncio.run(api.save_device_mappings())
        self.assertEqual(dict(plugin.config), original)
        self.assertEqual(plugin.config.save_count, 0)

    def test_sensitive_login_values_are_redacted(self):
        redacted = self.web._redact_text(
            "https://account.xiaomi.com/a?ticket=abc serviceToken=secret-value"
        )
        self.assertNotIn("abc", redacted)
        self.assertNotIn("secret-value", redacted)
        self.assertIn("已隐藏", redacted)

    def test_login_payload_hides_raw_url_and_blocks_parallel_operations(self):
        api, _plugin = self.build_api()

        async def scenario():
            task = asyncio.create_task(asyncio.sleep(60))
            api._login_task = task
            api._login_qr_url = "https://account.xiaomi.com/pass/qr/login?ticket=secret"
            api._login_qr_image = "data:image/svg+xml;base64,PHN2Zy8+"
            api._login_qr_created_at = self.web.time.monotonic()
            try:
                payload = api._login_status_payload()
                self.assertNotIn("qr_url", payload)
                self.assertEqual(
                    payload["qr_image"],
                    "data:image/svg+xml;base64,PHN2Zy8+",
                )
                with self.assertRaises(self.web.WebAPIError) as raised:
                    api._ensure_operation_available()
                self.assertEqual(raised.exception.status_code, 409)
            finally:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        asyncio.run(scenario())

    def test_sensitive_responses_disable_browser_caching(self):
        original = self.web.json_response

        class Response:
            def __init__(self):
                self.headers = {}

        self.web.json_response = lambda *_args, **_kwargs: Response()
        try:
            response = self.web._sensitive_json_response({"ok": True})
        finally:
            self.web.json_response = original

        self.assertEqual(
            response.headers["Cache-Control"],
            "no-store, max-age=0",
        )
        self.assertEqual(response.headers["Pragma"], "no-cache")

    def test_multiline_login_url_is_fully_hidden(self):
        client = object.__new__(self.client_module.MiHomeClient)
        redacted = client._redact_login_output(
            "开始登录\n"
            "https://account.xiaomi.com/pass/qr/login?tic\n"
            "ket=secret-ticket&dc=cn&sid=mihome\n"
            "DEBUG: worker stopped"
        )
        self.assertEqual(redacted, "[米家登录输出已隐藏]")
        self.assertNotIn("secret-ticket", redacted)


class WebUIStaticContractTests(unittest.TestCase):
    def test_page_uses_bridge_without_direct_dashboard_access(self):
        html = (PAGE_DIR / "index.html").read_text(encoding="utf-8")
        script = (PAGE_DIR / "app.js").read_text(encoding="utf-8")
        style = (PAGE_DIR / "style.css").read_text(encoding="utf-8")

        self.assertIn("./style.css", html)
        self.assertIn("./app.js", html)
        self.assertIn("window.AstrBotPluginPage", script)
        self.assertRegex(script, r"\bbridge\.ready\s*\(")
        self.assertIn("bridge.apiGet", script)
        self.assertIn("bridge.apiPost", script)
        self.assertIn("context.isDark", script)
        self.assertIn("await bridge.ready()", script)
        self.assertLess(
            script.index("account.running ??"),
            script.index("account.login_in_progress ??"),
        )
        self.assertIn("#ff6900", style.lower())
        for forbidden in (
            "fetch(",
            "window.confirm",
            "window.prompt",
            "localStorage",
            "sessionStorage",
            "window.parent",
            "parent.document",
        ):
            self.assertNotIn(forbidden, script)

    def test_frontend_contains_server_confirmation_contracts(self):
        script = (PAGE_DIR / "app.js").read_text(encoding="utf-8")
        self.assertIn('confirm: "退出登录"', script)
        self.assertRegex(script, r"confirm\s*:\s*true")
        self.assertIn("confirm_public_scene_tool", script)
        self.assertIn("root.text", script)
        self.assertNotIn("蒸烤锅类别", script)

    def test_qr_is_generated_locally_and_never_sent_to_third_party(self):
        backend = WEB_API_PATH.read_text(encoding="utf-8")
        client = (ROOT / "mihome_client.py").read_text(encoding="utf-8")
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        script = (PAGE_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn("_build_qr_data_uri", backend)
        self.assertIn("SvgPathFillImage", backend)
        self.assertIn('"qr_image": qr_image', backend)
        self.assertNotIn('"qr_url": qr_url', backend)
        self.assertIn("self._extract_qr_url_from_buffer(raw)", client)
        self.assertIn("[米家登录输出已隐藏]", client)
        self.assertRegex(requirements, r"(?m)^qrcode==8\.2$")
        self.assertIn("svg\\+xml", script)
        self.assertNotIn("normalizeAuthUrl", script)
        self.assertNotIn("auth-link", script)
        self.assertNotRegex(
            backend + script,
            r"https?://[^\"'\s]*(?:qrserver|quickchart|googleapis)",
        )

    def test_scene_tool_admin_check_uses_astrbot_event_only(self):
        tree = ast.parse(MAIN_PATH.read_text(encoding="utf-8"))
        plugin_class = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "MiHomeControlPlugin"
        )
        checker = next(
            node
            for node in plugin_class.body
            if isinstance(node, ast.FunctionDef) and node.name == "_event_is_admin"
        )
        source = ast.unparse(checker)
        self.assertIn("event", source)
        self.assertIn("is_admin", source)
        self.assertNotIn("message_obj", source)
        self.assertNotIn("sender", source)

    def test_plugin_page_metadata_and_version_contract(self):
        metadata = METADATA_PATH.read_text(encoding="utf-8")
        self.assertRegex(
            metadata,
            re.compile(r"^short_desc:\s*\S.+$", re.MULTILINE),
        )
        self.assertRegex(
            metadata,
            re.compile(
                r'^astrbot_version:\s*">=4\.24\.2"\s*$',
                re.MULTILINE,
            ),
        )
        changelog = CHANGELOG_PATH.read_text(encoding="utf-8")
        self.assertRegex(
            changelog,
            re.compile(r"^## \[v7\.4\.0\] - \d{4}-\d{2}-\d{2}$", re.MULTILINE),
        )

        tree = ast.parse(MAIN_PATH.read_text(encoding="utf-8"))
        imports = [
            alias.name
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module == "web_api"
            for alias in node.names
        ]
        self.assertIn("MiHomeWebAPI", imports)

        for locale in ("zh-CN", "en-US"):
            path = ROOT / ".astrbot-plugin" / "i18n" / f"{locale}.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("mihome", data["pages"])


if __name__ == "__main__":
    unittest.main()
