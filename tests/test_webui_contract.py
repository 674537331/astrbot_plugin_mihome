import base64
import asyncio
import ast
import contextlib
import importlib.util
import io
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
    devices_module = types.ModuleType("mijiaAPI.devices")
    devices_module.DevAction = type("DevAction", (), {})
    devices_module.DevProp = type("DevProp", (), {})
    sys.modules["mijiaAPI.devices"] = devices_module

    module_name = f"{package_name}.mihome_client"
    spec = importlib.util.spec_from_file_location(
        module_name,
        ROOT / "mihome_client.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def load_login_worker_module():
    module_name = "_mihome_login_worker_test"
    spec = importlib.util.spec_from_file_location(
        module_name,
        ROOT / "_login_worker.py",
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


class _FailingConfig(_Config):
    def save_config(self):
        self.save_count += 1
        raise RuntimeError("simulated persistence failure")


class _DataManager:
    def __init__(self, state=None):
        self.state = state or {}
        self.load_count = 0

    def load_state(self):
        self.load_count += 1
        return dict(self.state)


class _Client:
    async def get_login_status(self, state=None):
        state = state or {}
        return {
            "auth_exists": bool(state.get("auth_exists", False)),
            "last_login_at": state.get("last_login_at", ""),
            "last_login_error": state.get("last_login_error", ""),
            "scene_cache_updated_at": state.get(
                "scene_cache_updated_at",
                "",
            ),
        }

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

    def mapping_revision(self, api):
        return api._config_revision(self.web._MAPPING_REVISION_KEYS)

    def tool_revision(self, api):
        return api._config_revision(self.web._TOOL_REVISION_KEYS)

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
            "revision": self.mapping_revision(api),
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
                "revision": self.mapping_revision(api),
            }
        )
        with self.assertRaises(self.web.WebAPIError):
            asyncio.run(api.save_device_mappings())
        self.assertEqual(dict(plugin.config), original)
        self.assertEqual(plugin.config.save_count, 0)

    def test_mapping_rebind_fails_closed_for_control_allowlist(self):
        api, plugin = self.build_api(
            {
                "device_map": '{"客厅灯": "old-did"}',
                "device_category_map": '{"客厅灯": "开关类别"}',
                "control_tool": {
                    "enable": True,
                    "admin_only": True,
                    "allowed_devices": ["客厅灯"],
                },
            }
        )
        self.set_request_payload(
            {
                "mappings": [
                    {
                        "alias": "客厅灯",
                        "did": "new-did",
                        "category": "开关类别",
                    }
                ],
                "confirm": True,
                "revision": self.mapping_revision(api),
            }
        )

        response = asyncio.run(api.save_device_mappings())

        self.assertTrue(response["payload"]["saved"])
        self.assertEqual(
            response["payload"]["changes"]["control_allowlist_removed"],
            ["客厅灯"],
        )
        self.assertEqual(
            plugin.config["control_tool"]["allowed_devices"],
            [],
        )
        self.assertEqual(
            response["payload"]["tool_revision"],
            self.tool_revision(api),
        )

    def test_mapping_preview_rejects_stale_native_config_revision(self):
        api, plugin = self.build_api(
            {
                "device_map": '{"客厅灯": "old-did"}',
                "device_category_map": '{"客厅灯": "开关类别"}',
            }
        )
        stale_revision = self.mapping_revision(api)
        plugin.config["device_map"] = '{"卧室灯": "new-did"}'
        self.set_request_payload(
            {
                "mappings": [
                    {
                        "alias": "客厅灯",
                        "did": "old-did",
                        "category": "开关类别",
                    }
                ],
                "revision": stale_revision,
            }
        )

        with self.assertRaises(self.web.WebAPIError) as raised:
            asyncio.run(api.save_device_mappings())

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("插件设置", raised.exception.message)
        self.assertEqual(plugin.config.save_count, 0)

    def test_sensitive_login_values_are_redacted(self):
        redacted = self.web._redact_text(
            "https://account.xiaomi.com/a?ticket=abc "
            "serviceToken=secret-value psecurity=p-secret "
            "nonce=nonce-secret pass_o=pass-secret"
        )
        self.assertNotIn("abc", redacted)
        self.assertNotIn("secret-value", redacted)
        self.assertNotIn("p-secret", redacted)
        self.assertNotIn("nonce-secret", redacted)
        self.assertNotIn("pass-secret", redacted)
        self.assertIn("已隐藏", redacted)

    def test_cached_device_rows_reuses_one_state_snapshot(self):
        api, plugin = self.build_api(
            state={
                "did_to_name": {"did-1": "客厅灯"},
                "did_to_model": {"did-1": "example.light.v1"},
            }
        )

        rows = api._cached_device_rows()

        self.assertEqual(plugin.data_manager.load_count, 1)
        self.assertEqual(rows[0]["did"], "did-1")
        self.assertEqual(rows[0]["name"], "客厅灯")

    def test_status_endpoint_reuses_one_state_snapshot(self):
        api, plugin = self.build_api(
            state={
                "did_to_name": {"did-1": "客厅灯"},
                "did_to_model": {"did-1": "example.light.v1"},
                "scenes": [],
            }
        )

        response = asyncio.run(api.get_status())

        self.assertTrue(response["payload"]["ok"])
        self.assertEqual(plugin.data_manager.load_count, 1)

    def test_status_keeps_cloud_failure_out_of_authorization_state(self):
        api, _plugin = self.build_api(
            state={
                "auth_exists": True,
                "last_login_error": "拉取云端设备列表超时",
            }
        )

        auth = asyncio.run(api.get_status())["payload"]["auth"]

        self.assertEqual(auth["last_error_scope"], "cloud_connection")
        self.assertEqual(auth["credential_state"], "present")
        self.assertFalse(auth["authorization_problem"])

    def test_status_marks_explicit_auth_failure_as_invalid(self):
        api, _plugin = self.build_api(
            state={
                "auth_exists": True,
                "last_login_error": "鉴权失效",
            }
        )

        auth = asyncio.run(api.get_status())["payload"]["auth"]

        self.assertEqual(auth["last_error_scope"], "authorization")
        self.assertEqual(auth["credential_state"], "invalid")
        self.assertTrue(auth["authorization_problem"])

    def test_auth_poll_prefers_current_terminal_login_detail(self):
        api, plugin = self.build_api()

        async def get_login_status(_state=None):
            return {
                "auth_exists": True,
                "last_login_error": "鉴权失效",
                "credential_storage_error": (
                    "登录凭证已保存，但插件状态记录保存失败，"
                    "请检查数据目录权限"
                ),
            }

        plugin.client = types.SimpleNamespace(
            get_login_status=get_login_status,
        )
        api._login_result = {
            "status": "error",
            "message": "登录失败，请查看诊断信息或 AstrBot 日志。",
            "detail": (
                "登录凭证已保存，但插件状态记录保存失败，"
                "请检查数据目录权限"
            ),
        }

        auth = asyncio.run(api.get_auth_status())["payload"]

        self.assertEqual(auth["last_error_scope"], "credential_storage")
        self.assertEqual(auth["credential_state"], "attention")
        self.assertFalse(auth["authorization_problem"])
        self.assertIn("插件状态记录保存失败", auth["last_login_error"])
        overview_auth = asyncio.run(api.get_status())["payload"]["auth"]
        self.assertEqual(
            overview_auth["last_error_scope"],
            "credential_storage",
        )
        self.assertEqual(
            overview_auth["last_login_error"],
            auth["last_login_error"],
        )

    def test_auth_poll_converts_recovered_state_write_to_success(self):
        api, plugin = self.build_api()

        async def get_login_status(_state=None):
            return {
                "auth_exists": True,
                "last_login_at": "2026-07-24 12:00:00",
                "last_login_error": "",
                "credential_storage_error": "",
                "login_state_recovered": True,
            }

        plugin.client = types.SimpleNamespace(
            get_login_status=get_login_status,
        )
        api._login_result = {
            "status": "error",
            "message": "登录失败，请查看诊断信息或 AstrBot 日志。",
            "detail": (
                "登录凭证已保存，但插件状态记录保存失败，"
                "请检查数据目录权限"
            ),
        }

        auth = asyncio.run(api.get_auth_status())["payload"]

        self.assertEqual(auth["status"], "success")
        self.assertEqual(auth["detail"], "")
        self.assertEqual(auth["last_login_error"], "")

    def test_status_distinguishes_failed_login_flow_without_credentials(self):
        api, _plugin = self.build_api(
            state={
                "auth_exists": False,
                "last_login_error": "授权确认已超时 (120秒)",
            }
        )

        auth = asyncio.run(api.get_status())["payload"]["auth"]

        self.assertEqual(auth["last_error_scope"], "login_flow")
        self.assertEqual(auth["credential_state"], "missing")
        self.assertFalse(auth["authorization_problem"])

    def test_diagnostics_separates_saved_credentials_and_cloud_failure(self):
        api, _plugin = self.build_api()

        checks = api._diagnostic_checks(
            {
                "auth_exists": True,
                "last_login_error": "网络异常: ConnectionError",
            }
        )
        by_code = {item["code"]: item for item in checks}

        self.assertEqual(by_code["auth"]["level"], "success")
        self.assertEqual(by_code["cloud_connection"]["level"], "warning")
        self.assertIn("网络异常", by_code["cloud_connection"]["message"])

    def test_device_snapshot_discards_unused_upstream_fields(self):
        snapshot = self.web.MiHomeWebAPI._compact_device_snapshot(
            [
                {
                    "did": "did-1",
                    "name": "客厅灯",
                    "model": "example.light.v1",
                    "isOnline": True,
                    "is_shared": True,
                    "token": "must-not-be-kept",
                    "large_payload": "x" * 1000,
                }
            ]
        )

        self.assertEqual(
            set(snapshot[0]),
            {"did", "name", "model", "isOnline", "isShared"},
        )
        self.assertTrue(snapshot[0]["isShared"])

    def test_login_payload_hides_raw_url_and_blocks_parallel_operations(self):
        api, _plugin = self.build_api()

        async def scenario():
            task = asyncio.create_task(asyncio.sleep(60))
            api._login_task = task
            api._login_qr_url = (
                "https://ak.account.xiaomi.com/longPolling/login?"
                "opaque=secret"
            )
            api._login_qr_image = "data:image/svg+xml;base64,PHN2Zy8+"
            api._login_qr_created_at = self.web.time.monotonic()
            api._login_qr_revision = "7"
            try:
                payload = api._login_status_payload()
                self.assertNotIn("qr_url", payload)
                self.assertEqual(
                    payload["qr_image"],
                    "data:image/svg+xml;base64,PHN2Zy8+",
                )
                self.assertTrue(payload["qr_available"])
                self.assertEqual(payload["qr_revision"], "7")
                cached_payload = api._login_status_payload("7")
                self.assertEqual(cached_payload["qr_image"], "")
                self.assertTrue(cached_payload["qr_available"])
                self.assertEqual(cached_payload["qr_revision"], "7")
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
        login_url = (
            "https://ak.account.xiaomi.com/longPolling/login?"
            "opaque=secret-ticket"
        )
        encoded = base64.urlsafe_b64encode(login_url.encode()).decode()
        split_at = len(encoded) // 2
        redacted = client._redact_login_output(
            "开始登录\n"
            f"{self.client_module.WORKER_QR_PAYLOAD_START}"
            f"{encoded[:split_at]}\n{encoded[split_at:]}"
            f"{self.client_module.WORKER_QR_PAYLOAD_END}\n"
            "DEBUG: worker stopped"
        )
        self.assertEqual(redacted, "[米家登录输出已隐藏]")
        self.assertNotIn("secret-ticket", redacted)

    def test_worker_passes_direct_login_url_and_reuses_login_data(self):
        worker = load_login_worker_module()
        client = object.__new__(self.client_module.MiHomeClient)
        login_data = {
            "loginUrl": (
                "https://ak.account.xiaomi.com/longPolling/login?"
                "opaque=direct-login"
            ),
            "qr": (
                "https://account.xiaomi.com/pass/qr/login?"
                "opaque=image-resource"
            ),
            "lp": "https://account.xiaomi.com/longPolling/login?opaque=wait",
        }

        class FakeAPI:
            auth_data = {}

            def __init__(self):
                self.completed_with = None

            def _get_qr_login_data(self):
                return login_data

            def _complete_qr_login(self, value):
                self.completed_with = value
                return {"status": "ok"}

        api = FakeAPI()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = worker._login_with_direct_qr(api)

        worker_output = output.getvalue()
        self.assertEqual(result, {"status": "ok"})
        self.assertIs(api.completed_with, login_data)
        self.assertNotIn(login_data["loginUrl"], worker_output)
        self.assertNotIn(login_data["qr"], worker_output)
        self.assertNotIn(login_data["lp"], worker_output)
        self.assertEqual(
            client._extract_qr_url_from_buffer(worker_output),
            login_data["loginUrl"],
        )

    def test_worker_refresh_path_does_not_emit_qr(self):
        worker = load_login_worker_module()

        class FakeAPI:
            auth_data = {"refreshed": True}

            @staticmethod
            def _get_qr_login_data():
                return {"refreshed": True}

            @staticmethod
            def _complete_qr_login(_value):
                raise AssertionError("刷新成功后不应进入扫码长轮询")

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = worker._login_with_direct_qr(FakeAPI())

        self.assertEqual(result, {"refreshed": True})
        self.assertNotIn(worker.WORKER_QR_PAYLOAD_START, output.getvalue())

    def test_qr_image_url_without_worker_marker_is_ignored(self):
        client = object.__new__(self.client_module.MiHomeClient)
        image_url = (
            "https://account.xiaomi.com/pass/qr/login?"
            "opaque=image-resource"
        )
        self.assertEqual(
            client._extract_qr_url_from_buffer(
                f"也可以访问链接查看二维码图片: {image_url}"
            ),
            "",
        )
        encoded = base64.urlsafe_b64encode(image_url.encode()).decode()
        marked_payload = (
            f"{self.client_module.WORKER_QR_PAYLOAD_START}{encoded}"
            f"{self.client_module.WORKER_QR_PAYLOAD_END}"
        )
        self.assertEqual(
            client._extract_qr_url_from_buffer(marked_payload),
            "",
        )

    def test_login_payload_rejects_lookalike_domain(self):
        client = object.__new__(self.client_module.MiHomeClient)
        malicious = (
            "https://account.xiaomi.com.example.test/longPolling/login?"
            "opaque=malicious"
        )
        encoded = base64.urlsafe_b64encode(malicious.encode()).decode()
        payload = (
            f"{self.client_module.WORKER_QR_PAYLOAD_START}{encoded}"
            f"{self.client_module.WORKER_QR_PAYLOAD_END}"
        )
        self.assertEqual(client._extract_qr_url_from_buffer(payload), "")

    def test_logout_clears_account_snapshot_and_preserves_mappings(self):
        api, plugin = self.build_api(
            {
                "device_map": '{"旧账号设备": "123"}',
                "device_category_map": '{"旧账号设备": "开关类别"}',
            }
        )

        class LogoutClient:
            async def logout(self):
                return True

            async def get_login_status(self):
                return {"auth_exists": False}

        plugin.client = LogoutClient()
        api._device_snapshot = [
            {
                "did": "123",
                "name": "旧账号设备",
                "model": "old.model",
            }
        ]
        api._device_snapshot_at = "2026-07-24 12:00:00"
        self.set_request_payload({"confirm": "退出登录"})

        response = asyncio.run(api.logout())

        self.assertTrue(response["payload"]["credential_removed"])
        self.assertEqual(api._device_snapshot, [])
        self.assertEqual(api._device_snapshot_at, "")
        self.assertIn("旧账号设备", plugin.config["device_map"])
        self.assertIn("旧账号设备", plugin.config["device_category_map"])

    def test_control_tool_requires_confirmation_and_valid_allowlist(self):
        api, plugin = self.build_api(
            {
                "device_map": '{"客厅灯": "123"}',
                "device_category_map": '{"客厅灯": "开关类别"}',
            }
        )
        payload = {
            "enable_readonly_tool": False,
            "scene_tool": {"enable": False, "admin_only": True},
            "control_tool": {
                "enable": True,
                "admin_only": True,
                "allowed_devices": ["客厅灯"],
            },
            "revision": self.tool_revision(api),
        }
        self.set_request_payload(payload)
        with self.assertRaises(self.web.WebAPIError) as raised:
            asyncio.run(api.save_tool_settings())
        self.assertIn("confirm_control_tool", raised.exception.message)
        self.assertNotIn("control_tool", plugin.config)

        payload["confirm_control_tool"] = True
        self.set_request_payload(payload)
        response = asyncio.run(api.save_tool_settings())
        self.assertTrue(response["payload"]["saved"])
        self.assertEqual(
            plugin.config["control_tool"]["allowed_devices"],
            ["客厅灯"],
        )

    def test_control_tool_rejects_unmapped_alias(self):
        api, plugin = self.build_api(
            {
                "device_map": '{"客厅灯": "123"}',
                "device_category_map": '{"客厅灯": "开关类别"}',
            }
        )
        self.set_request_payload(
            {
                "enable_readonly_tool": False,
                "scene_tool": {"enable": False, "admin_only": True},
                "control_tool": {
                    "enable": True,
                    "admin_only": True,
                    "allowed_devices": ["不存在的别名"],
                },
                "confirm_control_tool": True,
                "revision": self.tool_revision(api),
            }
        )
        with self.assertRaises(self.web.WebAPIError):
            asyncio.run(api.save_tool_settings())
        self.assertNotIn("control_tool", plugin.config)

    def test_disabled_control_tool_drops_stale_allowlist_alias(self):
        api, plugin = self.build_api(
            {
                "device_map": '{"客厅灯": "123"}',
                "device_category_map": '{"客厅灯": "开关类别"}',
            }
        )
        self.set_request_payload(
            {
                "enable_readonly_tool": False,
                "scene_tool": {"enable": False, "admin_only": True},
                "control_tool": {
                    "enable": False,
                    "admin_only": True,
                    "allowed_devices": ["旧设备"],
                },
                "revision": self.tool_revision(api),
            }
        )

        response = asyncio.run(api.save_tool_settings())

        self.assertTrue(response["payload"]["saved"])
        self.assertEqual(
            plugin.config["control_tool"]["allowed_devices"],
            [],
        )

    def test_scene_tool_settings_share_native_plugin_config(self):
        api, plugin = self.build_api(
            {
                "scene_tool": {"enable": False, "admin_only": True},
                "enable_scene_tool": False,
                "scene_tool_admin_only": True,
                "enable_readonly_tool": False,
            }
        )
        self.set_request_payload(
            {
                "enable_readonly_tool": True,
                "scene_tool": {"enable": True, "admin_only": False},
                "control_tool": {
                    "enable": False,
                    "admin_only": True,
                    "allowed_devices": [],
                },
                "confirm_public_scene_tool": True,
                "revision": self.tool_revision(api),
            }
        )

        response = asyncio.run(api.save_tool_settings())

        self.assertTrue(response["payload"]["saved"])
        self.assertEqual(
            plugin.config["scene_tool"],
            {"enable": True, "admin_only": False},
        )
        self.assertTrue(plugin.config["enable_scene_tool"])
        self.assertFalse(plugin.config["scene_tool_admin_only"])
        self.assertEqual(plugin.config.save_count, 1)
        self.assertEqual(
            response["payload"]["mapping_revision"],
            self.mapping_revision(api),
        )

        # 模拟原生“插件设置”写入当前 AstrBotConfig。
        plugin.config["scene_tool"] = {"enable": False, "admin_only": True}
        self.assertEqual(
            api._tool_settings()["scene_tool"],
            {"enable": False, "admin_only": True},
        )

        # 原生设置保存会热重载插件；新实例应从同一份持久化配置回读。
        reloaded_api, _reloaded_plugin = self.build_api(dict(plugin.config))
        self.assertEqual(
            reloaded_api._tool_settings()["scene_tool"],
            {"enable": False, "admin_only": True},
        )

    def test_tool_save_rejects_stale_native_config_revision(self):
        api, plugin = self.build_api(
            {
                "scene_tool": {"enable": False, "admin_only": True},
                "enable_readonly_tool": False,
            }
        )
        response = asyncio.run(api.get_tool_settings())
        stale_revision = response["payload"]["revision"]
        plugin.config["scene_tool"] = {"enable": True, "admin_only": True}
        self.set_request_payload(
            {
                "enable_readonly_tool": True,
                "scene_tool": {"enable": False, "admin_only": True},
                "control_tool": {
                    "enable": False,
                    "admin_only": True,
                    "allowed_devices": [],
                },
                "revision": stale_revision,
            }
        )

        with self.assertRaises(self.web.WebAPIError) as raised:
            asyncio.run(api.save_tool_settings())

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("插件设置", raised.exception.message)
        self.assertEqual(plugin.config.save_count, 0)

    def test_tool_settings_roll_back_exactly_when_persistence_fails(self):
        original = {
            "device_map": '{"客厅灯": "123"}',
            "device_category_map": '{"客厅灯": "开关类别"}',
        }
        api, plugin = self.build_api()
        plugin.config = _FailingConfig(original)
        self.set_request_payload(
            {
                "enable_readonly_tool": True,
                "scene_tool": {"enable": False, "admin_only": True},
                "control_tool": {
                    "enable": True,
                    "admin_only": True,
                    "allowed_devices": ["客厅灯"],
                },
                "confirm_control_tool": True,
                "revision": self.tool_revision(api),
            }
        )
        with self.assertRaises(RuntimeError):
            asyncio.run(api.save_tool_settings())
        self.assertEqual(dict(plugin.config), original)


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
        self.assertIn("#12875d", style.lower())
        self.assertNotIn("#ff6900", style.lower())
        self.assertIn('id="save-device-mappings"', html)
        self.assertIn(
            '$("#save-device-mappings").addEventListener("click", reviewAndSaveMappings)',
            script,
        )
        self.assertIn(".save-dock { display: none !important; }", style)
        self.assertIn("state.tools = normalizeTools(saved)", script)
        self.assertIn('"有未保存修改"', script)
        self.assertIn('"等待读取"', script)
        self.assertIn("await awaitAll([", script)
        self.assertIn("setMappingEditingDisabled(true)", script)
        self.assertIn("setToolEditingDisabled(true)", script)
        self.assertIn("state.configGeneration += 1", script)
        self.assertIn("state.deviceLoading = true", script)
        self.assertIn("state.toolLoading = true", script)
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
        self.assertIn("confirm_control_tool", script)
        self.assertIn("confirm_public_control_tool", script)
        self.assertIn("data-control-alias", script)
        self.assertIn("revision: baseRevision", script)
        self.assertIn("revision: previewRevision", script)
        self.assertIn("state.loaded.tools", script)
        self.assertIn("saved && saved.mapping_revision", script)
        self.assertIn("Tool 设置有未保存修改", script)
        self.assertIn("设备映射有未保存修改", script)
        self.assertIn("root.text", script)
        self.assertNotIn("蒸烤锅类别", script)

    def test_frontend_does_not_overstate_credential_validity(self):
        script = (PAGE_DIR / "app.js").read_text(encoding="utf-8")
        self.assertIn("credential_present", script)
        self.assertIn("accountErrorScope", script)
        self.assertIn("authorization", script)
        self.assertIn("cloud_connection", script)
        self.assertIn("login_flow", script)
        self.assertIn("授权已失效", script)
        self.assertIn("云端连接异常", script)
        self.assertIn("登录未完成", script)
        self.assertIn(
            "if (!running && (auth.last_login_error || auth.error))",
            script,
        )
        self.assertIn("shouldContinue = running", script)
        self.assertIn("authPolling: false", script)
        self.assertIn("authPollPending: false", script)
        self.assertIn("if (state.authPolling)", script)
        self.assertIn("state.authPolling = false", script)
        self.assertIn(
            "if (shouldContinue || pending) scheduleAuthPoll()",
            script,
        )
        poll_start = script.index("async function pollAuthStatus()")
        self.assertLess(
            script.index("if (terminalFailure)", poll_start),
            script.index("if (isLoggedIn(auth) && !running)", poll_start),
        )
        self.assertLess(
            script.index("? SCAN_GUIDANCE", script.index('"#account-description"')),
            script.index(": loginError ||", script.index('"#account-description"')),
        )
        self.assertNotIn("授权有效", script)
        self.assertNotIn("登录凭证当前可用", script)
        self.assertNotIn("服务正常", script)

        html = (PAGE_DIR / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("页面不含", html)

        style = (PAGE_DIR / "style.css").read_text(encoding="utf-8")
        self.assertIn("#0d7650", style.lower())

    def test_qr_is_generated_locally_and_never_sent_to_third_party(self):
        backend = WEB_API_PATH.read_text(encoding="utf-8")
        client = (ROOT / "mihome_client.py").read_text(encoding="utf-8")
        worker = (ROOT / "_login_worker.py").read_text(encoding="utf-8")
        main = MAIN_PATH.read_text(encoding="utf-8")
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        html = (PAGE_DIR / "index.html").read_text(encoding="utf-8")
        script = (PAGE_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn("_build_qr_data_uri", backend)
        self.assertIn("SvgPathFillImage", backend)
        self.assertIn('"qr_image": qr_image', backend)
        self.assertIn('"qr_available": qr_available', backend)
        self.assertIn("known_qr_revision != qr_revision", backend)
        self.assertNotIn('"qr_url": qr_url', backend)
        self.assertIn("self._extract_qr_url_from_buffer(raw)", client)
        self.assertIn("[米家登录输出已隐藏]", client)
        self.assertIn('login_data.get("loginUrl")', worker)
        self.assertIn("complete_login(login_data)", worker)
        self.assertIn("urlsafe_b64encode", worker)
        self.assertNotIn("api.login()", worker)
        self.assertRegex(requirements, r"(?m)^qrcode==8\.2$")
        self.assertIn("svg\\+xml", script)
        self.assertIn("state.authQrRevision", script)
        self.assertIn("qr_revision: state.authQrRevision", script)
        self.assertIn("requestId !== state.authRequestId", script)
        self.assertIn("requestId !== state.drawerRequestId", script)
        self.assertNotIn("normalizeAuthUrl", script)
        self.assertNotIn("auth-link", script)
        self.assertIn("设置 → 小米账号 → 右上角“扫一扫”", html)
        self.assertIn("米家、小米商城、小爱音箱", html)
        self.assertIn("微信、微博、QQ“扫一扫”", html)
        self.assertIn("扫描设备或说明书二维码", html)
        self.assertNotIn("请勿使用米家", backend + html + script)
        self.assertNotRegex(
            backend + script,
            r"https?://[^\"'\s]*(?:qrserver|quickchart|googleapis)",
        )
        plugin_tree = ast.parse(main)
        plugin_class = next(
            node
            for node in plugin_tree.body
            if isinstance(node, ast.ClassDef) and node.name == "MiHomeControlPlugin"
        )
        login_command = next(
            node
            for node in plugin_class.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "mihome_login"
        )
        login_source = ast.unparse(login_command)
        self.assertNotIn("client.login", login_source)
        self.assertNotIn("event.send", login_source)
        self.assertNotIn("qr_callback", login_source)

    def test_upstream_mijia_debug_logs_are_suppressed_before_import(self):
        client = (ROOT / "mihome_client.py").read_text(encoding="utf-8")
        worker = (ROOT / "_login_worker.py").read_text(encoding="utf-8")

        guard = 'logging.getLogger("mijiaAPI").setLevel(logging.WARNING)'
        self.assertIn(guard, client)
        self.assertLess(client.index(guard), client.index("from mijiaAPI import"))
        self.assertIn(guard, worker)

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
        self.assertNotIn("role", source)
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
            re.compile(r"^## \[v8\.1\.1\] - 2026-07-24$", re.MULTILINE),
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
