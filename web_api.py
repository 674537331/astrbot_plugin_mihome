# -*- coding: utf-8 -*-
"""米家插件的 AstrBot Plugin Pages 后端。

这里仅提供账号授权、配置、同步和诊断能力。为避免 WebUI 变成另一个
“米家遥控器”，本模块刻意不注册任何设备控制或场景执行接口。

``Context.register_web_api`` 注册的路由由 AstrBot Dashboard 统一鉴权；
本模块通过 Dashboard Plugin Pages 桥接提供管理接口。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import inspect
import json
import re
import time
from copy import deepcopy
from datetime import datetime
from functools import wraps
from typing import Any, Awaitable, Callable

from astrbot.api import logger
from astrbot.api.web import json_response, request

from .device_profiles import (
    CATEGORY_AC,
    CATEGORY_AIR_FRYER,
    CATEGORY_BODY_SCALE,
    CATEGORY_COOKER,
    CATEGORY_DOOR_SENSOR,
    CATEGORY_FAN,
    CATEGORY_GAS_SENSOR,
    CATEGORY_NONE,
    CATEGORY_PURIFIER,
    CATEGORY_ROUTER,
    CATEGORY_SPEAKER,
    CATEGORY_SWITCH,
    CATEGORY_TH_SENSOR,
    CATEGORY_VACUUM,
    CATEGORY_WATER_HEATER,
    VALID_CATEGORIES,
    has_model_profile,
    resolve_effective_category,
)
from .mihome_client import MiHomeAuthError, MiHomeClientError

PLUGIN_NAME = "astrbot_plugin_mihome"

MAX_MAPPING_COUNT = 500
MAX_ALIAS_LENGTH = 64
MAX_DID_LENGTH = 128
MAX_ERROR_LENGTH = 360

CATEGORY_OPTIONS = (
    CATEGORY_NONE,
    CATEGORY_AC,
    CATEGORY_PURIFIER,
    CATEGORY_FAN,
    CATEGORY_COOKER,
    CATEGORY_AIR_FRYER,
    CATEGORY_TH_SENSOR,
    CATEGORY_BODY_SCALE,
    CATEGORY_VACUUM,
    CATEGORY_WATER_HEATER,
    CATEGORY_ROUTER,
    CATEGORY_SPEAKER,
    CATEGORY_SWITCH,
    CATEGORY_DOOR_SENSOR,
    CATEGORY_GAS_SENSOR,
)

_MAPPING_REVISION_KEYS = (
    "device_map",
    "device_category_map",
    "control_tool",
)
_TOOL_REVISION_KEYS = (
    "scene_tool",
    "control_tool",
    "enable_readonly_tool",
    "enable_scene_tool",
    "scene_tool_admin_only",
    "device_map",
    "device_category_map",
)

_DID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REVISION_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
_URL_PATTERN = re.compile(r"https?://[^\s]+", flags=re.IGNORECASE)
_SECRET_PATTERN = re.compile(
    r"(?i)(ticket|serviceToken|passToken|ssecurity|psecurity|nonce|pass_o|"
    r"deviceId|userId|cookie|token|ua)"
    r"([\"']?\s*[:=]\s*[\"']?)([^,\s\"'&}]+)"
)


class WebAPIError(Exception):
    """可安全返回给管理页面的请求错误。"""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _redact_text(value: Any, *, limit: int = MAX_ERROR_LENGTH) -> str:
    """移除错误文本中的 URL，防止二维码登录票据意外进入 WebUI。"""

    text = str(value or "").strip()
    if not text:
        return ""
    text = _URL_PATTERN.sub("[链接已隐藏]", text)
    text = _SECRET_PATTERN.sub(r"\1\2[已隐藏]", text)
    text = text.replace("\x00", "")
    if len(text) > limit:
        return f"{text[:limit]}…"
    return text


def _build_qr_data_uri(content: str) -> str:
    """在本地把登录 URL 编码为 SVG 二维码，不经过第三方服务。"""

    if not content:
        return ""
    try:
        import qrcode
        import qrcode.image.svg

        image = qrcode.make(
            content,
            image_factory=qrcode.image.svg.SvgPathFillImage,
        )
        encoded = base64.b64encode(image.to_string()).decode("ascii")
        return f"data:image/svg+xml;base64,{encoded}"
    except Exception as exc:
        # 异常日志只记录类型，不带可能包含登录 URL 的上下文。
        logger.warning(
            "[MiHome WebUI] 本地生成登录二维码失败: %s",
            type(exc).__name__,
        )
        return ""


def _sensitive_json_response(
    payload: dict[str, Any],
    *,
    status_code: int = 200,
):
    """为可能含短时登录票据的响应显式禁用缓存。"""

    response = json_response(payload, status_code=status_code)
    headers = getattr(response, "headers", None)
    if headers is not None:
        headers["Cache-Control"] = "no-store, max-age=0"
        headers["Pragma"] = "no-cache"
    return response


def _safe_handler(
    handler: Callable[[], Awaitable[Any]],
) -> Callable[[], Awaitable[Any]]:
    """统一处理异常，日志保留细节，响应不泄露底层凭证或文件路径。"""

    @wraps(handler)
    async def wrapper(*_args: Any, **_kwargs: Any):
        try:
            return await handler()
        except asyncio.CancelledError:
            raise
        except WebAPIError as exc:
            return json_response(
                {"ok": False, "error": exc.message},
                status_code=exc.status_code,
            )
        except MiHomeAuthError:
            return json_response(
                {
                    "ok": False,
                    "error": "米家登录已失效，请重新扫码登录。",
                },
                status_code=401,
            )
        except MiHomeClientError as exc:
            logger.warning(
                "[MiHome WebUI] 米家客户端请求失败: %s",
                _redact_text(exc),
            )
            return json_response(
                {
                    "ok": False,
                    "error": "米家云端请求失败，请稍后重试或查看诊断信息。",
                    "detail": _redact_text(exc),
                },
                status_code=502,
            )
        except Exception as exc:
            logger.error(
                "[MiHome WebUI] 接口 %s 执行失败: %s",
                handler.__name__,
                type(exc).__name__,
            )
            return json_response(
                {
                    "ok": False,
                    "error": "插件内部错误，请查看 AstrBot 日志。",
                },
                status_code=500,
            )

    return wrapper


class MiHomeWebAPI:
    """米家 Plugin Page 的受限管理 API。"""

    def __init__(self, plugin: Any):
        self.plugin = plugin
        self._operation_lock = asyncio.Lock()
        self._login_start_lock = asyncio.Lock()
        self._login_task: asyncio.Task | None = None
        self._login_qr_url = ""
        self._login_qr_image = ""
        self._login_started_at = ""
        self._login_qr_created_at = 0.0
        self._login_result: dict[str, Any] = {
            "status": "idle",
            "message": "尚未开始登录",
        }
        self._device_snapshot: list[dict[str, Any]] = []
        self._device_snapshot_at = ""

    def _clear_account_runtime_state(self) -> None:
        self._device_snapshot = []
        self._device_snapshot_at = ""
        self._login_task = None
        self._login_qr_url = ""
        self._login_qr_image = ""
        self._login_qr_created_at = 0.0
        self._login_started_at = ""
        self._login_result = {
            "status": "idle",
            "message": "已退出米家账号。",
        }

    async def _finish_account_runtime_reset(
        self,
        login_task: asyncio.Task | None,
    ) -> None:
        if (
            login_task is not None
            and login_task is not asyncio.current_task()
            and not login_task.done()
        ):
            login_task.cancel()
            try:
                await login_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.debug(
                    "[MiHome WebUI] 登出时回收登录任务异常: %s",
                    type(exc).__name__,
                )
        self._clear_account_runtime_state()

    async def logout_account(self) -> bool:
        """供聊天命令与管理页共用的账号登出协调器。"""

        async with self._login_start_lock:
            async with self._operation_lock:
                login_task = self._login_task
                try:
                    removed = await self._client.logout()
                except MiHomeClientError:
                    client_status = await self._client.get_login_status()
                    if not client_status.get("auth_exists"):
                        await self._finish_account_runtime_reset(login_task)
                    raise
                await self._finish_account_runtime_reset(login_task)
                return bool(removed)

    # ------------------------------------------------------------------
    # 路由注册
    # ------------------------------------------------------------------

    def register_routes(self, context: Any) -> None:
        """注册由 AstrBot Dashboard 统一鉴权的页面接口。"""

        def register(
            method: str,
            path: str,
            handler: Callable[[], Awaitable[Any]],
            description: str,
        ) -> None:
            context.register_web_api(
                f"/{PLUGIN_NAME}{path}",
                _safe_handler(handler),
                [method],
                description,
            )

        register("GET", "/status", self.get_status, "米家 WebUI：概览")
        register("POST", "/auth/start", self.start_login, "米家 WebUI：开始扫码登录")
        register(
            "GET", "/auth/status", self.get_auth_status, "米家 WebUI：登录流程状态"
        )
        register("POST", "/auth/logout", self.logout, "米家 WebUI：退出登录")
        register("GET", "/devices", self.get_devices, "米家 WebUI：设备清单")
        register("POST", "/devices/sync", self.sync_devices, "米家 WebUI：同步设备")
        register(
            "POST",
            "/devices/mappings",
            self.save_device_mappings,
            "米家 WebUI：保存设备映射",
        )
        register(
            "GET",
            "/devices/status",
            self.get_readonly_device_status,
            "米家 WebUI：读取已配置设备状态",
        )
        register("GET", "/scenes", self.get_scenes, "米家 WebUI：场景缓存")
        register("POST", "/scenes/sync", self.sync_scenes, "米家 WebUI：同步场景")
        register("GET", "/tools", self.get_tool_settings, "米家 WebUI：Tool 设置")
        register(
            "POST", "/tools", self.save_tool_settings, "米家 WebUI：保存 Tool 设置"
        )
        register("GET", "/diagnostics", self.get_diagnostics, "米家 WebUI：诊断")
        register(
            "POST",
            "/diagnostics/check",
            self.run_diagnostics,
            "米家 WebUI：重新诊断",
        )

    async def shutdown(self) -> None:
        """停止页面后台任务，不保留二维码登录票据。"""

        task = self._login_task
        if task is not None and not task.done():
            # 先终止登录子进程，再取消包装任务，避免遗留沙箱进程。
            terminator = getattr(self._client, "terminate", None)
            if callable(terminator):
                try:
                    result = terminator()
                    if inspect.isawaitable(result):
                        await result
                except Exception as exc:
                    logger.warning(
                        "[MiHome WebUI] 停止登录子进程失败: %s",
                        exc,
                    )
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.debug("[MiHome WebUI] 登录任务结束异常: %s", exc)

        self._login_task = None
        self._login_qr_url = ""
        self._login_qr_image = ""
        self._login_qr_created_at = 0.0
        self._login_started_at = ""
        self._login_result = {
            "status": "idle",
            "message": "登录流程已停止。",
        }

    async def terminate(self) -> None:
        """兼容插件生命周期中常用的 terminate 命名。"""

        await self.shutdown()

    # ------------------------------------------------------------------
    # 通用辅助
    # ------------------------------------------------------------------

    @property
    def _config(self) -> Any:
        return self.plugin.config

    @property
    def _client(self) -> Any:
        return self.plugin.client

    @property
    def _data_manager(self) -> Any:
        return self.plugin.data_manager

    async def _read_json_body(self) -> dict[str, Any]:
        payload = await request.json(default=None)
        if not isinstance(payload, dict):
            raise WebAPIError("请求体必须是 JSON 对象。")
        return payload

    async def _persist_config(self) -> None:
        """兼容 AstrBotConfig 与少量旧版插件保存方式。"""

        saver = getattr(self._config, "save_config", None)
        if not callable(saver):
            saver = getattr(self.plugin, "save_config", None)
        if not callable(saver):
            raise RuntimeError("当前 AstrBotConfig 不支持持久化")
        result = saver()
        if inspect.isawaitable(result):
            await result

    def _config_revision(self, keys: tuple[str, ...]) -> str:
        """为 WebUI 管理的配置生成稳定版本，用于阻止陈旧页面覆盖新设置。"""

        snapshot = {
            key: (
                {"present": True, "value": deepcopy(self._config[key])}
                if key in self._config
                else {"present": False}
            )
            for key in keys
        }
        encoded = json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _expected_revision(self, data: dict[str, Any]) -> str:
        revision = data.get("revision")
        if not isinstance(revision, str) or not _REVISION_PATTERN.fullmatch(revision):
            raise WebAPIError("配置版本无效，请刷新页面后重试。", 409)
        return revision

    def _ensure_revision(self, expected: str, keys: tuple[str, ...]) -> None:
        current = self._config_revision(keys)
        if not hmac.compare_digest(expected, current):
            raise WebAPIError(
                "配置已由 AstrBot 插件设置或其他管理页面更新，请刷新后重试。",
                409,
            )

    def _ensure_operation_available(
        self,
        *,
        allow_login_task: bool = False,
    ) -> None:
        if self._operation_lock.locked():
            raise WebAPIError("已有管理操作正在执行，请稍后再试。", 409)
        if (
            not allow_login_task
            and self._login_task is not None
            and not self._login_task.done()
        ):
            raise WebAPIError(
                "扫码登录正在进行，完成或退出登录后再执行此操作。",
                409,
            )

    def _parse_config_map(self, key: str) -> tuple[dict[str, str], str]:
        raw = self._config.get(key, "{}")
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}, f"{key} 不是合法 JSON"
        if not isinstance(parsed, dict):
            return {}, f"{key} 必须是 JSON 对象"

        normalized: dict[str, str] = {}
        for raw_key, raw_value in parsed.items():
            key_text = str(raw_key).strip()
            if not key_text:
                continue
            normalized[key_text] = str(raw_value).strip()
        return normalized, ""

    def _mapping_snapshot(
        self,
    ) -> tuple[dict[str, str], dict[str, str], list[str]]:
        device_map, device_error = self._parse_config_map("device_map")
        category_map, category_error = self._parse_config_map("device_category_map")
        errors = [item for item in (device_error, category_error) if item]
        return device_map, category_map, errors

    def _cached_device_rows(self) -> list[dict[str, Any]]:
        if self._device_snapshot:
            return self._build_device_rows(self._device_snapshot)

        state = self._data_manager.load_state()
        did_to_name = state.get("did_to_name", {})
        did_to_model = state.get("did_to_model", {})
        if not isinstance(did_to_name, dict):
            did_to_name = {}
        if not isinstance(did_to_model, dict):
            did_to_model = {}

        cached = []
        all_dids = dict.fromkeys(
            [str(key) for key in did_to_name] + [str(key) for key in did_to_model]
        )
        for did in all_dids:
            cached.append(
                {
                    "did": did,
                    "name": did_to_name.get(did, "未知设备"),
                    "model": did_to_model.get(did, ""),
                    "isOnline": None,
                }
            )
        return self._build_device_rows(cached)

    def _build_device_rows(self, devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
        device_map, category_map, _ = self._mapping_snapshot()
        aliases_by_did: dict[str, list[str]] = {}
        for alias, did in device_map.items():
            aliases_by_did.setdefault(did, []).append(alias)

        rows: list[dict[str, Any]] = []
        seen_dids: set[str] = set()
        state = self._data_manager.load_state()
        cached_names = state.get("did_to_name", {})
        cached_models = state.get("did_to_model", {})
        if not isinstance(cached_names, dict):
            cached_names = {}
        if not isinstance(cached_models, dict):
            cached_models = {}
        for item in devices:
            if not isinstance(item, dict):
                continue
            did = str(item.get("did") or "").strip()
            if not did or did in seen_dids:
                continue
            seen_dids.add(did)
            name = str(item.get("name") or "未知设备").strip() or "未知设备"
            model = str(item.get("model") or "").strip()
            online = item.get("isOnline")
            if not isinstance(online, bool):
                online = None
            shared = item.get(
                "isShared",
                item.get("is_shared", item.get("shared")),
            )
            if not isinstance(shared, bool):
                shared = None

            aliases = sorted(aliases_by_did.get(did, []))
            mappings = []
            for alias in aliases:
                configured = category_map.get(alias, CATEGORY_NONE)
                effective = resolve_effective_category(
                    model=model,
                    category=configured,
                )
                mappings.append(
                    {
                        "alias": alias,
                        "category": configured
                        if configured in VALID_CATEGORIES
                        else CATEGORY_NONE,
                        "effective_category": effective,
                    }
                )

            rows.append(
                {
                    "did": did,
                    "name": name,
                    "model": model,
                    "online": online,
                    "shared": shared,
                    "configured": bool(aliases),
                    "aliases": aliases,
                    "mappings": mappings,
                    "profile_matched": has_model_profile(model),
                }
            )

        # 仍展示不在当前云端快照中的旧映射，便于管理员修复。
        for did, aliases in aliases_by_did.items():
            if did in seen_dids:
                continue
            name = str(cached_names.get(did) or "未在当前设备列表中").strip()
            model = str(cached_models.get(did) or "").strip()
            mappings = []
            for alias in sorted(aliases):
                configured = category_map.get(alias, CATEGORY_NONE)
                mappings.append(
                    {
                        "alias": alias,
                        "category": configured
                        if configured in VALID_CATEGORIES
                        else CATEGORY_NONE,
                        "effective_category": resolve_effective_category(
                            model=model,
                            category=configured,
                        ),
                    }
                )
            rows.append(
                {
                    "did": did,
                    "name": name,
                    "model": model,
                    "online": None,
                    "shared": None,
                    "configured": True,
                    "aliases": sorted(aliases),
                    "mappings": mappings,
                    "profile_matched": has_model_profile(model),
                    "missing_from_cloud": True,
                }
            )

        rows.sort(key=lambda row: (not row["configured"], row["name"], row["did"]))
        return rows

    def _sanitized_scenes(self) -> list[dict[str, str]]:
        state = self._data_manager.load_state()
        scenes = state.get("scenes", [])
        if not isinstance(scenes, list):
            return []
        result = []
        for scene in scenes:
            if not isinstance(scene, dict):
                continue
            result.append(
                {
                    "scene_id": str(scene.get("scene_id") or "").strip(),
                    "scene_name": str(scene.get("scene_name") or "未命名场景").strip(),
                    "home_id": str(scene.get("home_id") or "").strip(),
                    "home_name": str(scene.get("home_name") or "未知家庭").strip(),
                }
            )
        return result

    def _tool_settings(self) -> dict[str, Any]:
        scene_config = self._config.get("scene_tool", {})
        if not isinstance(scene_config, dict):
            scene_config = {}
        scene_enable = bool(
            scene_config.get(
                "enable",
                self._config.get("enable_scene_tool", False),
            )
        )
        scene_admin_only = bool(
            scene_config.get(
                "admin_only",
                self._config.get("scene_tool_admin_only", True),
            )
        )
        control_config = self._config.get("control_tool", {})
        if not isinstance(control_config, dict):
            control_config = {}
        allowed_devices = control_config.get("allowed_devices", [])
        if isinstance(allowed_devices, str):
            try:
                allowed_devices = json.loads(allowed_devices)
            except json.JSONDecodeError:
                allowed_devices = []
        if not isinstance(allowed_devices, list):
            allowed_devices = []
        allowed_aliases = []
        for value in allowed_devices:
            alias = str(value or "").strip()
            if alias and alias not in allowed_aliases:
                allowed_aliases.append(alias)

        scene_risky = scene_enable and not scene_admin_only
        control_enable = bool(control_config.get("enable", False))
        control_admin_only = bool(control_config.get("admin_only", True))
        control_risky = control_enable and not control_admin_only
        warnings = []
        if scene_risky:
            warnings.append("场景 Tool 当前允许非管理员调用，可能触发真实自动化。")
        if control_risky:
            warnings.append("设备控制 Tool 当前允许非管理员调用，可能触发真实物理操作。")
        if control_enable and not allowed_aliases:
            warnings.append("设备控制 Tool 已开启但白名单为空，当前控制范围为空。")
        return {
            "scene_tool": {
                "enable": scene_enable,
                "admin_only": scene_admin_only,
            },
            "control_tool": {
                "enable": control_enable,
                "admin_only": control_admin_only,
                "allowed_devices": allowed_aliases,
            },
            "enable_readonly_tool": bool(
                self._config.get("enable_readonly_tool", False)
            ),
            "warnings": warnings,
        }

    # ------------------------------------------------------------------
    # 概览与登录
    # ------------------------------------------------------------------

    async def get_status(self):
        login = await self._client.get_login_status()
        credential_present = bool(login.get("auth_exists"))
        login_error = _redact_text(login.get("last_login_error"))
        rows = self._cached_device_rows()
        scenes = self._sanitized_scenes()
        device_map, _category_map, mapping_errors = self._mapping_snapshot()
        tool_settings = self._tool_settings()
        unmapped = sum(
            1
            for row in rows
            if not row.get("configured") and not row.get("missing_from_cloud")
        )
        return json_response(
            {
                "ok": True,
                "auth": {
                    "credential_present": credential_present,
                    "credential_state": (
                        "attention"
                        if credential_present and login_error
                        else "present"
                        if credential_present
                        else "missing"
                    ),
                    "logged_in": credential_present,
                    "login_in_progress": bool(
                        login.get("login_in_progress")
                        or (
                            self._login_task is not None and not self._login_task.done()
                        )
                    ),
                    "last_login_at": str(login.get("last_login_at") or ""),
                    "last_login_error": login_error,
                },
                "summary": {
                    "cloud_device_count": sum(
                        1 for row in rows if not row.get("missing_from_cloud")
                    ),
                    "configured_alias_count": len(device_map),
                    "unmapped_device_count": unmapped,
                    "scene_count": len(scenes),
                    "scene_cache_updated_at": str(
                        login.get("scene_cache_updated_at") or ""
                    ),
                },
                "tools": tool_settings,
                "mapping_errors": mapping_errors,
                "security": {
                    "dashboard_authenticated_api": True,
                    "device_control_available": False,
                    "scene_execution_available": False,
                    "credentials_exposed": False,
                    "llm_device_control_enabled": tool_settings["control_tool"][
                        "enable"
                    ],
                },
            }
        )

    async def start_login(self):
        # 接受空对象，避免跨站表单直接触发；Plugin Page bridge 会发送 JSON。
        await self._read_json_body()
        async with self._login_start_lock:
            if self._login_task is not None and not self._login_task.done():
                raise WebAPIError("扫码登录流程已在进行中。", 409)
            self._ensure_operation_available(allow_login_task=True)
            async with self._operation_lock:
                client_status = await self._client.get_login_status()
                if client_status.get("auth_exists"):
                    self._login_result = {
                        "status": "already_logged_in",
                        "message": "当前已有米家登录凭证。",
                    }
                    return _sensitive_json_response(
                        {"ok": True, **self._login_status_payload()}
                    )

                self._login_qr_url = ""
                self._login_qr_image = ""
                self._login_qr_created_at = 0.0
                self._login_started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self._login_result = {
                    "status": "starting",
                    "message": "正在启动安全登录进程…",
                }
                self._login_task = asyncio.create_task(
                    self._run_login(),
                    name="mihome-webui-login",
                )
                return _sensitive_json_response(
                    {"ok": True, **self._login_status_payload()},
                    status_code=202,
                )

    async def _run_login(self) -> None:
        async def on_qr(url: str) -> None:
            # URL 只保存在内存中，不写日志和状态文件。
            self._login_qr_url = str(url or "").strip()
            self._login_qr_image = await asyncio.to_thread(
                _build_qr_data_uri,
                self._login_qr_url,
            )
            self._login_qr_created_at = time.monotonic()
            self._login_result = {
                "status": "waiting_scan",
                "message": "请使用米家 App 扫码并确认授权。",
            }

        try:
            result = await self._client.login(qr_callback=on_qr)
            status = str(result.get("status") or "error")
            messages = {
                "success": "授权成功。",
                "already_logged_in": "当前已有米家登录凭证。",
                "timeout": "扫码授权已超时，请重新开始。",
                "qrcode_not_found": "未能生成登录二维码，请查看日志。",
                "in_progress": "扫码登录流程已在进行中。",
                "error": "登录失败，请查看诊断信息或 AstrBot 日志。",
            }
            self._login_result = {
                "status": status,
                "message": messages.get(status, "登录流程已结束。"),
            }
            if status == "error" and result.get("message"):
                self._login_result["detail"] = _redact_text(result.get("message"))
        except asyncio.CancelledError:
            self._login_result = {
                "status": "cancelled",
                "message": "登录流程已取消。",
            }
            raise
        except Exception as exc:
            logger.error(
                "[MiHome WebUI] 登录任务失败: %s",
                type(exc).__name__,
            )
            self._login_result = {
                "status": "error",
                "message": "登录失败，请查看 AstrBot 日志。",
            }
        finally:
            # 已结束的二维码票据不再返回给浏览器。
            self._login_qr_url = ""
            self._login_qr_image = ""
            self._login_qr_created_at = 0.0

    def _login_status_payload(self) -> dict[str, Any]:
        running = self._login_task is not None and not self._login_task.done()
        qr_image = ""
        # 原始登录 URL 仅在服务端内存中用于生成二维码，不返回给浏览器。
        if (
            running
            and self._login_qr_url
            and time.monotonic() - self._login_qr_created_at <= 130
        ):
            qr_image = self._login_qr_image
        return {
            "running": running,
            "started_at": self._login_started_at,
            "qr_image": qr_image,
            "status": self._login_result.get("status", "idle"),
            "message": self._login_result.get("message", ""),
            "detail": _redact_text(self._login_result.get("detail")),
        }

    async def get_auth_status(self):
        login = await self._client.get_login_status()
        credential_present = bool(login.get("auth_exists"))
        login_error = _redact_text(login.get("last_login_error"))
        return _sensitive_json_response(
            {
                "ok": True,
                **self._login_status_payload(),
                "credential_present": credential_present,
                "credential_state": (
                    "attention"
                    if credential_present and login_error
                    else "present"
                    if credential_present
                    else "missing"
                ),
                "logged_in": credential_present,
                "last_login_at": str(login.get("last_login_at") or ""),
                "last_login_error": login_error,
            }
        )

    async def logout(self):
        data = await self._read_json_body()
        if data.get("confirm") != "退出登录":
            raise WebAPIError('请发送 confirm="退出登录" 以确认清除凭证。')
        self._ensure_operation_available(allow_login_task=True)
        removed = await self.logout_account()
        return json_response(
            {
                "ok": True,
                "credential_removed": bool(removed),
                "message": "已退出米家账号并重置登录状态。",
            }
        )

    # ------------------------------------------------------------------
    # 设备与映射
    # ------------------------------------------------------------------

    async def get_devices(self):
        rows = self._cached_device_rows()
        device_map, category_map, mapping_errors = self._mapping_snapshot()
        mappings = [
            {
                "alias": alias,
                "did": did,
                "category": (
                    category_map.get(alias, CATEGORY_NONE)
                    if category_map.get(alias, CATEGORY_NONE) in VALID_CATEGORIES
                    else CATEGORY_NONE
                ),
            }
            for alias, did in sorted(device_map.items())
        ]
        return json_response(
            {
                "ok": True,
                "devices": rows,
                "mappings": mappings,
                "categories": list(CATEGORY_OPTIONS),
                "snapshot_at": self._device_snapshot_at,
                "mapping_errors": mapping_errors,
                "revision": self._config_revision(_MAPPING_REVISION_KEYS),
            }
        )

    async def sync_devices(self):
        await self._read_json_body()
        self._ensure_operation_available()
        async with self._operation_lock:
            devices = await self._client.get_devices()
            self._device_snapshot = [
                dict(item) for item in devices if isinstance(item, dict)
            ]
            self._device_snapshot_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            rows = self._build_device_rows(self._device_snapshot)
        return json_response(
            {
                "ok": True,
                "devices": rows,
                "synced_at": self._device_snapshot_at,
                "revision": self._config_revision(_MAPPING_REVISION_KEYS),
                "message": f"已同步 {len(rows)} 台设备。",
            }
        )

    def _validate_alias(self, value: Any) -> str:
        if not isinstance(value, str):
            raise WebAPIError("设备别名必须是字符串。")
        alias = value.strip()
        if not alias:
            raise WebAPIError("设备别名不能为空。")
        if alias != value:
            raise WebAPIError(f"设备别名“{alias}”首尾不能包含空格。")
        if len(alias) > MAX_ALIAS_LENGTH:
            raise WebAPIError(
                f"设备别名“{alias[:20]}…”超过 {MAX_ALIAS_LENGTH} 个字符。"
            )
        if _CONTROL_CHAR_PATTERN.search(alias):
            raise WebAPIError(f"设备别名“{alias}”包含不可见控制字符。")
        return alias

    def _validate_did(self, value: Any, alias: str) -> str:
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise WebAPIError(f"“{alias}”的 DID 必须是字符串或整数。")
        did = str(value).strip()
        if not did or len(did) > MAX_DID_LENGTH:
            raise WebAPIError(f"“{alias}”的 DID 长度不合法。")
        if not _DID_PATTERN.fullmatch(did):
            raise WebAPIError(f"“{alias}”的 DID 格式不合法。")
        return did

    def _validate_category(self, value: Any, alias: str) -> str:
        if value in (None, ""):
            return CATEGORY_NONE
        if not isinstance(value, str):
            raise WebAPIError(f"“{alias}”的设备类别必须是字符串。")
        category = value.strip()
        if category not in VALID_CATEGORIES:
            raise WebAPIError(f"“{alias}”使用了不支持的设备类别。")
        return category

    def _parse_mapping_rows(
        self, data: dict[str, Any]
    ) -> tuple[dict[str, str], dict[str, str]]:
        rows = None
        for key in ("mappings", "rows", "devices"):
            if key in data:
                rows = data[key]
                break

        device_map: dict[str, str] = {}
        category_map: dict[str, str] = {}

        if rows is not None:
            if not isinstance(rows, list):
                raise WebAPIError("mappings 必须是数组。")
            if len(rows) > MAX_MAPPING_COUNT:
                raise WebAPIError(f"单次最多保存 {MAX_MAPPING_COUNT} 条设备映射。")
            for index, item in enumerate(rows, 1):
                if not isinstance(item, dict):
                    raise WebAPIError(f"第 {index} 条映射必须是对象。")
                raw_alias = item.get("alias", "")
                raw_did = item.get("did", "")
                raw_category = item.get("category", CATEGORY_NONE)

                # 表格可包含尚未配置别名的云端设备行；空别名表示不保存。
                if isinstance(raw_alias, str) and not raw_alias.strip():
                    if raw_category not in (None, "", CATEGORY_NONE):
                        raise WebAPIError(f"第 {index} 行设置了类别但没有填写别名。")
                    continue

                alias = self._validate_alias(raw_alias)
                if alias in device_map:
                    raise WebAPIError(f"设备别名“{alias}”重复。")
                did = self._validate_did(raw_did, alias)
                category = self._validate_category(raw_category, alias)
                device_map[alias] = did
                category_map[alias] = category
        else:
            raw_device_map = data.get("device_map")
            raw_category_map = data.get("device_category_map", {})
            if isinstance(raw_device_map, str):
                try:
                    raw_device_map = json.loads(raw_device_map)
                except json.JSONDecodeError as exc:
                    raise WebAPIError("device_map 不是合法 JSON。") from exc
            if isinstance(raw_category_map, str):
                try:
                    raw_category_map = json.loads(raw_category_map)
                except json.JSONDecodeError as exc:
                    raise WebAPIError("device_category_map 不是合法 JSON。") from exc
            if not isinstance(raw_device_map, dict):
                raise WebAPIError("请提供 mappings 数组或 device_map 对象。")
            if not isinstance(raw_category_map, dict):
                raise WebAPIError("device_category_map 必须是对象。")
            if len(raw_device_map) > MAX_MAPPING_COUNT:
                raise WebAPIError(f"单次最多保存 {MAX_MAPPING_COUNT} 条设备映射。")

            extra_categories = set(map(str, raw_category_map)) - set(
                map(str, raw_device_map)
            )
            if extra_categories:
                first = sorted(extra_categories)[0]
                raise WebAPIError(f"类别映射“{first}”没有对应的设备别名。")
            for raw_alias, raw_did in raw_device_map.items():
                alias = self._validate_alias(raw_alias)
                if alias in device_map:
                    raise WebAPIError(f"设备别名“{alias}”重复。")
                did = self._validate_did(raw_did, alias)
                category = self._validate_category(
                    raw_category_map.get(raw_alias, CATEGORY_NONE),
                    alias,
                )
                device_map[alias] = did
                category_map[alias] = category

        return device_map, category_map

    def _mapping_change_summary(
        self,
        old_devices: dict[str, str],
        old_categories: dict[str, str],
        new_devices: dict[str, str],
        new_categories: dict[str, str],
    ) -> dict[str, Any]:
        old_aliases = set(old_devices)
        new_aliases = set(new_devices)
        added = [
            {
                "alias": alias,
                "did": new_devices[alias],
                "category": new_categories.get(alias, CATEGORY_NONE),
            }
            for alias in sorted(new_aliases - old_aliases)
        ]
        removed = [
            {
                "alias": alias,
                "did": old_devices[alias],
                "category": old_categories.get(alias, CATEGORY_NONE),
            }
            for alias in sorted(old_aliases - new_aliases)
        ]
        changed = []
        for alias in sorted(old_aliases & new_aliases):
            before = {
                "did": old_devices[alias],
                "category": old_categories.get(alias, CATEGORY_NONE),
            }
            after = {
                "did": new_devices[alias],
                "category": new_categories.get(alias, CATEGORY_NONE),
            }
            if before != after:
                changed.append(
                    {
                        "alias": alias,
                        "before": before,
                        "after": after,
                    }
                )
        return {
            "added": added,
            "removed": removed,
            "changed": changed,
            "counts": {
                "added": len(added),
                "removed": len(removed),
                "changed": len(changed),
                "total": len(new_devices),
            },
        }

    async def save_device_mappings(self):
        data = await self._read_json_body()
        expected_revision = self._expected_revision(data)
        self._ensure_operation_available()
        self._ensure_revision(expected_revision, _MAPPING_REVISION_KEYS)
        new_device_map, new_category_map = self._parse_mapping_rows(data)
        old_device_map, old_category_map, _ = self._mapping_snapshot()

        known_devices = {
            row["did"]
            for row in self._cached_device_rows()
            if not row.get("missing_from_cloud")
        }
        # 已有的离线/失联映射仍可原样保留，避免用户为了修改其他设备
        # 而被迫删除旧配置；仅拦截新加入且未在最近同步结果中出现的 DID。
        unknown_dids = sorted(
            set(new_device_map.values()) - known_devices - set(old_device_map.values())
        )
        if known_devices and unknown_dids:
            raise WebAPIError(
                "以下 DID 不在最近同步的设备清单中："
                + "、".join(unknown_dids[:8])
                + ("…" if len(unknown_dids) > 8 else "")
            )

        # 旧版本允许存在尚未绑定 DID 的类别项。管理页没有足够信息安全
        # 地判断它们是否应删除，因此保存时继续保留，避免静默丢配置。
        preserved_orphan_categories = {
            alias: category
            for alias, category in old_category_map.items()
            if alias not in old_device_map and alias not in new_category_map
        }
        persisted_category_map = {
            **preserved_orphan_categories,
            **new_category_map,
        }
        summary = self._mapping_change_summary(
            old_device_map,
            old_category_map,
            new_device_map,
            persisted_category_map,
        )
        summary["preserved_orphan_categories"] = sorted(preserved_orphan_categories)
        current_control = self._tool_settings()["control_tool"]
        allowed_aliases = set(current_control["allowed_devices"])
        removed_aliases = set(old_device_map) - set(new_device_map)
        rebound_aliases = {
            alias
            for alias in set(old_device_map) & set(new_device_map)
            if old_device_map[alias] != new_device_map[alias]
        }
        control_allowlist_removed = sorted(
            allowed_aliases & (removed_aliases | rebound_aliases)
        )
        summary["control_allowlist_removed"] = control_allowlist_removed
        if data.get("confirm") is not True:
            return json_response(
                {
                    "ok": True,
                    "saved": False,
                    "requires_confirmation": True,
                    "changes": summary,
                    "revision": self._config_revision(_MAPPING_REVISION_KEYS),
                }
            )

        async with self._operation_lock:
            self._ensure_revision(expected_revision, _MAPPING_REVISION_KEYS)
            old_device_raw = self._config.get("device_map", "{}")
            old_category_raw = self._config.get("device_category_map", "{}")
            had_control_config = "control_tool" in self._config
            old_control_config = deepcopy(
                self._config.get("control_tool", {})
            )
            self._config["device_map"] = json.dumps(
                new_device_map,
                ensure_ascii=False,
                indent=2,
            )
            self._config["device_category_map"] = json.dumps(
                persisted_category_map,
                ensure_ascii=False,
                indent=2,
            )
            if control_allowlist_removed:
                self._config["control_tool"] = {
                    **current_control,
                    "allowed_devices": [
                        alias
                        for alias in current_control["allowed_devices"]
                        if alias not in control_allowlist_removed
                    ],
                }
            try:
                await self._persist_config()
            except Exception:
                self._config["device_map"] = old_device_raw
                self._config["device_category_map"] = old_category_raw
                if had_control_config:
                    self._config["control_tool"] = old_control_config
                else:
                    self._config.pop("control_tool", None)
                raise

        return json_response(
            {
                "ok": True,
                "saved": True,
                "changes": summary,
                "control_tool": self._tool_settings()["control_tool"],
                "revision": self._config_revision(_MAPPING_REVISION_KEYS),
                "tool_revision": self._config_revision(_TOOL_REVISION_KEYS),
                "message": "设备别名与类别映射已保存。",
            }
        )

    async def get_readonly_device_status(self):
        self._ensure_operation_available()
        alias = str(request.query.get("alias") or "")
        if not alias:
            raise WebAPIError("缺少 alias 参数。")
        if alias != alias.strip():
            raise WebAPIError("alias 首尾不能包含空格。")

        device_map, _categories, parse_errors = self._mapping_snapshot()
        if parse_errors:
            raise WebAPIError("设备映射配置无效，请先修复配置。", 409)
        if alias not in device_map:
            raise WebAPIError("只能读取 device_map 中已配置的精确别名。", 404)

        renderer = getattr(
            self.plugin,
            "_render_readonly_status_by_alias",
            None,
        )
        if not callable(renderer):
            raise WebAPIError("当前插件版本不支持只读设备状态。", 501)
        async with self._operation_lock:
            text = await renderer(alias)
        return json_response(
            {
                "ok": True,
                "alias": alias,
                "text": str(text or ""),
                "read_only": True,
            }
        )

    # ------------------------------------------------------------------
    # 场景（仅同步与展示，不提供执行）
    # ------------------------------------------------------------------

    async def get_scenes(self):
        state = self._data_manager.load_state()
        return json_response(
            {
                "ok": True,
                "scenes": self._sanitized_scenes(),
                "cache_updated_at": str(state.get("scene_cache_updated_at") or ""),
                "execution_available": False,
            }
        )

    async def sync_scenes(self):
        await self._read_json_body()
        self._ensure_operation_available()
        async with self._operation_lock:
            scenes = await self._client.get_scenes()
        return json_response(
            {
                "ok": True,
                "scenes": self._sanitized_scenes(),
                "count": len(scenes),
                "cache_updated_at": str(
                    self._data_manager.load_state().get("scene_cache_updated_at") or ""
                ),
                "execution_available": False,
                "message": f"已同步 {len(scenes)} 个场景。",
            }
        )

    # ------------------------------------------------------------------
    # Tool 设置
    # ------------------------------------------------------------------

    async def get_tool_settings(self):
        return json_response(
            {
                "ok": True,
                **self._tool_settings(),
                "revision": self._config_revision(_TOOL_REVISION_KEYS),
            }
        )

    async def save_tool_settings(self):
        data = await self._read_json_body()
        expected_revision = self._expected_revision(data)
        supported_fields = {
            "enable_readonly_tool",
            "scene_tool",
            "control_tool",
            "confirm_public_scene_tool",
            "confirm_control_tool",
            "confirm_public_control_tool",
            "revision",
        }
        if set(data) - supported_fields:
            raise WebAPIError("Tool 设置包含不支持的字段。")
        scene = data.get("scene_tool")
        control = data.get("control_tool")
        if not isinstance(scene, dict):
            raise WebAPIError("scene_tool 必须是对象。")
        if set(scene) - {"enable", "admin_only"}:
            raise WebAPIError("scene_tool 包含不支持的配置项。")
        if not isinstance(scene.get("enable"), bool):
            raise WebAPIError("scene_tool.enable 必须是布尔值。")
        if not isinstance(scene.get("admin_only"), bool):
            raise WebAPIError("scene_tool.admin_only 必须是布尔值。")
        if not isinstance(data.get("enable_readonly_tool"), bool):
            raise WebAPIError("enable_readonly_tool 必须是布尔值。")
        if not isinstance(control, dict):
            raise WebAPIError("control_tool 必须是对象。")
        if set(control) - {"enable", "admin_only", "allowed_devices"}:
            raise WebAPIError("control_tool 包含不支持的配置项。")
        if not isinstance(control.get("enable"), bool):
            raise WebAPIError("control_tool.enable 必须是布尔值。")
        if not isinstance(control.get("admin_only"), bool):
            raise WebAPIError("control_tool.admin_only 必须是布尔值。")
        allowed_devices = control.get("allowed_devices")
        if not isinstance(allowed_devices, list):
            raise WebAPIError("control_tool.allowed_devices 必须是数组。")
        if len(allowed_devices) > MAX_MAPPING_COUNT:
            raise WebAPIError(
                f"control_tool.allowed_devices 最多允许 {MAX_MAPPING_COUNT} 项。"
            )

        device_map, _category_map, mapping_errors = self._mapping_snapshot()
        if mapping_errors:
            raise WebAPIError("设备映射格式无效，请先修复设备映射。")
        normalized_allowed = []
        for index, value in enumerate(allowed_devices, 1):
            if not isinstance(value, str):
                if not control["enable"]:
                    continue
                raise WebAPIError(f"设备白名单第 {index} 项必须是字符串。")
            alias = value.strip()
            if (
                not alias
                or len(alias) > MAX_ALIAS_LENGTH
                or _CONTROL_CHAR_PATTERN.search(alias)
            ):
                if not control["enable"]:
                    continue
                raise WebAPIError(f"设备白名单第 {index} 项不是有效别名。")
            if alias not in device_map:
                if not control["enable"]:
                    continue
                raise WebAPIError(f"设备白名单别名“{alias}”不在 device_map 中。")
            if alias not in normalized_allowed:
                normalized_allowed.append(alias)

        if (
            scene["enable"]
            and not scene["admin_only"]
            and data.get("confirm_public_scene_tool") is not True
        ):
            raise WebAPIError(
                "允许非管理员调用场景 Tool 可能触发真实自动化；"
                "确认风险后请提交 confirm_public_scene_tool=true。"
            )
        if control["enable"] and data.get("confirm_control_tool") is not True:
            raise WebAPIError(
                "设备控制 Tool 可以触发真实物理操作；"
                "确认风险后请提交 confirm_control_tool=true。"
            )
        if (
            control["enable"]
            and not control["admin_only"]
            and data.get("confirm_public_control_tool") is not True
        ):
            raise WebAPIError(
                "允许非管理员调用设备控制 Tool 风险很高；"
                "再次确认后请提交 confirm_public_control_tool=true。"
            )

        old_readonly = self._config.get("enable_readonly_tool", False)
        managed_keys = (
            "scene_tool",
            "control_tool",
            "enable_readonly_tool",
            "enable_scene_tool",
            "scene_tool_admin_only",
        )
        original_values = {
            key: deepcopy(self._config[key])
            for key in managed_keys
            if key in self._config
        }
        new_scene = {
            "enable": scene["enable"],
            "admin_only": scene["admin_only"],
        }
        new_control = {
            "enable": control["enable"],
            "admin_only": control["admin_only"],
            "allowed_devices": normalized_allowed,
        }
        changes = {
            "scene_tool": {
                "before": self._tool_settings()["scene_tool"],
                "after": new_scene,
            },
            "enable_readonly_tool": {
                "before": bool(old_readonly),
                "after": data["enable_readonly_tool"],
            },
            "control_tool": {
                "before": self._tool_settings()["control_tool"],
                "after": new_control,
            },
        }

        self._ensure_operation_available()
        async with self._operation_lock:
            self._ensure_revision(expected_revision, _TOOL_REVISION_KEYS)
            self._config["scene_tool"] = new_scene
            self._config["control_tool"] = new_control
            self._config["enable_readonly_tool"] = data["enable_readonly_tool"]
            # 与旧版隐藏字段保持一致，避免降级后出现权限反转。
            self._config["enable_scene_tool"] = new_scene["enable"]
            self._config["scene_tool_admin_only"] = new_scene["admin_only"]
            try:
                await self._persist_config()
            except Exception:
                for key in managed_keys:
                    if key in original_values:
                        self._config[key] = original_values[key]
                    else:
                        self._config.pop(key, None)
                raise

        return json_response(
            {
                "ok": True,
                "saved": True,
                "changes": changes,
                **self._tool_settings(),
                "revision": self._config_revision(_TOOL_REVISION_KEYS),
                "mapping_revision": self._config_revision(_MAPPING_REVISION_KEYS),
            }
        )

    # ------------------------------------------------------------------
    # 诊断
    # ------------------------------------------------------------------

    def _diagnostic_checks(self, login: dict[str, Any]) -> list[dict[str, str]]:
        device_map, category_map, mapping_errors = self._mapping_snapshot()
        rows = self._cached_device_rows()
        scenes = self._sanitized_scenes()
        checks: list[dict[str, str]] = []

        def add(code: str, level: str, title: str, message: str) -> None:
            checks.append(
                {
                    "code": code,
                    "level": level,
                    "title": title,
                    "message": message,
                }
            )

        if login.get("auth_exists") and login.get("last_login_error"):
            add(
                "auth",
                "warning",
                "登录凭证",
                "已检测到米家登录凭证，但近期存在登录或连接异常。",
            )
        elif login.get("auth_exists"):
            add("auth", "success", "登录凭证", "已检测到米家登录凭证。")
        else:
            add(
                "auth",
                "warning",
                "登录凭证",
                "尚未登录米家账号，请先完成扫码授权。",
            )

        if mapping_errors:
            add(
                "mapping_json",
                "error",
                "映射格式",
                "；".join(mapping_errors),
            )
        else:
            add(
                "mapping_json",
                "success",
                "映射格式",
                f"映射格式正常，共 {len(device_map)} 个别名。",
            )

        unknown_categories = [
            alias
            for alias, category in category_map.items()
            if category not in VALID_CATEGORIES
        ]
        orphan_categories = [alias for alias in category_map if alias not in device_map]
        if unknown_categories or orphan_categories:
            messages = []
            if unknown_categories:
                messages.append(f"{len(unknown_categories)} 个别名使用了未知类别")
            if orphan_categories:
                messages.append(f"{len(orphan_categories)} 个类别没有对应设备")
            add(
                "mapping_category",
                "warning",
                "类别映射",
                "，".join(messages) + "。",
            )
        else:
            add(
                "mapping_category",
                "success",
                "类别映射",
                "设备类别映射一致。",
            )

        missing = [row for row in rows if row.get("missing_from_cloud")]
        if missing:
            add(
                "missing_devices",
                "warning",
                "设备匹配",
                f"{len(missing)} 个已配置 DID 不在最近同步的设备清单中。",
            )
        elif rows:
            add(
                "missing_devices",
                "success",
                "设备匹配",
                "已配置设备均可在最近同步清单中找到。",
            )
        else:
            add(
                "missing_devices",
                "info",
                "设备匹配",
                "暂无设备缓存，请先同步设备。",
            )

        if scenes:
            add(
                "scene_cache",
                "success",
                "场景缓存",
                f"已缓存 {len(scenes)} 个场景。",
            )
        else:
            add(
                "scene_cache",
                "info",
                "场景缓存",
                "暂无场景缓存；如需场景 Tool，请先同步。",
            )

        tool_settings = self._tool_settings()
        if (
            tool_settings["scene_tool"]["enable"]
            and not tool_settings["scene_tool"]["admin_only"]
        ):
            add(
                "scene_tool_permission",
                "warning",
                "场景 Tool 权限",
                "场景 Tool 允许非管理员调用，可能触发真实自动化。",
            )
        else:
            add(
                "scene_tool_permission",
                "success",
                "场景 Tool 权限",
                "场景 Tool 已关闭或仅管理员可调用。",
            )

        control_tool = tool_settings["control_tool"]
        if control_tool["enable"] and not control_tool["admin_only"]:
            add(
                "control_tool_permission",
                "warning",
                "设备控制 Tool 权限",
                "设备控制 Tool 允许非管理员调用，存在真实物理操作风险。",
            )
        elif control_tool["enable"] and not control_tool["allowed_devices"]:
            add(
                "control_tool_allowlist",
                "warning",
                "设备控制 Tool 白名单",
                "设备控制 Tool 已开启但白名单为空，当前会失败关闭。",
            )
        elif control_tool["enable"]:
            add(
                "control_tool_permission",
                "success",
                "设备控制 Tool 权限",
                f"设备控制 Tool 仅管理员可用，白名单含 "
                f"{len(control_tool['allowed_devices'])} 台设备。",
            )
        else:
            add(
                "control_tool_permission",
                "success",
                "设备控制 Tool 权限",
                "设备控制 Tool 已关闭。",
            )

        error_fields = (
            ("last_login_error", "最近登录异常"),
            ("last_shared_error", "共享设备异常"),
            ("last_scene_error", "最近场景异常"),
            ("last_control_error", "最近设备控制异常"),
        )
        active_errors = [
            f"{title}：{_redact_text(login.get(key), limit=160)}"
            for key, title in error_fields
            if login.get(key)
        ]
        if active_errors:
            add(
                "recent_errors",
                "warning",
                "近期错误",
                "；".join(active_errors),
            )
        else:
            add(
                "recent_errors",
                "success",
                "近期错误",
                "状态缓存中没有近期错误。",
            )

        return checks

    async def get_diagnostics(self):
        login = await self._client.get_login_status()
        checks = self._diagnostic_checks(login)
        return json_response(
            {
                "ok": True,
                "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "checks": checks,
                "summary": {
                    level: sum(1 for item in checks if item["level"] == level)
                    for level in ("success", "info", "warning", "error")
                },
                "privacy": {
                    "credentials_included": False,
                    "login_url_included": False,
                    "device_control_performed": False,
                    "scene_execution_performed": False,
                },
            }
        )

    async def run_diagnostics(self):
        data = await self._read_json_body()
        unsupported = set(data) - {"include_cached_errors"}
        if unsupported:
            raise WebAPIError("诊断接口包含不支持的参数。")
        # 诊断只检查登录状态和本地缓存，不借机调用设备/场景控制接口。
        return await self.get_diagnostics()
