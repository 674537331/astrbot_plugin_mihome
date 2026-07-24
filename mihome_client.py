# -*- coding: utf-8 -*-
import base64
import re
import os
import sys
import asyncio
import json
import math
import logging
import functools
import inspect
import subprocess
import tempfile
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Dict, Callable, Awaitable, Union, Any, Optional, List
from urllib import parse

import requests

from astrbot.api import logger

# mijiaAPI 4.1.3 的 DEBUG 日志会输出请求体与认证数据。必须在导入并创建
# 任何上游实例前抑制，避免 DID、动作文本及登录令牌进入 AstrBot 日志。
logging.getLogger("mijiaAPI").setLevel(logging.WARNING)

try:
    from mijiaAPI import (  # noqa: E402 - security guard must run before import
        mijiaAPI,
        mijiaDevice,
        LoginError,
        DeviceNotFoundError,
        DeviceSetError,
        DeviceGetError,
        DeviceActionError,
        APIError,
    )
    from mijiaAPI.devices import (  # noqa: E402
        DevAction,
        DevProp,
    )
except (ImportError, OSError) as exc:
    if "ARC4" in str(exc).upper():
        raise RuntimeError(
            "pycryptodome 的 ARC4 原生模块加载失败。"
            "请在 AstrBot 使用的 Python 环境中重新安装 pycryptodome==3.23.0。"
        ) from exc
    raise

try:
    from requests.exceptions import (
        RequestException,
        SSLError,
        Timeout as RequestsTimeout,
    )
except ImportError:
    class RequestException(Exception):
        pass

    class SSLError(Exception):
        pass

    class RequestsTimeout(RequestException):
        pass

from .data_manager import MiHomeDataManager  # noqa: E402

LOGIN_IDLE = "idle"
LOGIN_RUNNING = "running"
HTTP_REQUEST_TIMEOUT = (8.0, 20.0)
LOGIN_PROCESS_STOP_TIMEOUT = 5.0
SPEC_FETCH_TIMEOUT = 25.0
PREPARED_DEVICE_CACHE_TTL = 60.0
PREPARED_DEVICE_CACHE_MAX_SIZE = 64
DEVICE_MODEL_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}")
WORKER_QR_PAYLOAD_START = "[WORKER_QR_LOGIN_URL]"
WORKER_QR_PAYLOAD_END = "[/WORKER_QR_LOGIN_URL]"
MAX_LOGIN_URL_LENGTH = 8192
DEVICE_SYNC_ERROR_PREFIXES = (
    "拉取云端设备列表超时",
    "鉴权失效",
    "SSL 通信异常",
    "网络异常:",
    "云端接口异常",
    "系统级同步异常:",
)


class MiHomeClientError(Exception):
    pass


class MiHomeAuthError(MiHomeClientError):
    pass


class MiHomeControlError(MiHomeClientError):
    pass


class MiHomeSceneError(MiHomeClientError):
    pass


class MiHomeClient:
    def __init__(self, data_manager: MiHomeDataManager):
        self.data_manager = data_manager
        self.api = None
        self._api_lock = asyncio.Lock()
        self._login_status = LOGIN_IDLE
        self._login_process: Optional[asyncio.subprocess.Process] = None
        self._login_generation = 0
        self._pending_login_state: Optional[Dict[str, Any]] = None
        self._pending_login_state_baseline: Optional[Dict[str, Any]] = None
        self._prepared_device_cache = OrderedDict()
        self._worker_script = os.path.join(os.path.dirname(__file__), "_login_worker.py")
        self._spec_worker_script = os.path.join(
            os.path.dirname(__file__),
            "_spec_worker.py",
        )
        if not self.data_manager.auth_storage_is_secure():
            self.data_manager.update_state(
                last_login_error=(
                    "登录凭证存储路径安全检查失败，请检查数据目录权限"
                ),
            )
            return
        try:
            self._initialize_api()
        except MiHomeClientError as exc:
            logger.error(
                f"[MiHome] 登录凭证存储检查失败: {type(exc).__name__}"
            )
            self.data_manager.update_state(last_login_error=str(exc))
        except (
            json.JSONDecodeError,
            UnicodeDecodeError,
            OSError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            logger.error(
                f"[MiHome] 登录凭证初始化失败: {type(exc).__name__}"
            )
            self.data_manager.update_state(
                last_login_error=(
                    "登录凭证文件无法读取，请在米家管理中退出登录后重新授权"
                ),
            )

    def _initialize_api(self) -> None:
        self._clear_prepared_device_cache()
        self.api = None
        if not self.data_manager.auth_storage_is_secure():
            raise MiHomeClientError(
                "登录凭证存储路径安全检查失败，请检查数据目录权限"
            )
        api = mijiaAPI(self.data_manager.get_auth_path())
        self._configure_api_instance(api)
        self.api = api

    def _clear_prepared_device_cache(self) -> None:
        cache = getattr(self, "_prepared_device_cache", None)
        if cache is not None:
            cache.clear()

    def _get_prepared_device_cache(self) -> OrderedDict:
        cache = getattr(self, "_prepared_device_cache", None)
        if cache is None:
            cache = OrderedDict()
            self._prepared_device_cache = cache
        return cache

    def _configure_api_session(self, api: Any = None) -> None:
        """为上游同步 Session 设置默认连接与读取超时。"""

        target_api = api if api is not None else self.api
        session = getattr(target_api, "session", None)
        request = getattr(session, "request", None)
        if (
            session is None
            or not callable(request)
            or getattr(session, "_astrbot_timeout_configured", False)
        ):
            return
        session.request = functools.partial(
            request,
            timeout=HTTP_REQUEST_TIMEOUT,
        )
        session._astrbot_timeout_configured = True

    @staticmethod
    def _get_location_with_timeout(api: Any) -> Dict[str, Any]:
        """按上游 4.1.3 语义刷新位置票据，并为两次请求设置上限。"""

        headers = {
            "User-Agent": api.user_agent,
            "Connection": "keep-alive",
            "Accept-Encoding": "gzip",
            "Content-Type": "application/x-www-form-urlencoded",
            "Cookie": (
                f"deviceId={api.deviceId};"
                f"pass_o={api.pass_o};"
                f"passToken={api.auth_data.get('passToken', '')};"
                f"userId={api.auth_data.get('userId', '')};"
                f"cUserId={api.auth_data.get('cUserId', '')};"
                f"uLocale={api.locale};"
            ),
        }
        with requests.Session() as service_session:
            service_ret = service_session.get(
                api.service_login_url,
                headers=headers,
                timeout=HTTP_REQUEST_TIMEOUT,
            )
        service_data = api._handle_ret(service_ret, verify_code=False)
        location = service_data["location"]
        if service_data["code"] == 0:
            ret = api.session.get(
                location,
                timeout=HTTP_REQUEST_TIMEOUT,
            )
            if ret.status_code == 200 and ret.text == "ok":
                api.auth_data.update(api.session.cookies.get_dict())
                api.auth_data["ssecurity"] = service_data["ssecurity"]
                return {"code": 0, "message": "刷新Token成功"}
        location_data = parse.parse_qs(parse.urlparse(location).query)
        return {key: value[0] for key, value in location_data.items()}

    def _configure_api_instance(self, api: Any) -> None:
        """仅适配当前上游实例，避免修改进程级 requests 行为。"""

        init_session = getattr(api, "_init_session", None)
        if callable(init_session) and not getattr(
            api,
            "_astrbot_init_session_wrapped",
            False,
        ):
            original_init_session = init_session

            def init_session_with_timeout(*args: Any, **kwargs: Any) -> Any:
                result = original_init_session(*args, **kwargs)
                self._configure_api_session(api)
                return result

            api._init_session = init_session_with_timeout
            api._astrbot_init_session_wrapped = True

        get_location = getattr(api, "_get_location", None)
        if callable(get_location) and not getattr(
            api,
            "_astrbot_get_location_wrapped",
            False,
        ):
            api._get_location = functools.partial(
                self._get_location_with_timeout,
                api,
            )
            api._astrbot_get_location_wrapped = True

        self._configure_api_session(api)

    async def _run_sync_call(
        self,
        func: Callable[..., Any],
        *args: Any,
        warn_after: float,
        operation: str,
        **kwargs: Any,
    ) -> Any:
        """等待同步调用真正结束，避免超时后后台线程继续操作设备。"""

        task = asyncio.create_task(
            asyncio.to_thread(func, *args, **kwargs),
        )
        try:
            return await asyncio.wait_for(
                asyncio.shield(task),
                timeout=warn_after,
            )
        except asyncio.TimeoutError:
            if task.done():
                return await task
            logger.warning(
                f"[MiHome] {operation} 响应较慢，将保持串行等待以避免重复操作。"
            )
            return await task
        except asyncio.CancelledError:
            logger.warning(
                f"[MiHome] {operation} 等待被取消，将先回收正在运行的同步调用。"
            )
            try:
                await asyncio.shield(task)
            except Exception as exc:
                logger.debug(
                    f"[MiHome] {operation} 取消后回收异常: {type(exc).__name__}"
                )
            raise

    async def _stop_login_process_locked(
        self,
        process: Optional[asyncio.subprocess.Process] = None,
    ) -> bool:
        """终止并确认登录子进程退出；调用方必须持有 ``_api_lock``。"""

        target = process or self._login_process
        if target is None:
            return True

        if target.returncode is None:
            try:
                target.kill()
            except ProcessLookupError:
                pass
            except Exception as exc:
                logger.warning(
                    f"[MiHome] 中止登录进程失败: {type(exc).__name__}"
                )

            try:
                await asyncio.wait_for(
                    asyncio.shield(target.wait()),
                    timeout=LOGIN_PROCESS_STOP_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.error("[MiHome] 登录进程在终止后仍未退出")
            except Exception as exc:
                logger.warning(
                    f"[MiHome] 等待登录进程退出失败: {type(exc).__name__}"
                )

        stopped = target.returncode is not None
        if stopped and self._login_process is target:
            self._login_process = None
        return stopped

    def _finalize_login_generation_locked(
        self,
        process: Optional[asyncio.subprocess.Process],
        generation: int,
    ) -> None:
        """仅允许当前登录轮次收尾全局状态；调用方必须持有 ``_api_lock``。"""

        if generation != getattr(self, "_login_generation", generation):
            return
        process_alive = bool(
            process is not None and process.returncode is None
        )
        if self._login_process is process and not process_alive:
            self._login_process = None
        self._login_status = (
            LOGIN_RUNNING if process_alive else LOGIN_IDLE
        )

    def _cleanup_new_login_auth(self, auth_existed_before: bool) -> bool:
        """只回滚当前登录尝试新建的凭证，不触碰进入本轮前已有的文件。"""

        if auth_existed_before or not self.data_manager.auth_exists():
            return True
        return self.data_manager.clear_auth_file()

    async def _abort_login_attempt(
        self,
        process: Optional[asyncio.subprocess.Process],
        generation: int,
        auth_existed_before: bool,
    ) -> tuple[bool, bool]:
        """停止本轮 worker；确认退出后再回滚本轮新建的凭证。"""

        async with self._api_lock:
            stopped = (
                True
                if process is None
                else await self._stop_login_process_locked(process)
            )
            cleanup_ok = True
            if (
                stopped
                and generation == getattr(
                    self,
                    "_login_generation",
                    generation,
                )
            ):
                cleanup_ok = self._cleanup_new_login_auth(
                    auth_existed_before
                )
            return stopped, cleanup_ok

    def _check_idle(self):
        if self._login_status != LOGIN_IDLE:
            raise MiHomeClientError("登录沙盒正在运行中。")

    def _check_api(self):
        if not self.api:
            raise MiHomeClientError(
                "米家登录凭证当前不可用，请在米家管理中退出登录后重新授权。"
            )
        if not self.data_manager.auth_exists():
            raise MiHomeAuthError("login_required")

    def _normalize_key(self, key: str) -> str:
        return str(key).strip().lower().replace("-", "_")

    @staticmethod
    def _parse_rw_field(rw: Any) -> tuple[bool, bool]:
        """兼容 mijiaAPI 不同版本的读写权限表示。"""

        if isinstance(rw, str):
            token = rw.strip().lower()
            if token in {"r", "read"}:
                return True, False
            if token in {"w", "write"}:
                return False, True
            if token in {"rw", "wr", "readwrite", "read_write", "read-write"}:
                return True, True
            return False, False

        if isinstance(rw, (list, tuple, set)):
            tokens = {str(item).strip().lower() for item in rw}
            readable = bool(tokens & {"r", "read"})
            writable = bool(tokens & {"w", "write"})
            return readable, writable

        return False, False

    @staticmethod
    def _validate_action_response(response: Any) -> bool:
        """校验动作结果；返回 False 表示网关已接收但结果无法确认。"""

        confirmed = True
        saw_code = False
        candidates: List[Any] = [response]
        if isinstance(response, dict):
            nested = response.get("result")
            if isinstance(nested, list):
                candidates.extend(nested)
            elif isinstance(nested, dict):
                candidates.append(nested)
        elif isinstance(response, list):
            candidates.extend(response)

        for item in candidates:
            if not isinstance(item, dict) or "code" not in item:
                continue
            try:
                code = int(item["code"])
            except (TypeError, ValueError):
                continue
            saw_code = True
            if code == 1:
                confirmed = False
            elif code != 0:
                raise MiHomeControlError(f"cloud_rejected:{code}")
        return confirmed if saw_code else False

    def _build_property_method(
        self,
        device: Any,
        did: str,
        prop: str,
        value: Any,
    ) -> Dict[str, Any]:
        """按 mijiaAPI 4.1.3 的 DevProp 规则校验并构造属性请求。"""

        prop_list = getattr(device, "prop_list", {})
        if not isinstance(prop_list, dict) or prop not in prop_list:
            raise ValueError("属性不在设备运行时规格中")
        prop_info = prop_list[prop]
        _readable, writable = self._parse_rw_field(
            getattr(prop_info, "rw", None)
        )
        if not writable:
            raise ValueError("属性不可写")

        value_type = str(getattr(prop_info, "type", "") or "").lower()
        if value_type == "bool":
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in {"true", "1"}:
                    value = True
                elif lowered in {"false", "0"}:
                    value = False
                else:
                    raise ValueError("布尔值格式无效")
            elif isinstance(value, int) and not isinstance(value, bool):
                if value not in {0, 1}:
                    raise ValueError("布尔值格式无效")
                value = bool(value)
            elif not isinstance(value, bool):
                raise ValueError("布尔值格式无效")
        elif value_type in {"int", "uint"}:
            if isinstance(value, bool):
                raise ValueError("整数属性不能使用布尔值")
            if isinstance(value, str):
                raw_value = value.strip()
                if not re.fullmatch(r"[+-]?\d+", raw_value):
                    raise ValueError("整数属性必须使用完整整数")
                value = int(raw_value)
            elif isinstance(value, float):
                if not math.isfinite(value) or not value.is_integer():
                    raise ValueError("整数属性必须使用完整整数")
                value = int(value)
            elif not isinstance(value, int):
                try:
                    converted = int(value)
                    is_exact = value == converted
                except (TypeError, ValueError, OverflowError):
                    raise ValueError("整数属性必须使用完整整数") from None
                if not is_exact:
                    raise ValueError("整数属性必须使用完整整数")
                value = converted
            if value_type == "uint" and value < 0:
                raise ValueError("无符号整数不能为负数")
        elif value_type == "float":
            if isinstance(value, bool):
                raise ValueError("浮点属性不能使用布尔值")
            value = float(value)
            if not math.isfinite(value):
                raise ValueError("浮点属性必须是有限数值")
        elif value_type == "string":
            if not isinstance(value, str):
                raise ValueError("字符串属性必须使用字符串值")
        else:
            raise ValueError("设备规格包含不支持的属性类型")

        range_info = getattr(prop_info, "range", None) or []
        if value_type in {"int", "uint", "float"} and len(range_info) >= 2:
            minimum, maximum = range_info[0], range_info[1]
            if value < minimum or value > maximum:
                raise ValueError("属性值超出设备规格范围")
            if len(range_info) >= 3 and range_info[2]:
                step = range_info[2]
                quotient = (value - minimum) / step
                if not math.isclose(
                    quotient,
                    round(quotient),
                    rel_tol=0.0,
                    abs_tol=1e-9,
                ):
                    raise ValueError("属性值不符合设备规格步长")

        value_list = getattr(prop_info, "value_list", None) or []
        if value_list:
            allowed_values = [
                item.get("value")
                for item in value_list
                if isinstance(item, dict) and "value" in item
            ]
            if value not in allowed_values:
                raise ValueError("属性值不在设备规格枚举中")

        method = dict(getattr(prop_info, "method", {}) or {})
        if not method:
            raise ValueError("设备规格缺少属性调用方法")
        method["did"] = did
        method["value"] = value
        return method

    def _unit_suffix(self, unit: Any) -> str:
        mapping = {
            "percentage": "%",
            "celsius": "°C",
            "lux": " lux",
            "rpm": " rpm",
            "minutes": " 分钟",
            "days": " 天",
            "hours": " 小时",
            "seconds": " 秒",
            "μg/m3": " μg/m3",
            "ug/m3": " μg/m3",
        }
        if unit in mapping:
            return mapping[unit]
        if unit in ("none", "", None):
            return ""
        return f" {unit}"

    @staticmethod
    def _read_device_spec_cache(cache_path: Path) -> Optional[Dict[str, Any]]:
        if cache_path.is_symlink():
            raise MiHomeClientError("设备规格缓存路径类型异常")
        if not cache_path.exists():
            return None
        if not cache_path.is_file():
            raise MiHomeClientError("设备规格缓存路径类型异常")
        try:
            with cache_path.open("r", encoding="utf-8") as file:
                spec = json.load(file)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return None
        if (
            not isinstance(spec, dict)
            or not isinstance(spec.get("properties"), list)
            or not isinstance(spec.get("actions"), list)
        ):
            return None
        return spec

    def _load_or_fetch_device_spec(self, model: str) -> Dict[str, Any]:
        """在可终止子进程中获取公开设备规格，再原子写入上游缓存。"""

        model = str(model or "").strip()
        if not DEVICE_MODEL_PATTERN.fullmatch(model):
            raise MiHomeClientError("设备型号格式异常，无法读取设备规格")

        cache_dir = Path(self.data_manager.get_auth_path()).parent
        cache_path = cache_dir / f"{model}.json"
        cached = self._read_device_spec_cache(cache_path)
        if cached is not None:
            return cached

        worker_script = getattr(
            self,
            "_spec_worker_script",
            os.path.join(os.path.dirname(__file__), "_spec_worker.py"),
        )
        creation_flags = (
            subprocess.CREATE_NO_WINDOW
            if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW")
            else 0
        )
        try:
            with tempfile.TemporaryDirectory(
                prefix=".spec-",
                dir=cache_dir,
            ) as temp_dir:
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-u",
                        worker_script,
                        model,
                        temp_dir,
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=SPEC_FETCH_TIMEOUT,
                    check=False,
                    creationflags=creation_flags,
                )
                if completed.returncode != 0:
                    raise MiHomeClientError(
                        "设备规格获取失败，请稍后重试"
                    )

                temp_spec_path = Path(temp_dir) / f"{model}.json"
                spec = self._read_device_spec_cache(temp_spec_path)
                if spec is None:
                    raise MiHomeClientError("设备规格返回内容无效")
                temp_spec_path.chmod(0o600)
                os.replace(temp_spec_path, cache_path)
                cache_path.chmod(0o600)
                return spec
        except subprocess.TimeoutExpired as exc:
            raise MiHomeClientError("设备规格获取超时，请稍后重试") from exc
        except MiHomeClientError:
            raise
        except OSError as exc:
            raise MiHomeClientError("设备规格缓存写入失败") from exc

    def _prepare_device_sync(self, did: str):
        # 业务请求前不能调用 login()；凭证失效时，上游 login() 可能进入
        # 120 秒扫码长轮询。这里只使用有界的设备列表请求和规格子进程。
        target_did = str(did).strip()
        cache = self._get_prepared_device_cache()
        cached = cache.get(target_did)
        if cached is not None:
            cached_at, cached_api, cached_device = cached
            if (
                cached_api is self.api
                and time.monotonic() - cached_at
                < PREPARED_DEVICE_CACHE_TTL
            ):
                cache.move_to_end(target_did)
                return cached_device
            cache.pop(target_did, None)

        devices = self.api.get_devices_list()
        if not isinstance(devices, list):
            devices = []
        matches = [
            item
            for item in devices
            if isinstance(item, dict)
            and str(item.get("did", "")).strip() == target_did
        ]
        if not matches:
            get_shared_devices = getattr(
                self.api,
                "get_shared_devices_list",
                None,
            )
            if callable(get_shared_devices):
                shared_devices = get_shared_devices()
                if not isinstance(shared_devices, list):
                    shared_devices = []
                matches = [
                    item
                    for item in shared_devices
                    if isinstance(item, dict)
                    and str(item.get("did", "")).strip() == target_did
                ]
        if not matches:
            raise DeviceNotFoundError(did)

        device_row = matches[0]
        model = str(device_row.get("model", "")).strip()
        spec = self._load_or_fetch_device_spec(model)

        device = object.__new__(mijiaDevice)
        device.api = self.api
        device.did = str(did)
        device.model = model
        device.name = (
            str(device_row.get("name", "")).strip()
            or str(spec.get("name", "")).strip()
        )
        device.sleep_time = 1.0
        device.prop_list = {}
        for prop in spec.get("properties", []):
            if not isinstance(prop, dict) or not prop.get("name"):
                continue
            prop_obj = DevProp(prop)
            base_name = str(prop["name"])
            prop_name = base_name
            if prop_name in device.prop_list:
                method = dict(prop.get("method", {}) or {})
                prop_name = (
                    f"{base_name}-{method.get('siid', 'x')}-"
                    f"{method.get('piid', 'x')}"
                )
            device.prop_list[prop_name] = prop_obj
            if "-" in prop_name:
                device.prop_list.setdefault(
                    prop_name.replace("-", "_"),
                    prop_obj,
                )

        device.action_list = {}
        for action in spec.get("actions", []):
            if not isinstance(action, dict) or not action.get("name"):
                continue
            action_obj = DevAction(action)
            base_name = str(action["name"])
            action_name = base_name
            if action_name in device.action_list:
                method = dict(action.get("method", {}) or {})
                action_name = (
                    f"{base_name}-{method.get('siid', 'x')}-"
                    f"{method.get('aiid', 'x')}"
                )
            device.action_list[action_name] = action_obj

        cache[target_did] = (time.monotonic(), self.api, device)
        cache.move_to_end(target_did)
        while len(cache) > PREPARED_DEVICE_CACHE_MAX_SIZE:
            cache.popitem(last=False)
        return device

    def _normalize_scene_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        scene_id = str(
            item.get("scene_id")
            or item.get("id")
            or item.get("sceneId")
            or item.get("sceneid")
            or ""
        ).strip()

        scene_name = str(
            item.get("name")
            or item.get("scene_name")
            or item.get("sceneName")
            or item.get("title")
            or ""
        ).strip()

        home_id = str(
            item.get("home_id")
            or item.get("homeId")
            or item.get("homeid")
            or item.get("home")
            or ""
        ).strip()

        home_name = str(
            item.get("home_name")
            or item.get("homeName")
            or item.get("home_name_cn")
            or item.get("family_name")
            or item.get("familyName")
            or ""
        ).strip()

        return {
            "scene_id": scene_id,
            "scene_name": scene_name,
            "home_id": home_id,
            "home_name": home_name,
        }

    def _save_scene_cache(self, scenes: List[Dict[str, Any]]) -> None:
        saved = self.data_manager.update_state(
            scenes=scenes,
            scene_cache_updated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            last_scene_error="",
        )
        if saved is False:
            raise MiHomeClientError("场景缓存保存失败，请检查插件数据目录")

    def _extract_qr_url_from_buffer(self, buffer_text: str) -> str:
        """
        从登录沙盒的结构化 stdout 消息中提取真正的二维码载荷。

        上游 ``loginUrl`` 是应编码进二维码的登录地址；``qr`` 只是已经
        生成好的二维码图片地址。只接受 worker 明确标记的 ``loginUrl``，
        避免再次把图片地址编码成二维码。
        """
        if not buffer_text:
            return ""

        compact = buffer_text.replace("\r", "").replace("\n", "")
        match = re.search(
            re.escape(WORKER_QR_PAYLOAD_START)
            + r"([A-Za-z0-9_-]{1,12288}={0,2})"
            + re.escape(WORKER_QR_PAYLOAD_END),
            compact,
        )
        if not match:
            return ""

        try:
            decoded = base64.urlsafe_b64decode(
                match.group(1).encode("ascii")
            ).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return ""
        login_url = decoded.strip()
        if (
            not login_url
            or len(login_url) > MAX_LOGIN_URL_LENGTH
            or any(char in login_url for char in "\r\n\x00")
        ):
            return ""
        try:
            parsed = parse.urlparse(login_url)
            hostname = (parsed.hostname or "").lower()
            port = parsed.port
        except ValueError:
            return ""
        if (
            parsed.scheme.lower() != "https"
            or not (
                hostname == "account.xiaomi.com"
                or hostname.endswith(".account.xiaomi.com")
            )
            or port not in (None, 443)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path != "/longPolling/login"
            or not parsed.query
            or parsed.fragment
        ):
            return ""
        return login_url

    def _redact_login_output(self, text: str) -> str:
        """隐藏登录输出中的二维码链接和认证参数。"""

        raw = str(text or "")
        # 登录 URL 可能被子进程拆成多行。只要能从整体缓冲区恢复出它，
        # 就隐藏整段原始输出，避免任何续行查询参数绕过逐行正则。
        if (
            WORKER_QR_PAYLOAD_START in raw
            or self._extract_qr_url_from_buffer(raw)
        ):
            sanitized = "[米家登录输出已隐藏]"
        else:
            sanitized = re.sub(
                r"https://(?:[A-Za-z0-9-]+\.)*account\.xiaomi\.com/"
                r"(?:longPolling/login|pass/qr/login)\?[^\s]+",
                "[米家登录链接已隐藏]",
                raw,
                flags=re.IGNORECASE,
            )
        return re.sub(
            r"(?i)(ticket|serviceToken|passToken|ssecurity|psecurity|nonce|"
            r"pass_o|deviceId|userId|token|ua)"
            r"([\"']?\s*[:=]\s*[\"']?)([^,\s\"'&}]+)",
            r"\1\2[已隐藏]",
            sanitized,
        )

    def _clear_pending_login_state(self) -> None:
        self._pending_login_state = None
        self._pending_login_state_baseline = None

    def _retry_pending_login_state(
        self,
        state: Dict[str, Any],
    ) -> tuple[Dict[str, Any], str, bool]:
        pending = getattr(self, "_pending_login_state", None)
        baseline = getattr(self, "_pending_login_state_baseline", None)
        if not isinstance(pending, dict) or not isinstance(baseline, dict):
            return state, "", False
        if state != baseline:
            self._clear_pending_login_state()
            return state, "", False

        target_state = dict(baseline)
        target_state.update(pending)
        result, observed_state = self.data_manager.compare_and_update_state(
            baseline,
            **pending,
        )
        if result == "saved":
            self._clear_pending_login_state()
            return observed_state, "", True
        if result == "changed":
            self._clear_pending_login_state()
            return observed_state, "", observed_state == target_state

        if observed_state not in (baseline, target_state):
            self._clear_pending_login_state()
            return observed_state, "", False
        self._pending_login_state_baseline = observed_state
        return (
            observed_state,
            "登录凭证已保存，但插件状态记录保存失败，请检查数据目录权限",
            False,
        )

    async def get_login_status(
        self,
        state: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if state is None:
            state = self.data_manager.load_state()
        (
            state,
            credential_storage_error,
            login_state_recovered,
        ) = self._retry_pending_login_state(dict(state))
        return {
            "auth_exists": self.data_manager.auth_exists(),
            "login_in_progress": self._login_status != LOGIN_IDLE,
            "last_login_at": state.get("last_login_at", ""),
            "last_login_error": state.get("last_login_error", ""),
            "credential_storage_error": credential_storage_error,
            "login_state_recovered": login_state_recovered,
            "last_shared_error": state.get("last_shared_error", ""),
            "last_control_error": state.get("last_control_error", ""),
            "last_control_device": state.get("last_control_device", ""),
            "last_scene_error": state.get("last_scene_error", ""),
            "last_scene_name": state.get("last_scene_name", ""),
            "scene_cache_updated_at": state.get("scene_cache_updated_at", ""),
        }

    async def logout(self) -> bool:
        async with self._api_lock:
            if not await self._stop_login_process_locked():
                self._login_status = LOGIN_RUNNING
                self.data_manager.update_state(
                    last_login_error="登录进程仍在运行，本地凭证尚未清理",
                )
                raise MiHomeClientError(
                    "登录进程未能安全停止，请稍后重试登出"
                )

            self._login_status = LOGIN_IDLE
            ok = self.data_manager.clear_auth_file()
            if not ok:
                self.data_manager.update_state(
                    last_login_error="本地登录凭证移除失败，请检查文件权限",
                )
                raise MiHomeClientError(
                    "本地登录凭证移除失败，请检查文件权限后重试"
                )
            self._clear_pending_login_state()
            previous_api = self.api
            self.api = None
            previous_session = getattr(previous_api, "session", None)
            close_session = getattr(previous_session, "close", None)
            if callable(close_session):
                try:
                    close_session()
                except Exception as exc:
                    logger.debug(
                        f"[MiHome] 关闭旧会话失败: {type(exc).__name__}"
                    )
            self._initialize_api()

            state_cleared = self.data_manager.update_state(
                last_login_at="",
                last_login_error="",
                last_shared_error="",
                last_control_error="",
                last_control_device="",
                last_scene_error="",
                last_scene_name="",
                scenes=[],
                scene_cache_updated_at="",
                did_to_name={},
                did_to_model={},
            )
            if state_cleared is False:
                raise MiHomeClientError(
                    "登录凭证已移除，但本地账号缓存清理失败，"
                    "请检查插件数据目录权限"
                )
            if self.data_manager.clear_state_backups() is False:
                raise MiHomeClientError(
                    "登录凭证已移除，但旧账号状态备份清理失败，"
                    "请检查插件数据目录权限"
                )
            return ok

    async def login(
        self,
        qr_callback: Union[Callable[[str], Awaitable[None]], Callable[[str], None]],
    ) -> Dict[str, Any]:
        if self._login_status != LOGIN_IDLE:
            return {"status": "in_progress"}
        if not self.data_manager.auth_storage_is_secure():
            message = "登录凭证存储路径安全检查失败，请检查数据目录权限"
            self.data_manager.update_state(last_login_error=message)
            return {"status": "error", "message": message}
        auth_existed_before = self.data_manager.auth_exists()

        logger.info(f"[MiHome] 启动登录沙盒进程 -> {self._worker_script}")
        self._clear_pending_login_state()
        self._login_generation = getattr(self, "_login_generation", 0) + 1
        login_generation = self._login_generation
        self._login_status = LOGIN_RUNNING
        qr_found = False
        full_buffer = ""
        proc: Optional[asyncio.subprocess.Process] = None

        try:
            async with self._api_lock:
                proc = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-u",
                    self._worker_script,
                    self.data_manager.get_auth_path(),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                self._login_process = proc
                if proc.stdout is None:
                    raise MiHomeClientError("Stdout 管道损坏")

            async def read_stdout():
                nonlocal qr_found, full_buffer
                while True:
                    chunk = await proc.stdout.read(256)
                    if not chunk:
                        break

                    text = chunk.decode("utf-8", errors="replace")
                    full_buffer = (full_buffer + text)[-16384:]

                    if text.strip():
                        # 登录输出可能在任意字节边界拆分二维码票据。即使逐行
                        # 脱敏也有跨 chunk 泄露风险，因此日志只记录固定进度。
                        logger.debug(
                            "[Sandbox] 登录子进程有新输出，内容已隐藏。"
                        )

                    if not qr_found:
                        url = self._extract_qr_url_from_buffer(full_buffer)
                        if url:
                            qr_found = True
                            logger.info("[MiHome] 成功提取登录二维码载荷。")
                            callback_result = qr_callback(url)
                            if inspect.isawaitable(callback_result):
                                await callback_result

            try:
                await asyncio.wait_for(
                    asyncio.gather(proc.wait(), read_stdout()),
                    timeout=120.0,
                )
            except asyncio.TimeoutError:
                stopped, cleanup_ok = await self._abort_login_attempt(
                    proc,
                    login_generation,
                    auth_existed_before,
                )
                if not stopped:
                    msg = "登录进程未能安全停止，请在米家管理中重试登出"
                    self.data_manager.update_state(last_login_error=msg)
                    return {"status": "error", "message": msg}
                if not cleanup_ok:
                    msg = "授权已超时，但临时凭证清理失败，请检查文件权限"
                    self.data_manager.update_state(last_login_error=msg)
                    return {"status": "error", "message": msg}
                msg = "授权确认已超时 (120秒)" if qr_found else "超时未能提取登录链接"
                self.data_manager.update_state(last_login_error=msg)
                return {"status": "timeout" if qr_found else "qrcode_not_found"}

            async with self._api_lock:
                if proc.returncode == 0:
                    if not self.data_manager.harden_auth_file():
                        cleanup_ok = self._cleanup_new_login_auth(
                            auth_existed_before
                        )
                        message = (
                            "登录凭证文件安全检查失败，请检查数据目录权限"
                            if cleanup_ok
                            else "登录凭证安全检查与临时文件清理均失败，"
                            "请检查数据目录权限"
                        )
                        self.data_manager.update_state(last_login_error=message)
                        return {"status": "error", "message": message}
                    login_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    pending_state = {
                        "last_login_at": login_at,
                        "last_login_error": "",
                    }
                    state_saved = self.data_manager.update_state(
                        **pending_state,
                    )
                    self._initialize_api()
                    if state_saved is False:
                        self._pending_login_state = pending_state
                        self._pending_login_state_baseline = (
                            self.data_manager.load_state()
                        )
                        message = (
                            "登录凭证已保存，但插件状态记录保存失败，"
                            "请检查数据目录权限"
                        )
                        return {"status": "error", "message": message}
                    self._clear_pending_login_state()
                    return {"status": "success" if qr_found else "already_logged_in"}
                else:
                    cleanup_ok = self._cleanup_new_login_auth(
                        auth_existed_before
                    )
                    err = self._redact_login_output(
                        full_buffer[-800:].strip()
                    )
                    if not cleanup_ok:
                        err = "登录失败，且临时凭证清理失败，请检查文件权限"
                    logger.error(f"[MiHome] 沙盒异常退出: {err}")
                    self.data_manager.update_state(last_login_error=err)
                    return {"status": "error", "message": err}
        except asyncio.CancelledError:
            stopped, cleanup_ok = await self._abort_login_attempt(
                proc,
                login_generation,
                auth_existed_before,
            )
            if not stopped:
                self.data_manager.update_state(
                    last_login_error="登录任务已取消，但登录进程仍在运行",
                )
            elif not cleanup_ok:
                self.data_manager.update_state(
                    last_login_error="登录任务已取消，但临时凭证清理失败",
                )
            raise
        except Exception as e:
            stopped, cleanup_ok = await self._abort_login_attempt(
                proc,
                login_generation,
                auth_existed_before,
            )
            if not stopped:
                safe_error = "登录失败，且登录进程未能安全停止"
            elif not cleanup_ok:
                safe_error = "登录失败，且临时凭证清理失败，请检查文件权限"
            else:
                safe_error = self._redact_login_output(str(e))
            self.data_manager.update_state(last_login_error=safe_error)
            return {"status": "error", "message": safe_error}
        finally:
            async with self._api_lock:
                self._finalize_login_generation_locked(
                    proc,
                    login_generation,
                )

    async def get_devices(self) -> List[Dict[str, Any]]:
        self._check_idle()
        self._check_api()
        try:
            async with self._api_lock:
                self._clear_prepared_device_cache()
                own = await self._run_sync_call(
                    self.api.get_devices_list,
                    warn_after=20.0,
                    operation="同步设备列表",
                )
                if not isinstance(own, list):
                    own = []

                shared = []
                shared_error = ""
                if hasattr(self.api, "get_shared_devices_list"):
                    try:
                        shared = await self._run_sync_call(
                            self.api.get_shared_devices_list,
                            warn_after=20.0,
                            operation="同步共享设备列表",
                        )
                        if not isinstance(shared, list):
                            shared = []
                    except Exception as e:
                        shared_error = f"共享列表获取异常: {type(e).__name__}"
                        logger.warning(f"[MiHome] {shared_error}")

                merged = {}
                did_to_name = {}
                did_to_model = {}
                for d in (own + shared):
                    if isinstance(d, dict) and d.get("did"):
                        did_str = str(d["did"]).strip()
                        merged[did_str] = d
                        did_to_name[did_str] = str(d.get("name", "未知设备")).strip() or "未知设备"
                        did_to_model[did_str] = str(d.get("model", "")).strip()

                state_updates = dict(
                    last_shared_error=shared_error,
                    did_to_name=did_to_name,
                    did_to_model=did_to_model,
                )
                previous_login_error = str(
                    self.data_manager.load_state().get(
                        "last_login_error",
                        "",
                    )
                    or ""
                ).strip()
                if previous_login_error.startswith(
                    DEVICE_SYNC_ERROR_PREFIXES
                ):
                    state_updates["last_login_error"] = ""

                saved = self.data_manager.update_state(**state_updates)
                if saved is False:
                    raise MiHomeClientError(
                        "设备缓存保存失败，请检查插件数据目录"
                    )
                return list(merged.values())
        except asyncio.TimeoutError as e:
            self.data_manager.update_state(last_login_error="拉取云端设备列表超时")
            raise MiHomeClientError("同步设备列表超时，请检查网络") from e
        except LoginError as e:
            self.data_manager.update_state(last_login_error="鉴权失效")
            raise MiHomeAuthError("login_expired") from e
        except SSLError as e:
            self.data_manager.update_state(last_login_error="SSL 通信异常")
            raise MiHomeClientError("ssl_error") from e
        except RequestException as e:
            self.data_manager.update_state(last_login_error=f"网络异常: {type(e).__name__}")
            raise MiHomeClientError("network_error") from e
        except APIError as e:
            self.data_manager.update_state(last_login_error="云端接口异常")
            raise MiHomeClientError("cloud_api_error") from e
        except MiHomeClientError:
            raise
        except Exception as e:
            self.data_manager.update_state(
                last_login_error=f"系统级同步异常: {type(e).__name__}"
            )
            raise MiHomeClientError("device_sync_error") from e

    async def get_scenes(self) -> List[Dict[str, Any]]:
        """读取云端场景列表，并在同步调用真实结束后更新缓存。"""
        self._check_idle()
        self._check_api()

        async def _fetch_once(timeout_sec: float) -> List[Dict[str, Any]]:
            async with self._api_lock:
                scenes = await self._run_sync_call(
                    self.api.get_scenes_list,
                    warn_after=timeout_sec,
                    operation="同步场景列表",
                )

            if not isinstance(scenes, list):
                scenes = []

            normalized = []
            for item in scenes:
                if isinstance(item, dict):
                    normalized.append(self._normalize_scene_item(item))
            return normalized

        try:
            normalized = await _fetch_once(30.0)
            self._save_scene_cache(normalized)
            return normalized

        except asyncio.TimeoutError as e:
            self.data_manager.update_state(last_scene_error="获取场景列表超时")
            raise MiHomeClientError("获取场景列表超时，请检查网络") from e
        except LoginError as e:
            self.data_manager.update_state(last_scene_error="鉴权失效")
            raise MiHomeAuthError("login_expired") from e
        except SSLError as e:
            self.data_manager.update_state(last_scene_error="SSL 通信异常")
            raise MiHomeClientError("ssl_error") from e
        except RequestException as e:
            self.data_manager.update_state(last_scene_error=f"网络异常: {type(e).__name__}")
            raise MiHomeClientError("network_error") from e
        except APIError as e:
            self.data_manager.update_state(last_scene_error="云端接口异常")
            raise MiHomeClientError("cloud_api_error") from e
        except MiHomeClientError:
            raise
        except Exception as e:
            self.data_manager.update_state(
                last_scene_error=f"场景获取异常: {type(e).__name__}"
            )
            raise MiHomeClientError("scene_sync_error") from e

    async def run_scene(self, scene_id: str, home_id: str = "", scene_name: str = "") -> None:
        self._check_idle()
        self._check_api()
        scene_id = str(scene_id or "").strip()
        home_id = str(home_id or "").strip()

        if not scene_id:
            raise MiHomeSceneError("scene_id 不能为空")
        if not home_id:
            self.data_manager.update_state(
                last_scene_error="场景缓存缺少家庭 ID",
                last_scene_name=scene_name or scene_id,
            )
            raise MiHomeSceneError("scene_home_missing")

        try:
            async with self._api_lock:
                logger.info(
                    f"[MiHome] 执行场景: {scene_name or '未命名场景'}"
                )
                result = await self._run_sync_call(
                    self.api.run_scene,
                    scene_id=scene_id,
                    home_id=home_id,
                    warn_after=20.0,
                    operation="执行场景",
                )
                if result is not True:
                    raise MiHomeSceneError("scene_unconfirmed")

            self.data_manager.update_state(
                last_scene_error="",
                last_scene_name=scene_name or scene_id,
                last_control_error="",
                last_control_device=f"scene:{scene_name or scene_id}",
            )
        except Exception as e:
            self._handle_scene_exception(e, scene_name or scene_id)

    async def get_device_capabilities(self, did: str) -> Dict[str, Any]:
        self._check_idle()
        self._check_api()
        try:
            async with self._api_lock:
                device = await self._run_sync_call(
                    self._prepare_device_sync,
                    did,
                    warn_after=15.0,
                    operation="读取设备能力",
                )

                try:
                    prop_list = getattr(device, "prop_list", {})
                    if not isinstance(prop_list, dict):
                        prop_list = {}
                except Exception as e:
                    logger.debug(
                        f"[MiHome] 读取 prop_list 失败: {type(e).__name__}"
                    )
                    prop_list = {}

                try:
                    action_list = getattr(device, "action_list", {})
                    if not isinstance(action_list, dict):
                        action_list = {}
                except Exception as e:
                    logger.debug(
                        f"[MiHome] 读取 action_list 失败: {type(e).__name__}"
                    )
                    action_list = {}

                all_props = []
                writable = []
                readable = []
                actions = []

                for raw_k, p_info in prop_list.items():
                    norm_k = self._normalize_key(raw_k)
                    if norm_k not in all_props:
                        all_props.append(norm_k)

                    is_readable, is_writable = self._parse_rw_field(
                        getattr(p_info, "rw", None)
                    )
                    if is_writable and norm_k not in writable:
                        writable.append(norm_k)
                    if is_readable and norm_k not in readable:
                        readable.append(norm_k)

                for raw_k in action_list.keys():
                    raw_action = str(raw_k).strip()
                    if raw_action and raw_action not in actions:
                        actions.append(raw_action)

                all_props.sort()
                writable.sort()
                readable.sort()
                actions.sort()

                return {
                    "all_props": all_props,
                    "writable": writable,
                    "readable": readable,
                    "actions": actions,
                }

        except (asyncio.TimeoutError, RequestsTimeout):
            return {"__error__": "请求超时 (设备离线或深度休眠)"}
        except DeviceGetError:
            return {"__error__": "设备拒绝读取能力菜单"}
        except LoginError:
            return {"__error__": "鉴权失效"}
        except json.JSONDecodeError:
            return {"__error__": "米家云端没有返回有效数据，请稍后重试"}
        except Exception as e:
            return {"__error__": f"接口异常:{type(e).__name__}"}

    async def get_device_props(
        self,
        did: str,
        readable_keys: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        self._check_idle()
        self._check_api()
        try:
            async with self._api_lock:
                device = await self._run_sync_call(
                    self._prepare_device_sync,
                    did,
                    warn_after=15.0,
                    operation="准备设备状态读取",
                )

                try:
                    prop_list = getattr(device, "prop_list", {})
                    if not isinstance(prop_list, dict):
                        prop_list = {}
                except Exception as e:
                    logger.debug(
                        f"[MiHome] 读取 prop_list 失败: {type(e).__name__}"
                    )
                    prop_list = {}

                result = {
                    "writable": [],
                    "readable": {},
                    "readable_keys": [],
                }

                exclude_kws = [
                    "fault",
                    "dbg",
                    "heartbeat",
                    "moto",
                    "motor",
                    "crc32",
                    "brand_id",
                    "remote_id",
                    "match_state",
                    "library",
                    "ac_type",
                    "mac",
                    "ip",
                    "user_device_info",
                ]

                seen_writable = set()
                for raw_k, p_info in prop_list.items():
                    norm_k = self._normalize_key(raw_k)
                    if any(bw in norm_k for bw in exclude_kws):
                        continue
                    if norm_k in seen_writable:
                        continue

                    _is_readable, is_writable = self._parse_rw_field(
                        getattr(p_info, "rw", None)
                    )

                    if is_writable:
                        seen_writable.add(norm_k)
                        result["writable"].append(norm_k)

                if readable_keys:
                    normalized_targets = list(
                        dict.fromkeys(self._normalize_key(k) for k in readable_keys if k)
                    )
                    requests = []
                    request_meta = []
                    for norm_k in normalized_targets:
                        prop_info = prop_list.get(norm_k)
                        if prop_info is None:
                            prop_info = prop_list.get(norm_k.replace("_", "-"))

                        is_readable, _is_writable = self._parse_rw_field(
                            getattr(prop_info, "rw", None)
                        )
                        method = dict(getattr(prop_info, "method", {}) or {})
                        if (
                            not is_readable
                            or "siid" not in method
                            or "piid" not in method
                        ):
                            result["readable_keys"].append(norm_k)
                            continue

                        method["did"] = did
                        requests.append(method)
                        request_meta.append((norm_k, prop_info, method))

                    if requests:
                        response = await self._run_sync_call(
                            self.api.get_devices_prop,
                            requests,
                            warn_after=15.0,
                            operation="批量读取设备状态",
                        )
                        response_items = (
                            response if isinstance(response, list) else [response]
                        )
                        response_map = {
                            (
                                str(item.get("did", did)),
                                item.get("siid"),
                                item.get("piid"),
                            ): item
                            for item in response_items
                            if isinstance(item, dict)
                        }

                        for norm_k, prop_info, method in request_meta:
                            item = response_map.get(
                                (
                                    str(method["did"]),
                                    method["siid"],
                                    method["piid"],
                                )
                            )
                            if (
                                not isinstance(item, dict)
                                or item.get("code") != 0
                                or item.get("value") is None
                            ):
                                result["readable_keys"].append(norm_k)
                                continue

                            val = item["value"]
                            if isinstance(val, float):
                                val = round(val, 2)
                            unit_str = self._unit_suffix(
                                getattr(prop_info, "unit", "")
                            )
                            result["readable"][norm_k] = f"{val}{unit_str}"

                    return result

                return result

        except (asyncio.TimeoutError, RequestsTimeout):
            return {"__error__": "请求超时 (设备离线或深度休眠)"}
        except DeviceGetError:
            return {"__error__": "设备拒绝读取状态"}
        except LoginError:
            return {"__error__": "鉴权失效"}
        except json.JSONDecodeError:
            return {"__error__": "米家云端没有返回有效数据，请稍后重试"}
        except Exception as e:
            return {"__error__": f"接口异常:{type(e).__name__}"}

    async def control_power(
        self,
        did: str,
        is_on: bool,
        device_name: str = "",
    ) -> bool:
        return await self.set_property(did, "on", is_on, device_name)

    async def set_property(
        self,
        did: str,
        prop: str,
        value: Any,
        device_name: str = "",
    ) -> bool:
        self._check_idle()
        self._check_api()
        try:
            async with self._api_lock:
                logger.info(
                    f"[MiHome] 执行属性控制: {device_name or '未命名设备'}"
                    f" -> property={prop}"
                )
                device = await self._run_sync_call(
                    self._prepare_device_sync,
                    did,
                    warn_after=15.0,
                    operation="准备属性控制",
                )
                method = self._build_property_method(
                    device,
                    did,
                    prop,
                    value,
                )
                response = await self._run_sync_call(
                    self.api.set_devices_prop,
                    method,
                    warn_after=15.0,
                    operation="下发属性控制",
                )
                confirmed = self._validate_action_response(response)
            self.data_manager.update_state(last_control_error="", last_control_device=device_name or did)
            return confirmed
        except Exception as e:
            self._handle_control_exception(e, device_name or did)

    async def run_action(
        self,
        did: str,
        action: str,
        device_name: str = "",
    ) -> bool:
        self._check_idle()
        self._check_api()
        try:
            async with self._api_lock:
                logger.info(
                    f"[MiHome] 执行动作控制: {device_name or '未命名设备'}"
                    f" -> action={action}"
                )
                device = await self._run_sync_call(
                    self._prepare_device_sync,
                    did,
                    warn_after=15.0,
                    operation="准备动作控制",
                )
                action_list = getattr(device, "action_list", {})
                if not isinstance(action_list, dict) or action not in action_list:
                    raise MiHomeControlError(f"action_not_found:{action}")
                action_info = action_list[action]
                method = dict(getattr(action_info, "method", {}) or {})
                if not method:
                    raise MiHomeControlError(f"action_schema_missing:{action}")
                method["did"] = did
                response = await self._run_sync_call(
                    self.api.run_action,
                    method,
                    warn_after=15.0,
                    operation="下发设备动作",
                )
                confirmed = self._validate_action_response(response)
            self.data_manager.update_state(last_control_error="", last_control_device=device_name or did)
            return confirmed
        except Exception as e:
            self._handle_control_exception(e, device_name or did)

    async def run_action_with_in(
        self,
        did: str,
        action: str,
        in_params: List[Dict[str, Any]],
        device_name: str = "",
    ) -> bool:
        """按 MIoT ``in`` 数组执行已验证过参数结构的动作。"""

        self._check_idle()
        self._check_api()
        try:
            async with self._api_lock:
                logger.info(
                    f"[MiHome] 执行带参动作: {device_name or '未命名设备'}"
                    f" -> action={action}, parameter_count={len(in_params)}"
                )
                device = await self._run_sync_call(
                    self._prepare_device_sync,
                    did,
                    warn_after=15.0,
                    operation="准备带参动作",
                )
                action_list = getattr(device, "action_list", {})
                if not isinstance(action_list, dict) or action not in action_list:
                    raise MiHomeControlError(f"action_not_found:{action}")

                action_info = action_list[action]
                method = dict(getattr(action_info, "method", {}) or {})
                if not method:
                    raise MiHomeControlError(f"action_schema_missing:{action}")
                method["did"] = did
                method["in"] = in_params
                response = await self._run_sync_call(
                    self.api.run_action,
                    method,
                    warn_after=15.0,
                    operation="下发带参动作",
                )
                confirmed = self._validate_action_response(response)
            self.data_manager.update_state(
                last_control_error="",
                last_control_device=device_name or did,
            )
            return confirmed
        except Exception as e:
            self._handle_control_exception(e, device_name or did)

    def _handle_scene_exception(self, e: Exception, scene_name: str):
        if isinstance(e, asyncio.TimeoutError):
            self.data_manager.update_state(last_scene_error="场景执行超时", last_scene_name=scene_name)
            raise MiHomeClientError("执行场景超时，请检查网络") from e
        elif isinstance(e, json.JSONDecodeError):
            self.data_manager.update_state(
                last_scene_error="米家云端返回的数据无效",
                last_scene_name=scene_name,
            )
            raise MiHomeSceneError("cloud_no_response") from e
        elif isinstance(e, LoginError):
            self.data_manager.update_state(last_scene_error="鉴权过期", last_scene_name=scene_name)
            raise MiHomeAuthError("login_expired") from e
        elif isinstance(e, APIError):
            self.data_manager.update_state(last_scene_error="云端拒绝", last_scene_name=scene_name)
            raise MiHomeClientError("cloud_api_error") from e
        elif isinstance(e, SSLError):
            self.data_manager.update_state(last_scene_error="SSL 通信异常", last_scene_name=scene_name)
            raise MiHomeClientError("ssl_error") from e
        elif isinstance(e, RequestException):
            self.data_manager.update_state(last_scene_error=f"网络异常: {type(e).__name__}", last_scene_name=scene_name)
            raise MiHomeClientError(f"网络请求失败: {type(e).__name__}") from e
        elif isinstance(e, MiHomeSceneError):
            self.data_manager.update_state(
                last_scene_error=str(e),
                last_scene_name=scene_name,
            )
            raise
        else:
            logger.error(f"[MiHome] 场景异常: type={type(e).__name__}")
            self.data_manager.update_state(
                last_scene_error=f"内部错误: {type(e).__name__}",
                last_scene_name=scene_name,
            )
            raise MiHomeSceneError("internal_error") from e

    def _handle_control_exception(self, e: Exception, device_name: str):
        if isinstance(e, asyncio.TimeoutError):
            self.data_manager.update_state(last_control_error="控制超时", last_control_device=device_name)
            raise MiHomeClientError("下发控制指令超时，请检查网络或设备状态") from e
        elif isinstance(e, json.JSONDecodeError):
            self.data_manager.update_state(
                last_control_error="米家云端返回的数据无效",
                last_control_device=device_name,
            )
            raise MiHomeControlError("cloud_no_response") from e
        elif isinstance(e, LoginError):
            self.data_manager.update_state(last_control_error="鉴权过期", last_control_device=device_name)
            raise MiHomeAuthError("login_expired") from e
        elif isinstance(e, DeviceNotFoundError):
            self.data_manager.update_state(last_control_error="DID不存在", last_control_device=device_name)
            raise MiHomeControlError("device_not_found") from e
        elif isinstance(e, (DeviceSetError, DeviceActionError)):
            self.data_manager.update_state(last_control_error="设备拒绝", last_control_device=device_name)
            raise MiHomeControlError("device_rejected") from e
        elif isinstance(e, MiHomeControlError):
            self.data_manager.update_state(
                last_control_error=str(e),
                last_control_device=device_name,
            )
            raise
        elif isinstance(e, APIError):
            self.data_manager.update_state(last_control_error="云端拒绝", last_control_device=device_name)
            raise MiHomeClientError("cloud_api_error") from e
        elif isinstance(e, SSLError):
            self.data_manager.update_state(last_control_error="SSL 通信异常", last_control_device=device_name)
            raise MiHomeClientError("ssl_error") from e
        elif isinstance(e, RequestException):
            self.data_manager.update_state(last_control_error=f"网络异常: {type(e).__name__}", last_control_device=device_name)
            raise MiHomeClientError(f"网络请求失败: {type(e).__name__}") from e
        elif isinstance(e, ValueError):
            self.data_manager.update_state(
                last_control_error="参数或设备能力不匹配",
                last_control_device=device_name,
            )
            raise MiHomeControlError("invalid_value_or_capability") from e
        else:
            logger.error(f"[MiHome] 控制异常: type={type(e).__name__}")
            self.data_manager.update_state(
                last_control_error=f"内部错误: {type(e).__name__}",
                last_control_device=device_name,
            )
            raise MiHomeControlError("internal_error") from e

    async def terminate(self):
        async with self._api_lock:
            stopped = await self._stop_login_process_locked()
            if not stopped:
                self._login_status = LOGIN_RUNNING
                logger.error("[MiHome] 插件终止时登录进程仍在运行")
                return
            self._login_status = LOGIN_IDLE
            session = getattr(self.api, "session", None)
            close_session = getattr(session, "close", None)
            if callable(close_session):
                try:
                    close_session()
                except Exception as exc:
                    logger.warning(
                        f"[MiHome] 关闭云端会话失败: {type(exc).__name__}"
                    )
            self._clear_prepared_device_cache()
            self.api = None
