# -*- coding: utf-8 -*-
import os
import json
import asyncio
import shlex
import re
from typing import Tuple, Any

from miservice import MiAccount, MiIOService
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

# --- 统一的自定义异常体系 ---
class MiHomeException(Exception):
    pass

class MiHomeAuthError(MiHomeException):
    pass

class MiHomeAPIError(MiHomeException):
    pass

class MiHomeTimeoutError(MiHomeException):
    pass

@register("astrbot_plugin_mihome", "RyanVaderAn", "米家设备云端控制插件 (基于 MiService)", "v5.9.1")
class MiHomeControlPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}
        
        # 状态缓存、凭据快照与并发锁
        self._mi_service = None
        self._service_lock = asyncio.Lock()
        self._cached_credentials: Tuple[str, str] = ("", "")
        
        # 规范化路径与 Token 缓存
        base_data_path = str(get_astrbot_data_path())
        plugin_name = getattr(self, "name", "astrbot_plugin_mihome")
        plugin_data_path = os.path.join(base_data_path, "plugin_data", plugin_name)
        os.makedirs(plugin_data_path, exist_ok=True)
        self.token_store_path = os.path.join(plugin_data_path, "mi_token_cache.json")

        # 动作指令标准化映射
        self.action_alias = {
            "开": True, "开启": True, "打开": True, "on": True, "true": True,
            "关": False, "关闭": False, "off": False, "false": False
        }

    def _get_credentials(self) -> Tuple[str, str]:
        username = self.config.get("mi_username", "").strip()
        password = self.config.get("mi_password", "").strip()
        return username, password

    def _parse_device_map(self) -> dict:
        """严格校验并格式化设备映射表 (恢复完整的错误日志)"""
        raw_map = self.config.get("device_map", "{}")
        parsed = {}
        
        if isinstance(raw_map, str):
            try:
                parsed = json.loads(raw_map)
            except Exception as e:
                logger.error(f"[MiHome] device_map JSON 解析失败: {e}")
                return {}
        elif isinstance(raw_map, dict):
            parsed = raw_map

        if not isinstance(parsed, dict):
            logger.error("[MiHome] device_map 格式不正确，必须是字典/对象结构。")
            return {}

        valid_map = {}
        for name, cfg in parsed.items():
            name_clean = str(name).strip()
            if not name_clean: continue
            
            if isinstance(cfg, str) and cfg.strip():
                valid_map[name_clean] = {"did": cfg.strip(), "siid": 2, "piid": 1}
            elif isinstance(cfg, dict):
                did = str(cfg.get("did", "")).strip()
                if not did:
                    logger.warning(f"[MiHome] 设备 '{name_clean}' 缺少 did，已跳过。")
                    continue
                try:
                    siid = int(cfg.get("siid", 2))
                    piid = int(cfg.get("piid", 1))
                    valid_map[name_clean] = {"did": did, "siid": siid, "piid": piid}
                except (ValueError, TypeError):
                    logger.warning(f"[MiHome] 设备 '{name_clean}' 的 siid/piid 格式错误，已跳过。")
            else:
                logger.warning(f"[MiHome] 设备 '{name_clean}' 配置类型不合法，已跳过。")
                
        return valid_map

    async def _get_mi_service(self) -> MiIOService:
        current_creds = self._get_credentials()
        username, password = current_creds
        
        if not username or not password:
            raise MiHomeAuthError("请先在 WebUI 配置小米账号和密码。")

        async with self._service_lock:
            if self._cached_credentials != current_creds:
                if self._cached_credentials != ("", ""):
                    logger.info("[MiHome] 检测到账号凭据变更，正在清理旧缓存...")
                self._mi_service = None
                self._cached_credentials = current_creds

            if self._mi_service is not None:
                return self._mi_service
                
            try:
                account = MiAccount(username, password, self.token_store_path)
                await asyncio.wait_for(account.login('xiaomiio'), timeout=15.0)
                self._mi_service = MiIOService(account)
                logger.info("[MiHome] MiService 登录成功并已缓存")
                return self._mi_service
            except asyncio.TimeoutError as e:
                raise MiHomeTimeoutError("登录小米云端超时") from e
            except Exception as e:
                self._cached_credentials = ("", "")
                raise MiHomeAuthError(f"鉴权或初始化失败: {e}") from e

    async def _call_mi_api(self, func_name: str, *args, **kwargs) -> Any:
        max_retries = 1
        for attempt in range(max_retries + 1):
            try:
                service = await self._get_mi_service()
                func = getattr(service, func_name)
                timeout_val = 20.0 if func_name == "device_list" else 15.0
                return await asyncio.wait_for(func(*args, **kwargs), timeout=timeout_val)
            except asyncio.TimeoutError as e:
                raise MiHomeTimeoutError(f"请求超时 ({func_name})") from e
            except MiHomeException:
                raise  
            except Exception as e:
                err_str = str(e).lower()
                is_auth_issue = any(k in err_str for k in ["auth", "token", "unauthorized", "sign", "401", "login"])
                if is_auth_issue and attempt < max_retries:
                    logger.warning(f"[MiHome] 鉴权可能失效 ({e})，正在重试...")
                    self._mi_service = None
                    self._cached_credentials = ("", "") 
                    continue  
                raise MiHomeAPIError(f"云端接口异常: {e}") from e

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @filter.command("刷新米家")
    async def refresh_mihome_devices(self, event: AstrMessageEvent):
        yield event.plain_result("正在连接小米云端拉取设备，请稍候...")
        try:
            devices = await self._call_mi_api("device_list")
            if not devices:
                yield event.plain_result("✅ 拉取成功，但账号下没有绑定任何 MIoT 设备。")
                return
                
            total_count = len(devices)
            display_limit = 15
            devices_to_show = devices[:display_limit]
            
            result_texts = [f"✅ 成功找到 {total_count} 个设备" + (f" (仅展示前{display_limit}个)：\n" if total_count > display_limit else "：\n")]
            for idx, dev in enumerate(devices_to_show):
                # 防御性判断，防止脏数据导致崩溃
                if not isinstance(dev, dict): continue
                
                name = dev.get('name', '未知设备')
                if len(name) > 20: name = name[:18] + ".."
                model = dev.get('model', '未知型号')
                did = dev.get('did', '未知DID')
                is_online = "🟢在线" if dev.get('isOnline') else "🔴离线"
                result_texts.append(f"{idx + 1}. 【{name}】\n   - 型号: {model}\n   - DID: {did}\n   - 状态: {is_online}")
                
            if total_count > display_limit:
                result_texts.append(f"\n...以及其他 {total_count - display_limit} 个设备。")
                
            yield event.plain_result("\n".join(result_texts))
            
        except MiHomeAuthError:
            yield event.plain_result("❌ 鉴权失败，请检查账号密码配置是否正确。")
        except MiHomeTimeoutError:
            yield event.plain_result("❌ 请求超时，小米云端响应缓慢，请稍后再试。")
        except Exception:
            yield event.plain_result("❌ 发生内部错误，请检查后台日志。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @filter.command("控制米家")
    async def control_mihome_device(
        self, 
        event: AstrMessageEvent, 
        query: str = "", 
        args: Any = None
    ):
        """控制米家设备 (v5.9.1 定稿版)"""
        
        # 🚀 探针日志降级为 debug
        logger.debug(f"[MiHome Debug] 收到控制指令 -> query={query!r}, args={args!r}, raw_msg={event.message_str!r}")

        device_map = self._parse_device_map()
        if not device_map:
            yield event.plain_result("❌ 配置为空或格式错误，请前往 WebUI 检查 `device_map`。")
            return

        # 🚀 严谨的正则前缀移除
        full_msg = event.message_str.strip()
        clean_msg = re.sub(r'^/?控制米家(?:\s+|$)', '', full_msg).strip()
        
        try:
            parts = shlex.split(clean_msg)
        except Exception:
            parts = clean_msg.split()

        # 兜底：如果连碎块都没拿到，尝试用 query 和 args 拼装
        if len(parts) < 2:
            raw_parts = []
            if isinstance(query, str) and query.strip():
                raw_parts.append(query.strip())
            if args:
                if isinstance(args, str) and args.strip():
                    raw_parts.append(args.strip())
                elif isinstance(args, (list, tuple)):
                    raw_parts.extend(str(x).strip() for x in args if str(x).strip())
                else:
                    arg_str = str(args).strip()
                    if arg_str:
                        raw_parts.append(arg_str)
            
            raw_input = " ".join(raw_parts).strip()
            parts = raw_input.split()

        if len(parts) < 2:
            yield event.plain_result("❌ 格式错误。正确用法：/控制米家 [设备别名] [开/关]")
            return

        action_str = parts[-1].lower()
        device_name = " ".join(parts[:-1]).strip()

        if device_name not in device_map:
            available = "、".join(list(device_map.keys())[:10]) + ("..." if len(device_map) > 10 else "")
            yield event.plain_result(f"❌ 未找到设备 '{device_name}'。当前配置的设备有：{available}")
            return

        if action_str not in self.action_alias:
            yield event.plain_result(f"❌ 暂不支持动作 '{action_str}'。支持的指令：开, 关, on, off 等。")
            return

        is_on = self.action_alias[action_str]
        device_cfg = device_map[device_name]
        
        did = device_cfg["did"]
        siid = device_cfg["siid"]
        piid = device_cfg["piid"]

        yield event.plain_result(f"⏳ 正在尝试操作【{device_name}】...")

        try:
            props = [[siid, piid, is_on]]
            result = await self._call_mi_api("miot_set_props", did, props)
            
            if isinstance(result, list) and result and all(isinstance(i, dict) and i.get("code") == 0 for i in result):
                yield event.plain_result(f"✅ 操作成功！已发送指令给【{device_name}】。")
            else:
                raise MiHomeAPIError(f"设备返回异常状态码: {result}")
                
        except MiHomeAuthError:
            yield event.plain_result("❌ 会话或鉴权失效，请检查账号状态或稍后再试。")
        except MiHomeTimeoutError:
            yield event.plain_result("❌ 云端请求超时，设备可能离线或网络拥堵。")
        except MiHomeAPIError:
            yield event.plain_result("⚠️ 指令下发异常，设备可能未正常执行。(请检查 siid/piid 参数是否正确)")
        except Exception:
            yield event.plain_result("❌ 发生未知内部错误，请检查内部日志。")

    async def terminate(self):
        self._mi_service = None
