# -*- coding: utf-8 -*-
import os
import json
import shlex
import re
import sys
import asyncio
from typing import Any

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from mijiaAPI import mijiaAPI, mijiaDevice

@register("astrbot_plugin_mihome", "RyanVaderAn", "米家云端控制 (原生链接扫码版)", "v6.0-beta4")
class MiHomeControlPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}
        
        base_data_path = str(get_astrbot_data_path())
        plugin_name = getattr(self, "name", "astrbot_plugin_mihome")
        self.plugin_data_path = os.path.join(base_data_path, "plugin_data", plugin_name)
        os.makedirs(self.plugin_data_path, exist_ok=True)
        self.auth_store_path = os.path.join(self.plugin_data_path, "auth.json")

        self.api = mijiaAPI(self.auth_store_path)
        self._api_lock = asyncio.Lock()
        
        # 🚀 新增：UX 体验锁，防止多次触发登录导致弹出一堆二维码
        self._login_in_progress = False

        self.action_alias = {
            "开": True, "开启": True, "打开": True, "on": True, "true": True,
            "关": False, "关闭": False, "off": False, "false": False
        }

    def _parse_device_map(self) -> dict:
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
            logger.error("[MiHome] device_map 格式不正确，必须是字典结构。")
            return {}

        valid_map = {}
        for name, cfg in parsed.items():
            name_clean = str(name).strip()
            if not name_clean: continue
            
            if isinstance(cfg, str) and cfg.strip():
                valid_map[name_clean] = cfg.strip()
            elif isinstance(cfg, dict):
                did = str(cfg.get("did", "")).strip()
                if did:
                    valid_map[name_clean] = did
                else:
                    logger.warning(f"[MiHome] 设备 '{name_clean}' 缺少 did，已跳过。")
        return valid_map

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @filter.command("米家登录")
    async def mihome_login(self, event: AstrMessageEvent):
        # 🛡️ 状态位防御：如果正在登录中，直接驳回
        if self._login_in_progress:
            yield event.plain_result("⏳ 当前已有米家登录流程进行中，请查看之前的二维码链接或稍候再试。")
            return
            
        self._login_in_progress = True
        yield event.plain_result("⏳ 正在检查登录状态并向小米云端请求授权码，请稍候...")
        
        url_queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        
        class CaptureStdout:
            def __init__(self, original_stdout):
                self.original_stdout = original_stdout
                self.buffer = ""
                self.found = False
                
            def write(self, text):
                self.original_stdout.write(text) 
                self.buffer += text
                if not self.found:
                    match = re.search(r'也可以访问链接查看二维码图片:\s*(https://account\.xiaomi\.com[^\s]+)', self.buffer)
                    if match:
                        self.found = True
                        asyncio.run_coroutine_threadsafe(url_queue.put(match.group(1)), loop)
                        
            def flush(self):
                self.original_stdout.flush()

        original_stdout = sys.stdout
        sys.stdout = CaptureStdout(original_stdout)
        pending = set()

        def run_login():
            try:
                self.api.login()
            except Exception as e:
                logger.error(f"[MiHome] 登录线程异常: {e}")
                raise e

        try:
            async with self._api_lock:
                login_task = asyncio.create_task(asyncio.to_thread(run_login))
                queue_task = asyncio.create_task(url_queue.get())
                
                done, pending = await asyncio.wait(
                    [login_task, queue_task], 
                    return_when=asyncio.FIRST_COMPLETED,
                    timeout=15.0
                )
                
                if login_task in done:
                    exc = login_task.exception()
                    if exc:
                        yield event.plain_result(f"❌ 登录发生异常: {exc}")
                    else:
                        yield event.plain_result("✅ 检测到本地已存在有效授权凭证，无需重新扫码！")
                elif queue_task in done:
                    img_url = queue_task.result()
                    yield event.plain_result(f"🔔 请点击下方链接获取二维码图片，并使用【米家APP】扫描授权：\n\n{img_url}\n\n👉 扫码并在手机上点击确认后，机器人会自动完成配置。")
                    
                    try:
                        await asyncio.wait_for(login_task, timeout=120.0)
                        yield event.plain_result("🎉 扫码授权成功！云端通行证已自动保存。你可以使用 /刷新米家 或 /控制米家 了。")
                    except asyncio.TimeoutError:
                        yield event.plain_result("⏳ 已发送二维码链接，但等待扫码确认超时。若你已扫码，可直接尝试 /刷新米家。")
                else:
                    yield event.plain_result("❌ 获取二维码超时，请检查网络或稍后再试。")
                    
        except Exception as e:
            logger.exception(f"[MiHome] 扫码登录流程异常: {e}")
            yield event.plain_result("❌ 登录流程发生严重异常，请检查后台日志。")
        finally:
            sys.stdout = original_stdout
            self._login_in_progress = False  # 释放状态锁
            for task in pending:
                task.cancel()

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @filter.command("刷新米家")
    async def refresh_mihome_devices(self, event: AstrMessageEvent):
        yield event.plain_result("正在通过授权凭证拉取小米云端设备，请稍候...")
        try:
            async with self._api_lock:
                await asyncio.to_thread(self.api.login)
                devices = await asyncio.to_thread(self.api.get_devices_list)
            
            if not devices:
                yield event.plain_result("✅ 拉取成功，但当前授权账号下没有可用设备。")
                return
                
            total_count = len(devices)
            display_limit = 15
            devices_to_show = devices[:display_limit]
            
            result_texts = [f"✅ 成功找到 {total_count} 个设备" + (f" (仅展示前{display_limit}个)：\n" if total_count > display_limit else "：\n")]
            
            display_index = 0
            for dev in devices_to_show:
                if not isinstance(dev, dict): continue
                display_index += 1
                
                name = dev.get('name', '未知设备')
                if len(name) > 20: name = name[:18] + ".."
                model = dev.get('model', '未知型号')
                did = dev.get('did', '未知DID')
                is_online = "🟢在线" if dev.get('isOnline') else "🔴离线"
                result_texts.append(f"{display_index}. 【{name}】\n   - 型号: {model}\n   - DID: {did}\n   - 状态: {is_online}")
                
            if total_count > display_limit:
                result_texts.append(f"\n...以及其他 {total_count - display_limit} 个设备。")
                
            yield event.plain_result("\n".join(result_texts))
            
        except Exception as e:
            logger.error(f"[MiHome] 刷新列表异常: {repr(e)}")
            yield event.plain_result("❌ 鉴权失败或凭证已过期！请发送 /米家登录 重新扫码获取凭证。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @filter.command("控制米家")
    async def control_mihome_device(
        self, 
        event: AstrMessageEvent, 
        query: str = "", 
        args: Any = None
    ):
        logger.debug(f"[MiHome Debug] 收到控制指令 -> query={query!r}, args={args!r}, raw_msg={event.message_str!r}")

        device_map = self._parse_device_map()
        if not device_map:
            yield event.plain_result("❌ 配置为空或格式错误，请前往 WebUI 检查 `device_map`。")
            return

        full_msg = event.message_str.strip()
        clean_msg = re.sub(r'^/?控制米家(?:\s+|$)', '', full_msg).strip()
        
        try:
            parts = shlex.split(clean_msg)
        except Exception:
            parts = clean_msg.split()

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
            yield event.plain_result(f"❌ 未找到设备 '{device_name}'。当前配置有：{available}")
            return

        if action_str not in self.action_alias:
            yield event.plain_result(f"❌ 暂不支持动作 '{action_str}'。支持：开, 关, on, off 等。")
            return

        is_on = self.action_alias[action_str]
        did = device_map[device_name] 

        yield event.plain_result(f"⏳ 正在呼叫云端，尝试操作【{device_name}】...")

        try:
            async with self._api_lock:
                await asyncio.to_thread(self.api.login)
                device = mijiaDevice(self.api, did=did)
                logger.debug(f"[MiHome] 准备下发动作 -> did={did}, action='on', value={is_on}")
                await asyncio.to_thread(device.set, 'on', is_on)
            
            yield event.plain_result(f"✅ 操作成功！已成功发送 {'开启' if is_on else '关闭'} 指令给【{device_name}】。")
                
        except Exception as e:
            logger.error(f"[MiHome] 控制设备失败 -> did={did}, action={is_on}, error={repr(e)}")
            err_str = str(e).lower()
            
            if "not found" in err_str or "did" in err_str:
                yield event.plain_result(f"❌ 找不到该设备，请检查 WebUI 配置里的 DID 是否正确。")
            else:
                yield event.plain_result("❌ 接口报错或凭证可能已过期！请发送 /米家登录 重新扫码，若已扫码请检查设备是否离线。")

    async def terminate(self):
        self.api = None
