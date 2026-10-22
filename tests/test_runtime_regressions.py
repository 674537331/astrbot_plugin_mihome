"""Exercise ambiguous aliases, cancellation and malformed cloud responses."""

import __future__
import asyncio
import ast
import threading
import types
import unittest
from unittest.mock import MagicMock, patch

from test_control_tools import ROOT, client_module


class AliasMatchingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
        plugin = next(node for node in tree.body if isinstance(node, ast.ClassDef))
        method = next(
            node for node in plugin.body
            if isinstance(node, ast.FunctionDef) and node.name == "_match_device_alias"
        )
        namespace = {}
        exec(compile(
            ast.Module(body=[method], type_ignores=[]),
            str(ROOT / "main.py"), "exec",
            flags=__future__.annotations.compiler_flag,
        ), namespace)
        cls.match_alias = staticmethod(namespace["_match_device_alias"])

    def test_longest_alias_wins_before_interpreting_control_arguments(self):
        # Previously this became a write to the shorter alias's 开关 property.
        mappings = {"客厅": "first", "客厅 开关": "second"}
        self.assertEqual(
            self.match_alias(None, ["客厅", "开关", "关"], mappings),
            ("客厅 开关", ["关"]),
        )

    def test_quoted_alias_is_an_explicit_selection(self):
        mappings = {"客厅 开关": "first", "客厅 开关 插座": "second"}
        self.assertEqual(
            self.match_alias(None, ["客厅 开关", "插座", "关"], mappings),
            ("客厅 开关", ["插座", "关"]),
        )

    def test_partial_token_does_not_match(self):
        self.assertEqual(
            self.match_alias(None, ["客厅灯带", "关"], {"客厅灯": "first"}),
            (None, ["客厅灯带", "关"]),
        )


class SyncCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def _check_serialization(self, *, after_warning, repeated):
        client = object.__new__(client_module.MiHomeClient)
        lock = asyncio.Lock()
        started = threading.Event()
        release = threading.Event()
        warned = asyncio.Event()
        second_started = asyncio.Event()
        order = []

        def first_call():
            started.set()
            release.wait(timeout=2)
            order.append("thread-finished")

        def warning(message):
            if "响应较慢" in message:
                warned.set()

        async def first_operation():
            async with lock:
                await client._run_sync_call(
                    first_call, warn_after=0.001 if after_warning else 10,
                    operation="first",
                )

        async def second_operation():
            async with lock:
                order.append("second-started")
                second_started.set()

        with patch.object(client_module.logger, "warning", side_effect=warning):
            first = asyncio.create_task(first_operation())
            second = None
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 1))
                if after_warning:
                    await asyncio.wait_for(warned.wait(), timeout=1)
                first.cancel()
                for _ in range(5):
                    await asyncio.sleep(0)
                if repeated:
                    first.cancel()
                second = asyncio.create_task(second_operation())
                for _ in range(5):
                    await asyncio.sleep(0)
                self.assertFalse(second_started.is_set(), "cancel released the API lock early")
            finally:
                release.set()
                outcomes = await asyncio.gather(
                    first, *([second] if second else []), return_exceptions=True,
                )
        self.assertIsInstance(outcomes[0], asyncio.CancelledError)
        self.assertEqual(order, ["thread-finished", "second-started"])

    async def test_cancel_after_slow_warning_keeps_lock(self):
        await self._check_serialization(after_warning=True, repeated=False)

    async def test_repeated_cancel_keeps_lock(self):
        await self._check_serialization(after_warning=False, repeated=True)

    async def test_repeated_cancel_after_warning_keeps_lock(self):
        await self._check_serialization(after_warning=True, repeated=True)


class CloudDirectoryValidationTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _client(response):
        state = {
            "did_to_model": {"device": "cuco.plug.v3"},
            "did_to_name": {"device": "插座"},
            "scenes": [{"scene_id": "scene", "scene_name": "回家"}],
            "scene_cache_updated_at": "previous-sync",
        }
        client = object.__new__(client_module.MiHomeClient)
        client._login_status = client_module.LOGIN_IDLE
        client._api_lock = asyncio.Lock()
        client.data_manager = types.SimpleNamespace(
            auth_exists=lambda: True,
            load_state=lambda: dict(state),
            update_state=lambda **values: state.update(values) or True,
        )
        client.api = types.SimpleNamespace(
            get_devices_list=MagicMock(return_value=response),
            get_scenes_list=MagicMock(return_value=response),
        )
        return client, state

    async def test_malformed_devices_do_not_erase_model_cache(self):
        for response in (None, {}, {"error": "temporary failure"}):
            with self.subTest(response=response):
                client, state = self._client(response)
                with self.assertRaises(client_module.MiHomeClientError):
                    await client.get_devices()
                self.assertEqual(state["did_to_model"], {"device": "cuco.plug.v3"})

    async def test_malformed_scenes_do_not_erase_scene_cache(self):
        for response in (None, {}, {"error": "temporary failure"}):
            with self.subTest(response=response):
                client, state = self._client(response)
                with self.assertRaises(client_module.MiHomeClientError):
                    await client.get_scenes()
                self.assertEqual(state["scenes"][0]["scene_id"], "scene")
                self.assertEqual(state["scene_cache_updated_at"], "previous-sync")

    async def test_valid_empty_lists_clear_caches(self):
        client, state = self._client([])
        self.assertEqual(await client.get_devices(), [])
        self.assertEqual(state["did_to_model"], {})
        self.assertEqual(await client.get_scenes(), [])
        self.assertEqual(state["scenes"], [])

    async def test_failed_shared_directory_preserves_previous_device_cache(self):
        client, state = self._client([{"did": "new", "model": "new.model", "name": "新设备"}])
        client.api.get_shared_devices_list = MagicMock(return_value={"error": "bad response"})
        devices = await client.get_devices()
        self.assertEqual(len(devices), 1)
        self.assertEqual(state["did_to_model"], {"device": "cuco.plug.v3", "new": "new.model"})
        self.assertIn("共享列表获取异常", state["last_shared_error"])
