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

@register("astrbot_plugin_mihome", "RyanVaderAn", "米家云端智能管家", "v6.1.0")
class MiHomeControlPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}

        self.data_manager = MiHomeDataManager("astrbot_plugin_mihome")
        self.client = MiHomeClient(self.data_manager)

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
            else:
                logger.warning(f"[MiHome] 设备 '{name_clean}' 配置类型不合法，已跳过。")

        return valid_map

    def _parse_control_args(self, event: AstrMessageEvent, query: str = "", args: Any = None):
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
            parts = " ".join(raw_parts).strip().split()

        if len(parts) < 2:
            return None, None

        action_str = parts[-1].lower()
        device_name = " ".join(parts[:-1]).strip()
        return device_name, action_str

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @filter.command("米家登录")
    async def mihome_login(self, event: AstrMessageEvent):
        """拉起隔离沙盒，尝试提取并返回扫码登录链接"""
        status_check = await self.client.get_login_status()
        if status_check["login_in_progress"]:
            yield event.plain_result("⏳ 登录沙盒已在运行中，请检查是否已有链接，或等待其结束。")
            return

        yield event.plain_result("⏳ 正在拉起独立沙盒环境...")

        async def send_qr_link(url: str):
            await event.send(MessageEventResult().message(
                f"🔔 请点击下方链接，使用手机【米家APP】扫码授权：\n\n{url}\n\n⏳ 机器人正在后台等待您的确认(120秒超时)..."
            ))

        result = await self.client.login(qr_callback=send_qr_link)
        status = result.get("status")

        if status == "already_logged_in":
            yield event.plain_result("✅ 检测到本地已存在有效凭证或刚已授权成功，无需再扫码。如需换号请先 /米家登出。")
        elif status == "success":
            yield event.plain_result("🎉 扫码授权成功！通行证已保存在本地，你可以正常使用了。")
        elif status == "timeout":
            yield event.plain_result("❌ 扫码确认超时 (120秒)。沙盒进程已被强制销毁，请重新执行 /米家登录。")
        elif status == "qrcode_not_found":
            logger.warning("[MiHome] 未能从登录流中提取二维码链接 (可能因网络阻塞或输出格式变更)")
            yield event.plain_result("⚠️ 在规定时间内未能提取到登录链接。可能是网络阻塞或底层库输出格式变化，请查看后台日志。")
        elif status == "error":
            yield event.plain_result(f"❌ 登录时遇到严重异常，沙盒反馈：\n{result.get('message', '未知错误')}")
        else:
            yield event.plain_result(f"❌ 未知登录结果状态：{status}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @filter.command("米家状态")
    async def mihome_status(self, event: AstrMessageEvent):
        """查看底层沙盒运行状态及历史错误摘要"""
        status = await self.client.get_login_status()
        text = (
            f"📊 米家插件运行状态：\n"
            f"- 凭证(auth.json)存在: {'✅' if status['auth_exists'] else '❌'}\n"
            f"- 登录沙盒运行中: {'是' if status['login_in_progress'] else '否'}\n"
            f"- 最近登录时间: {status['last_login_at'] or '无'}\n"
            f"- 最近异常日志: {status['last_login_error'] or '无'}\n"
            f"- 最近控制设备: {status['last_control_device'] or '无'}\n"
            f"- 最近控制错误: {status['last_control_error'] or '无'}"
        )
        yield event.plain_result(text)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @filter.command("米家登出")
    async def mihome_logout(self, event: AstrMessageEvent):
        """强制清理内存实例、中断沙盒并删除鉴权文件"""
        try:
            ok = await self.client.logout()
            if ok:
                yield event.plain_result("✅ 已强制切断所有相关进程并清除本地凭证。")
            else:
                yield event.plain_result("❌ 清除授权文件失败或文件本就不存在。")
        except MiHomeClientError as e:
            yield event.plain_result(f"❌ 登出被拒绝：{e}")
        except Exception as e:
            yield event.plain_result(f"❌ 登出过程中发生异常：{e}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @filter.command("刷新米家")
    async def refresh_mihome_devices(self, event: AstrMessageEvent):
        """从云端拉取当前账号下绑定的设备信息及 DID 列表"""
        yield event.plain_result("⏳ 正在与云端同步设备列表...")
        try:
            devices = await self.client.get_devices()
            if not devices:
                yield event.plain_result("✅ 拉取成功，但未发现可用设备。")
                return

            lines = [f"✅ 成功找到 {len(devices)} 个设备：\n"]
            for idx, dev in enumerate(devices[:20], start=1):
                if not isinstance(dev, dict): continue
                name = dev.get("name", "未知设备")
                did = dev.get("did", "未知DID")
                model = dev.get("model", "未知型号")
                online = "🟢在线" if dev.get("isOnline") else "🔴离线"
                lines.append(f"{idx}. 【{name}】\n   - DID: {did}\n   - Model: {model}\n   - 状态: {online}")
            if len(devices) > 20:
                lines.append(f"\n...以及其他 {len(devices) - 20} 个设备。")

            yield event.plain_result("\n".join(lines))
        except MiHomeAuthError as e:
            yield event.plain_result(f"❌ 鉴权被拒！请确认凭证是否有效。建议 /米家登出 后重新扫码。\n详细: {e}")
        except MiHomeClientError as e:
            yield event.plain_result(f"❌ 通信或解析异常，请稍后再试。\n详细: {e}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @filter.command("控制米家")
    async def control_mihome_device(self, event: AstrMessageEvent, query: str = "", args: Any = None):
        """通过预设别名向设备下发开/关控制指令"""
        device_map = self._parse_device_map()
        if not device_map:
            yield event.plain_result("❌ 设备词典为空。请前往 WebUI 检查 `device_map` 配置。")
            return

        device_name, action_str = self._parse_control_args(event, query, args)
        if not device_name or not action_str:
            yield event.plain_result("❌ 指令无法理解。尝试：/控制米家 [别名] [开/关]")
            return

        if device_name not in device_map:
            available = "、".join(list(device_map.keys())[:10]) + ("..." if len(device_map) > 10 else "")
            yield event.plain_result(f"❌ 找不到 '{device_name}'。当前配置：{available}")
            return

        if action_str not in self.action_alias:
            yield event.plain_result("❌ 动作无效。支持：开、关、on、off 等。")
            return

        did = device_map[device_name]
        is_on = self.action_alias[action_str]

        yield event.plain_result(f"⏳ 下发【{device_name}】{'开启' if is_on else '关闭'}指令...")
        try:
            await self.client.control_power(did=did, is_on=is_on, device_name=device_name)
            yield event.plain_result("✅ 指令下发成功！")
        except MiHomeAuthError:
            yield event.plain_result("❌ 通行证无效，请发送 /米家登出 后重新扫码。")
        except MiHomeClientError as e:
            yield event.plain_result(f"❌ API 拒绝请求：{e}")
        except MiHomeControlError as e:
            err_msg = str(e)
            if err_msg == "device_not_found":
                yield event.plain_result(f"❌ 云端无此设备 ({did})，请检查配置。")
            elif err_msg == "device_rejected":
                yield event.plain_result("❌ 设备本体拒绝属性修改。")
            else:
                yield event.plain_result(f"❌ 控制层异常：{err_msg}")

    async def terminate(self):
        """生命周期结束时的清理工作"""
        await self.client.terminate()
