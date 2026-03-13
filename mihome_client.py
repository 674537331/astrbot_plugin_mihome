# -*- coding: utf-8 -*-
import re
import os
import sys
import asyncio
from datetime import datetime
from typing import Dict, Callable, Awaitable, Union, Any, Optional, List

from astrbot.api import logger
from mijiaAPI import (
    mijiaAPI,
    mijiaDevice,
    LoginError,
    DeviceNotFoundError,
    DeviceSetError,
    DeviceGetError,
    APIError,
)

try:
    from requests.exceptions import RequestException, SSLError
except ImportError:
    class RequestException(Exception):
        pass

    class SSLError(Exception):
        pass

from .data_manager import MiHomeDataManager

LOGIN_IDLE = "idle"
LOGIN_RUNNING = "running"


class MiHomeClientError(Exception):
    pass


class MiHomeAuthError(MiHomeClientError):
    pass


class MiHomeControlError(MiHomeClientError):
    pass


class MiHomeClient:
    def __init__(self, data_manager: MiHomeDataManager):
        self.data_manager = data_manager
        self.api = mijiaAPI(self.data_manager.get_auth_path())
        self._api_lock = asyncio.Lock()
        self._login_status = LOGIN_IDLE
        self._login_process: Optional[asyncio.subprocess.Process] = None
        self._worker_script = os.path.join(os.path.dirname(__file__), "_login_worker.py")

    def _check_idle(self):
        if self._login_status != LOGIN_IDLE:
            raise MiHomeClientError("登录沙盒正在运行中。")

    def _check_api(self):
        if not self.api:
            raise MiHomeClientError("插件已被终止或未初始化。")

    def _normalize_key(self, key: str) -> str:
        return str(key).strip().lower().replace("-", "_")

    def _unit_suffix(self, unit: Any) -> str:
        mapping = {
            "percentage": "%",
            "celsius": "°C",
            "lux": " lux",
            "rpm": " rpm",
            "minutes": " 分钟",
            "days": " 天",
            "μg/m3": " μg/m3",
            "ug/m3": " μg/m3",
        }
        if unit in mapping:
            return mapping[unit]
        if unit in ("none", "", None):
            return ""
        return f" {unit}"

    def _prepare_device_sync(self, did: str):
        self.api.login()
        if getattr(self.api, "device_list", None) is None:
            logger.debug("[MiHome] 底层内存缓存为空，触发静默自愈拉取...")
            self.api.get_devices_list()
        return mijiaDevice(self.api, did=did, sleep_time=1.0)

    async def get_login_status(self) -> Dict[str, Any]:
        state = self.data_manager.load_state()
        return {
            "auth_exists": self.data_manager.auth_exists(),
            "login_in_progress": self._login_status != LOGIN_IDLE,
            "last_login_at": state.get("last_login_at", ""),
            "last_login_error": state.get("last_login_error", ""),
            "last_shared_error": state.get("last_shared_error", ""),
            "last_control_error": state.get("last_control_error", ""),
            "last_control_device": state.get("last_control_device", ""),
        }

    async def logout(self) -> bool:
        async with self._api_lock:
            if self._login_process and self._login_process.returncode is None:
                try:
                    self._login_process.kill()
                    await self._login_process.wait()
                except ProcessLookupError:
                    pass
                except Exception as e:
                    logger.warning(f"[MiHome] 强制中止登录进程失败: {e}")
                finally:
                    self._login_process = None

            self._login_status = LOGIN_IDLE
            ok = self.data_manager.clear_auth_file()
            self.api = mijiaAPI(self.data_manager.get_auth_path())

            self.data_manager.update_state(
                last_login_at="",
                last_login_error="",
                last_shared_error="",
                last_control_error="",
                last_control_device="",
            )
            return ok

    async def login(
        self,
        qr_callback: Union[Callable[[str], Awaitable[None]], Callable[[str], None]],
    ) -> Dict[str, Any]:
        if self._login_status != LOGIN_IDLE:
            return {"status": "in_progress"}

        logger.info(f"[MiHome] 启动登录沙盒进程 -> {self._worker_script}")
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
                    full_buffer = (full_buffer + text)[-4096:]

                    if text.strip():
                        for line in text.split("\n"):
                            if line.strip():
                                logger.debug(f"[Sandbox] {line.strip()}")

                    if not qr_found:
                        compact = full_buffer.replace("\r", "").replace("\n", "")
                        match = re.search(
                            r'(https://account\.xiaomi\.com/pass/qr/login\?[^\s\'"]+)',
                            compact,
                        )
                        if match:
                            url = match.group(1)
                            if "ticket=" in url and "dc=" in url and "sid=" in url:
                                qr_found = True
                                logger.info("[MiHome] 成功提取完整登录链接。")
                                if asyncio.iscoroutinefunction(qr_callback):
                                    await qr_callback(url)
                                else:
                                    qr_callback(url)

            try:
                await asyncio.wait_for(
                    asyncio.gather(proc.wait(), read_stdout()),
                    timeout=120.0,
                )
            except asyncio.TimeoutError:
                async with self._api_lock:
                    try:
                        proc.kill()
                        await proc.wait()
                    except ProcessLookupError:
                        pass
                    except Exception:
                        pass
                msg = "授权确认已超时 (120秒)" if qr_found else "超时未能提取登录链接"
                self.data_manager.update_state(last_login_error=msg)
                return {"status": "timeout" if qr_found else "qrcode_not_found"}

            async with self._api_lock:
                if proc.returncode == 0:
                    self.data_manager.update_state(
                        last_login_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        last_login_error="",
                    )
                    self.api = mijiaAPI(self.data_manager.get_auth_path())
                    return {"status": "success" if qr_found else "already_logged_in"}
                else:
                    err = full_buffer[-800:].strip()
                    logger.error(f"[MiHome] 沙盒异常退出: {err}")
                    self.data_manager.update_state(last_login_error=err)
                    return {"status": "error", "message": err}
        except Exception as e:
            self.data_manager.update_state(last_login_error=str(e))
            return {"status": "error", "message": str(e)}
        finally:
            self._login_status = LOGIN_IDLE
            async with self._api_lock:
                if self._login_process is proc:
                    self._login_process = None

    async def get_devices(self) -> List[Dict[str, Any]]:
        self._check_idle()
        self._check_api()
        try:
            async with self._api_lock:
                await asyncio.wait_for(asyncio.to_thread(self.api.login), timeout=15.0)
                own = await asyncio.wait_for(asyncio.to_thread(self.api.get_devices_list), timeout=20.0)
                if not isinstance(own, list):
                    own = []

                shared = []
                shared_error = ""
                if hasattr(self.api, "get_shared_devices_list"):
                    try:
                        shared = await asyncio.wait_for(
                            asyncio.to_thread(self.api.get_shared_devices_list),
                            timeout=20.0,
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

                self.data_manager.update_state(
                    last_shared_error=shared_error,
                    did_to_name=did_to_name,
                    did_to_model=did_to_model,
                )
                return list(merged.values())
        except asyncio.TimeoutError as e:
            self.data_manager.update_state(last_login_error="拉取云端设备列表超时")
            raise MiHomeClientError("同步设备列表超时，请检查网络") from e
        except LoginError as e:
            self.data_manager.update_state(last_login_error=f"鉴权失效: {e}")
            raise MiHomeAuthError(str(e)) from e
        except SSLError as e:
            self.data_manager.update_state(last_login_error=f"SSL异常: {e}")
            raise MiHomeClientError(f"云端通信安全建立失败: {e}") from e
        except RequestException as e:
            self.data_manager.update_state(last_login_error=f"网络异常: {type(e).__name__}")
            raise MiHomeClientError(f"请求失败: {e}") from e
        except APIError as e:
            self.data_manager.update_state(last_login_error=f"云端接口异常: {e}")
            raise MiHomeClientError(str(e)) from e
        except Exception as e:
            self.data_manager.update_state(last_login_error=f"系统级同步异常: {e}")
            raise MiHomeClientError(str(e)) from e

    async def get_device_capabilities(self, did: str) -> Dict[str, Any]:
        """
        返回原始能力菜单：
        {
            "all_props": [...],
            "writable": [...],
            "readable": [...],
            "__error__": "..."
        }
        """
        self._check_api()
        try:
            async with self._api_lock:
                device = await asyncio.wait_for(
                    asyncio.to_thread(self._prepare_device_sync, did),
                    timeout=15.0,
                )

                try:
                    prop_list = getattr(device, "prop_list", {})
                    if not isinstance(prop_list, dict):
                        prop_list = {}
                except Exception as e:
                    logger.debug(f"[MiHome] 读取 prop_list 失败: {e}")
                    prop_list = {}

                if not prop_list:
                    return {"all_props": [], "writable": [], "readable": []}

                all_props = []
                writable = []
                readable = []

                for raw_k, p_info in prop_list.items():
                    norm_k = self._normalize_key(raw_k)
                    if norm_k not in all_props:
                        all_props.append(norm_k)

                    rw = getattr(p_info, "rw", [])
                    rw_set = set()
                    if isinstance(rw, (list, tuple, set)):
                        rw_set = {str(x).lower() for x in rw}
                    elif isinstance(rw, str):
                        rw_set = {rw.lower()}

                    if "write" in rw_set and norm_k not in writable:
                        writable.append(norm_k)
                    if "read" in rw_set and norm_k not in readable:
                        readable.append(norm_k)

                all_props.sort()
                writable.sort()
                readable.sort()

                return {
                    "all_props": all_props,
                    "writable": writable,
                    "readable": readable,
                }

        except asyncio.TimeoutError:
            return {"__error__": "请求超时 (设备离线或深度休眠)"}
        except DeviceGetError:
            return {"__error__": "设备拒绝读取能力菜单"}
        except LoginError:
            return {"__error__": "鉴权失效"}
        except Exception as e:
            return {"__error__": f"接口异常:{type(e).__name__}"}

    async def get_device_props(
        self,
        did: str,
        readable_keys: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        返回统一结构：
        {
            "writable": [...],
            "readable": {k: v},
            "readable_keys": [...],
        }
        """
        self._check_api()
        try:
            async with self._api_lock:
                device = await asyncio.wait_for(
                    asyncio.to_thread(self._prepare_device_sync, did),
                    timeout=15.0,
                )

                try:
                    prop_list = getattr(device, "prop_list", {})
                    if not isinstance(prop_list, dict):
                        prop_list = {}
                except Exception as e:
                    logger.debug(f"[MiHome] 读取 prop_list 失败: {e}")
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

                    rw = getattr(p_info, "rw", [])
                    is_writable = False
                    if isinstance(rw, (list, tuple, set)):
                        rw_set = {str(x).lower() for x in rw}
                        if "write" in rw_set:
                            is_writable = True
                    elif isinstance(rw, str) and "write" in rw.lower():
                        is_writable = True

                    if is_writable:
                        seen_writable.add(norm_k)
                        result["writable"].append(norm_k)

                if readable_keys:
                    normalized_targets = list(
                        dict.fromkeys(self._normalize_key(k) for k in readable_keys if k)
                    )

                    read_concurrency = 2
                    semaphore = asyncio.Semaphore(read_concurrency)

                    async def fetch_one(norm_k: str):
                        raw_candidates = [norm_k, norm_k.replace("_", "-")]
                        fetch_key = None

                        for cand in raw_candidates:
                            if cand in prop_list:
                                fetch_key = cand
                                break

                        if fetch_key is None:
                            fetch_key = norm_k.replace("_", "-")

                        async with semaphore:
                            try:
                                val = await asyncio.wait_for(
                                    asyncio.to_thread(device.get, fetch_key),
                                    timeout=4.0,
                                )
                                if val is None:
                                    return norm_k, None

                                prop_info = prop_list.get(fetch_key) or prop_list.get(norm_k)
                                unit_str = self._unit_suffix(getattr(prop_info, "unit", "")) if prop_info else ""

                                if isinstance(val, float):
                                    val = round(val, 2)
                                return norm_k, f"{val}{unit_str}"
                            except Exception:
                                return norm_k, None

                    fetched = await asyncio.gather(*(fetch_one(k) for k in normalized_targets))

                    for norm_k, val in fetched:
                        if val is None:
                            result["readable_keys"].append(norm_k)
                        else:
                            result["readable"][norm_k] = val

                    return result

                return result

        except asyncio.TimeoutError:
            return {"__error__": "请求超时 (设备离线或深度休眠)"}
        except DeviceGetError:
            return {"__error__": "设备拒绝读取状态"}
        except LoginError:
            return {"__error__": "鉴权失效"}
        except Exception as e:
            return {"__error__": f"接口异常:{type(e).__name__}"}

    async def control_power(self, did: str, is_on: bool, device_name: str = "") -> None:
        self._check_idle()
        self._check_api()
        try:
            async with self._api_lock:
                logger.info(f"[MiHome] 执行开关控制: {device_name} ({did}) -> {'开' if is_on else '关'}")
                device = await asyncio.wait_for(
                    asyncio.to_thread(self._prepare_device_sync, did),
                    timeout=15.0,
                )
                await asyncio.wait_for(
                    asyncio.to_thread(device.set, "on", is_on),
                    timeout=15.0,
                )
            self.data_manager.update_state(last_control_error="", last_control_device=device_name or did)
        except Exception as e:
            self._handle_control_exception(e, device_name or did)

    async def set_property(self, did: str, prop: str, value: Any, device_name: str = "") -> None:
        self._check_idle()
        self._check_api()
        try:
            async with self._api_lock:
                logger.info(f"[MiHome] 执行高级控制: {device_name} ({did}) -> [{prop}] = {value}")
                device = await asyncio.wait_for(
                    asyncio.to_thread(self._prepare_device_sync, did),
                    timeout=15.0,
                )
                await asyncio.wait_for(
                    asyncio.to_thread(device.set, prop, value),
                    timeout=15.0,
                )
            self.data_manager.update_state(last_control_error="", last_control_device=device_name or did)
        except Exception as e:
            self._handle_control_exception(e, device_name or did)

    def _handle_control_exception(self, e: Exception, device_name: str):
        if isinstance(e, asyncio.TimeoutError):
            self.data_manager.update_state(last_control_error="控制超时", last_control_device=device_name)
            raise MiHomeClientError("下发控制指令超时，请检查网络或设备状态") from e
        elif isinstance(e, LoginError):
            self.data_manager.update_state(last_control_error=f"鉴权过期: {e}", last_control_device=device_name)
            raise MiHomeAuthError(str(e)) from e
        elif isinstance(e, DeviceNotFoundError):
            self.data_manager.update_state(last_control_error="DID不存在", last_control_device=device_name)
            raise MiHomeControlError("device_not_found") from e
        elif isinstance(e, DeviceSetError):
            self.data_manager.update_state(last_control_error=f"被拒: {e}", last_control_device=device_name)
            raise MiHomeControlError("device_rejected") from e
        elif isinstance(e, APIError):
            self.data_manager.update_state(last_control_error=f"云端拒绝: {e}", last_control_device=device_name)
            raise MiHomeClientError(f"云端拒绝请求: {e}") from e
        elif isinstance(e, SSLError):
            self.data_manager.update_state(last_control_error=f"SSL异常: {e}", last_control_device=device_name)
            raise MiHomeClientError(f"SSL 通信失败: {e}") from e
        elif isinstance(e, RequestException):
            self.data_manager.update_state(last_control_error=f"网络异常: {type(e).__name__}", last_control_device=device_name)
            raise MiHomeClientError(f"网络请求失败: {type(e).__name__}") from e
        else:
            logger.error(f"[MiHome] 控制异常: type={type(e).__name__}, detail={e}")
            self.data_manager.update_state(last_control_error=f"内部错误: {e}", last_control_device=device_name)
            raise MiHomeControlError(str(e)) from e

    async def terminate(self):
        async with self._api_lock:
            if self._login_process and self._login_process.returncode is None:
                try:
                    self._login_process.kill()
                    await self._login_process.wait()
                except ProcessLookupError:
                    pass
                except Exception as e:
                    logger.warning(f"[MiHome] 终止进程失败: {e}")
            self._login_status = LOGIN_IDLE
            self._login_process = None
