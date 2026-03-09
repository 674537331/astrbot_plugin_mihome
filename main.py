# -*- coding: utf-8 -*-
import json
import shlex
import re
from typing import Any

from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig

from .data_manager import MiHomeDataManager
from .mihome_client import MiHomeClient, MiHomeAuthError, MiHomeControlError, MiHomeClientError

@register("astrbot_plugin_mihome", "Ryan", "米家云端智能管家", "v6.1.4")
class MiHomeControlPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}
        self.data_manager = MiHomeDataManager("astrbot_plugin_mihome")
        self.client = MiHomeClient(self.data_manager)
        self.action_alias = {
            "开": True, "开启": True, "打开": True, "on": True, 
            "关": False, "关闭": False, "off": False
        }

    def _parse_device_map(self) -> dict:
        raw = self.config.get("device_map", "{}")
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(parsed, dict):
                return {}
            return {str(k).strip(): str(v).strip() for k, v in parsed.items() if str(v).strip()}
        except Exception as e:
            logger.warning(f"[MiHome] device_map 解析失败: {e}")
            return {}

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("米家登录")
    async def mihome_login(self, event: AstrMessageEvent):
        yield event.plain_result("⏳ 正在拉起独立沙盒环境...")
        async def cb(url): 
            await event.send(MessageEventResult().message(f"🔔 请扫码授权：\n\n{url}"))
        
        res = await self.client.login(qr_callback=cb)
        s = res.get("status")
        msg = {
            "success": "🎉 授权成功！", 
            "timeout": "❌ 超时了。", 
            "qrcode_not_found": "⚠️ 未能抓取到链接。", 
            "already_logged_in": "✅ 您当前已处于登录状态。"
        }.get(s, f"❌ 错误: {res.get('message')}")
        yield event.plain_result(msg)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("米家状态")
    async def mihome_status(self, event: AstrMessageEvent):
        """查看插件诊断报告"""
        s = await self.client.get_login_status()
        
        # 🚀 修正点 2：优化文案，增加“未发生”状态
        last_device = s['last_control_device'] or '无'
        if not s['last_control_device']:
            last_result = '未发生'
        else:
            last_result = '失败' if s['last_control_error'] else '成功'

        yield event.plain_result(
            f"📊 状态报告：\n"
            f"- 凭证存在: {s['auth_exists']}\n"
            f"- 最近登录: {s['last_login_at'] or '无'}\n"
            f"- 登录异常: {s['last_login_error'] or '无'}\n"
            f"- 共享异常: {s['last_shared_error'] or '无'}\n"
            f"- 最近控制: {last_device} ({last_result})"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("米家登出")
    async def mihome_logout(self, event: AstrMessageEvent):
        yield event.plain_result("⏳ 正在登出...")
        try:
            ok = await self.client.logout()
            # 语义：无论 ok 是 True 还是 False，状态都已经重置了
            yield event.plain_result("✅ 登出成功，凭证及状态已重置。" if ok else "⚠️ 凭证本就不存在，已重置现场。")
        except Exception as e:
            logger.error(f"[MiHome] 登出失败: {e}")
            yield event.plain_result(f"❌ 登出异常: {e}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("刷新米家")
    async def refresh_mihome_devices(self, event: AstrMessageEvent):
        yield event.plain_result("⏳ 正在与云端同步设备列表...")
        try:
            devs = await self.client.get_devices()
            if not devs:
                yield event.plain_result("✅ 拉取成功，但未发现可用设备。")
                return
            res = [f"✅ 找到 {len(devs)} 个设备："]
            for i, d in enumerate(devs[:15], 1):
                res.append(f"{i}. 【{d.get('name')}】({d.get('did')}) [{'🟢' if d.get('isOnline') else '🔴'}]")
            yield event.plain_result("\n".join(res))
        except Exception as e:
            yield event.plain_result(f"❌ 同步失败: {e}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("控制米家")
    async def control_mihome_device(self, event: AstrMessageEvent):
        device_map = self._parse_device_map()
        msg = event.message_str.strip()
        cmd_prefix = r'^/?控制米家\s*'
        try:
            parts = shlex.split(re.sub(cmd_prefix, '', msg))
        except Exception as e:
            logger.warning(f"[MiHome] shlex解析异常: {e}")
            parts = re.sub(cmd_prefix, '', msg).split()

        if len(parts) < 2:
            yield event.plain_result("❌ 格式：/控制米家 [设备别名] [开/关]")
            return
        
        name, act = " ".join(parts[:-1]).strip(), parts[-1].lower()
        if name not in device_map or act not in self.action_alias:
            yield event.plain_result(f"❌ 找不到设备 '{name}'。")
            return

        yield event.plain_result(f"⏳ 正在下发指令...")
        try:
            await self.client.control_power(device_map[name], self.action_alias[act], name)
            yield event.plain_result("✅ 成功！")
        except MiHomeAuthError:
            yield event.plain_result("❌ 鉴权失效，请重新登录。")
        except MiHomeControlError as e:
            err = str(e)
            if err == "device_not_found":
                yield event.plain_result("❌ 云端找不到设备。注：共享设备权限可能受限。")
            elif err == "device_rejected":
                yield event.plain_result("❌ 设备在线但拒绝了请求。")
            else:
                yield event.plain_result(f"❌ 控制失败: {err}")
        except MiHomeClientError as e:
            yield event.plain_result(f"❌ API异常: {e}")
        except Exception as e:
            logger.error(f"[MiHome] 控制命令未知异常: {e}")
            yield event.plain_result(f"❌ 未知内部错误。")

    async def terminate(self): 
        await self.client.terminate()
