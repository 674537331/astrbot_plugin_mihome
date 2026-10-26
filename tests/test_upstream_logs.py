"""使用独立进程和真实固定依赖验证日志静默与登录协议。"""

import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SECRET = "UPSTREAM_SECRET_CANARY"
PRELUDE = """
import importlib
import json
import sys
import tempfile
import types
from pathlib import Path

upstream = importlib.import_module("mijiaAPI")
apis = importlib.import_module("mijiaAPI.apis")
devices = importlib.import_module("mijiaAPI.devices")
upstream_logs = importlib.import_module("mijiaAPI.logger")
secret = "UPSTREAM_SECRET_CANARY"

class LeakyLogger:
    def __getattr__(self, name):
        def emit(*args, **kwargs):
            print(secret, name, args, kwargs, file=sys.stderr)
        return emit

for module in (apis, devices, upstream_logs):
    module.logger = LeakyLogger()
"""


class UpstreamLogPrivacyTests(unittest.TestCase):
    def run_isolated(self, source):
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        process = subprocess.run(
            [sys.executable, "-c", PRELUDE + textwrap.dedent(source)],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertNotIn(SECRET, process.stdout + process.stderr)
        return process

    def test_client_suppresses_real_auth_and_request_logs(self):
        process = self.run_isolated("""
            import base64
            from _upstream_logs import silence_mijiaapi_logs

            astrbot = types.ModuleType("astrbot")
            astrbot_api = types.ModuleType("astrbot.api")
            astrbot_api.logger = object()
            astrbot.api = astrbot_api
            sys.modules["astrbot"] = astrbot
            sys.modules["astrbot.api"] = astrbot_api
            package = types.ModuleType("_mihome_log_privacy_test")
            package.__path__ = [str(Path.cwd())]
            sys.modules[package.__name__] = package

            unrelated = types.ModuleType("unrelated_dependency")
            unrelated.logger = original_logger = object()
            sys.modules[unrelated.__name__] = unrelated
            original_streams = (sys.stdout, sys.stderr)

            client = importlib.import_module(package.__name__ + ".mihome_client")
            assert client.logger is astrbot_api.logger
            with tempfile.TemporaryDirectory() as directory:
                auth_path = Path(directory) / "auth.json"
                api = client.mijiaAPI(str(auth_path))
                api.auth_data = {
                    "serviceToken": secret,
                    "ssecurity": base64.b64encode(b"0123456789abcdef").decode(),
                }
                api._save_auth_data()
                assert json.loads(auth_path.read_text())["serviceToken"] == secret
                api.session = types.SimpleNamespace(post=lambda *a, **kw:
                    types.SimpleNamespace(text=json.dumps({
                        "code": 0, "result": {"value": secret},
                    }))
                )
                assert api._request("/test", {"value": secret},
                                    refresh_token=False) == {"value": secret}

            devices.logger.warning("control value: %s", secret)
            upstream_logs.logger.error("auth: %s", secret)
            silence_mijiaapi_logs()
            assert unrelated.logger is original_logger
            assert (sys.stdout, sys.stderr) == original_streams
        """)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stdout + process.stderr, "")

    def run_worker(self, fail=False):
        return self.run_isolated("""
            import runpy
            import stat

            class LocalLoginAPI(apis.mijiaAPI):
                def __init__(self, auth_path):
                    super().__init__(auth_path)
                    apis.logger.debug(secret)
                    devices.logger.warning(secret)

                def _get_qr_login_data(self):
                    return {"loginUrl":
                        "https://account.xiaomi.com/longPolling/login?ticket=test"}

                def _complete_qr_login(self, data):
                    self.auth_data = {"serviceToken": secret}
                    self._save_auth_data()
                    if FAIL_LOGIN:
                        raise RuntimeError("模拟登录失败")
                    return self.auth_data

            upstream.mijiaAPI = LocalLoginAPI
            with tempfile.TemporaryDirectory() as directory:
                auth_path = Path(directory) / "credentials" / "auth.json"
                sys.argv = ["_login_worker.py", str(auth_path)]
                try:
                    runpy.run_path("_login_worker.py", run_name="__main__")
                except SystemExit as exc:
                    assert FAIL_LOGIN and exc.code == 1
                    assert not auth_path.exists()
                    raise
                assert not FAIL_LOGIN
                assert json.loads(auth_path.read_text())["serviceToken"] == secret
                if sys.platform != "win32":
                    assert stat.S_IMODE(auth_path.stat().st_mode) == 0o600
        """.replace("FAIL_LOGIN", repr(fail)))

    def test_worker_retains_qr_protocol_and_success_output(self):
        process = self.run_worker()
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn("[WORKER_QR_LOGIN_URL]", process.stdout)
        self.assertIn("[/WORKER_QR_LOGIN_URL]", process.stdout)
        self.assertIn("[WORKER_SUCCESS]", process.stdout)
        self.assertEqual(process.stderr, "")

    def test_worker_retains_error_output_and_auth_cleanup(self):
        process = self.run_worker(fail=True)
        self.assertEqual(process.returncode, 1, process.stderr)
        self.assertIn("[WORKER_ERROR]", process.stdout)
        self.assertNotIn("[WORKER_SUCCESS]", process.stdout)
        self.assertEqual(process.stderr, "")


if __name__ == "__main__":
    unittest.main()
