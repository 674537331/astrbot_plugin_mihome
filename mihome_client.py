# -*- coding: utf-8 -*-
import re
import os
import sys
import asyncio
from datetime import datetime
from typing import Any, Dict, Callable, Awaitable, Union

from astrbot.api import logger
from mijiaAPI import (
    mijiaAPI, 
    mijiaDevice,
    LoginError,
    DeviceNotFoundError,
    DeviceSetError,
    APIError
)
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
        self._login_process: asyncio.subprocess.Process | None = None
        self._worker_script = os.path.join(os.path.dirname(__file__), "_login_worker.py")

    def _check_idle(self):
        if self._login_status != LOGIN_IDLE:
            raise MiHomeClientError("登录沙盒正在运行中，请等待其结束或超时后再试。")

    async def get_login_status(self) -> Dict[str, Any]:
        state = self.data_manager.load_state()
        return {
            "auth_exists": self.data_manager.auth_exists(),
            "login_in_progress": self._login_status != LOGIN_IDLE,
            "last_login_at": state.get("last_login_at", ""),
            "last_login_error": state.get("last_login_error", ""),
            "last_control_error": state.get("last_control_error", ""),
            "last_control_device": state.get("last_control_device", ""),
        }

    async def logout(self) -> bool:
        if self._login_process and self._login_process.returncode is None:
            logger.warning("[MiHome] 检测到登出指令，正在强制中止后台登录沙盒...")
            try:
                self._login_process.kill()
                await self._login_process.wait()
            except ProcessLookupError:
                pass
            finally:
                self._login_process = None

        self._login_status = LOGIN_IDLE 
        ok = self.data_manager.clear_auth_file()
        self.api = mijiaAPI(self.data_manager.get_auth_path())
        
        if ok:
            self.data_manager.update_state(
                last_login_at="",
                last_login_error="",
                last_control_error="",
                last_control_device=""
            )
        return ok

    async def login(self, qr_callback: Union[Callable[[str], Awaitable[None]], Callable[[str], None]]) -> Dict[str, Any]:
        if self._login_status != LOGIN_IDLE:
            return {"status": "in_progress"}

        logger.info(f"[MiHome] 启动独立沙盒登录环境 -> auth_path={self.data_manager.get_auth_path()}")
        self._login_status = LOGIN_RUNNING
        
        qr_found = False
        full_buffer = ""
        
        try:
            async with self._api_lock:
                self._login_process = await asyncio.create_subprocess_exec(
                    sys.executable, self._worker_script, self.data_manager.get_auth_path(),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT
                )
                
                if self._login_process.stdout is None:
                    raise MiHomeClientError("登录沙盒 stdout 管道不可用")
                
                async def read_stdout():
                    nonlocal qr_found, full_buffer
                    while True:
                        chunk = await self._login_process.stdout.read(256)
                        if not chunk:
                            break
                        
                        text = chunk.decode('utf-8', errors='replace')
                        full_buffer += text
                        
                        if not qr_found:
                            match = re.search(
                                r'也可以访问链接查看二维码图片:\s*(https://account\.xiaomi\.com[^\s]+)',
                                full_buffer,
                            )
                            if match:
                                qr_found = True
                                qr_url = match.group(1)
                                logger.info("[MiHome] 已成功从沙盒流中截获二维码链接。")
                                if asyncio.iscoroutinefunction(qr_callback):
                                    await qr_callback(qr_url)
                                else:
                                    qr_callback(qr_url)

                try:
                    await asyncio.wait_for(
                        asyncio.gather(self._login_process.wait(), read_stdout()),
                        timeout=120.0
                    )
                except asyncio.TimeoutError:
                    try:
                        self._login_process.kill()
                        await self._login_process.wait() 
                    except ProcessLookupError:
                        pass
                    
                    if not qr_found:
                        err_msg = "在120秒内未能提取到登录链接（可能因云端拥堵、网络延迟，或底层库输出格式变更）"
                        self.data_manager.update_state(last_login_error=err_msg)
                        return {"status": "qrcode_not_found"}
                    else:
                        self.data_manager.update_state(last_login_error="等待用户扫码确认已超时 (120秒)")
                        return {"status": "timeout"}

                if self._login_process.returncode == 0:
                    self.data_manager.update_state(
                        last_login_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        last_login_error=""
                    )
                    self.api = mijiaAPI(self.data_manager.get_auth_path())
                    
                    if not qr_found:
                        logger.info("[MiHome] 沙盒执行成功且未产生二维码，判定为凭证已存在或直接通过。")
                        return {"status": "already_logged_in"}
                    return {"status": "success"}
                else:
                    error_msg = full_buffer[-800:] if len(full_buffer) > 800 else full_buffer
                    error_msg = error_msg.strip()
                    logger.error(f"[MiHome] 沙盒进程崩溃: {error_msg}")
                    self.data_manager.update_state(last_login_error=error_msg)
                    return {"status": "error", "message": f"进程退出码 {self._login_process.returncode}\n{error_msg}"}

        except Exception as e:
            self.data_manager.update_state(last_login_error=str(e))
            return {"status": "error", "message": str(e)}

        finally:
            self._login_status = LOGIN_IDLE
            self._login_process = None

    async def get_devices(self) -> list[dict]:
        self._check_idle()
        try:
            async with self._api_lock:
                await asyncio.to_thread(self.api.login)
                devices = await asyncio.to_thread(self.api.get_devices_list)
            return devices if isinstance(devices, list) else []
        except LoginError as e:
            self.data_manager.update_state(last_login_error=str(e))
            raise MiHomeAuthError(f"米家官方登录态拒绝: {e}") from e
        except APIError as e:
            self.data_manager.update_state(last_login_error=f"拉取设备列表时 API 异常: {e}")
            raise MiHomeClientError(f"云端接口调用异常: {e}") from e
        except Exception as e:
            self.data_manager.update_state(last_login_error=f"拉取设备列表时未知异常: {e}")
            raise MiHomeClientError(f"获取列表时发生未知错误: {e}") from e

    async def control_power(self, did: str, is_on: bool, device_name: str = "") -> None:
        self._check_idle()
        try:
            async with self._api_lock:
                await asyncio.to_thread(self.api.login)
                device = mijiaDevice(self.api, did=did)
                await asyncio.to_thread(device.set, "on", is_on)

            self.data_manager.update_state(
                last_control_error="",
                last_control_device=device_name or did
            )
        except LoginError as e:
            self.data_manager.update_state(
                last_control_error=f"鉴权失效: {e}",
                last_control_device=device_name or did
            )
            raise MiHomeAuthError(f"下发控制指令前鉴权被拒: {e}") from e
        except DeviceNotFoundError as e:
            self.data_manager.update_state(
                last_control_error="设备 DID 不存在",
                last_control_device=device_name or did
            )
            raise MiHomeControlError("device_not_found") from e
        except DeviceSetError as e:
            self.data_manager.update_state(
                last_control_error=f"属性设置拒绝: {e}",
                last_control_device=device_name or did
            )
            raise MiHomeControlError("device_rejected") from e
        except APIError as e:
            self.data_manager.update_state(
                last_control_error=f"云端接口拒绝: {e}",
                last_control_device=device_name or did
            )
            raise MiHomeClientError(f"API Error: {e}") from e
        except Exception as e:
            self.data_manager.update_state(
                last_control_error=f"未知控制错误: {e}",
                last_control_device=device_name or did
            )
            raise MiHomeControlError(str(e)) from e

    async def terminate(self) -> None:
        if self._login_process and self._login_process.returncode is None:
            try:
                self._login_process.kill()
                await self._login_process.wait()
            except ProcessLookupError:
                pass
        self.api = None
        self._login_status = LOGIN_IDLE
        self._login_process = None
