# -*- coding: utf-8 -*-
import json
import shlex
import re
from typing import Any, Dict, List, Tuple, Optional

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig

from .data_manager import MiHomeDataManager
from .mihome_client import MiHomeClient, MiHomeAuthError, MiHomeControlError, MiHomeClientError
from .device_profiles import (
    get_device_prop_map,
    get_device_val_map,
    get_device_display_map,
    get_reverse_prop_map,
    get_device_detail_writable_keys,
    get_device_detail_readable_keys,
    get_device_help_examples,
    get_device_help_hints,
)

PLUGIN_NAME = "astrbot_plugin_mihome"


@register(PLUGIN_NAME, "Ryan", "米家云端智能管家", "6.3.22")
class MiHomeControlPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}
        self.data_manager = MiHomeDataManager(PLUGIN_NAME)
        self.client = MiHomeClient(self.data_manager)

        self.action_alias = {
            "开": True,
            "开启": True,
            "打开": True,
            "on": True,
            "关": False,
            "关闭": False,
            "off": False,
        }

    def _parse_device_map(self) -> Dict[str, str]:
        raw = self.config.get("device_map", "{}")
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(parsed, dict):
                return {}
            return {str(k).strip(): str(v).strip() for k, v in parsed.items() if str(v).strip()}
        except Exception as e:
            logger.warning(f"[MiHome] device_map 解析失败: {e}")
            return {}

    def _match_device_alias(self, parts: List[str], device_map: Dict[str, str]) -> Tuple[Optional[str], List[str]]:
        if not parts:
            return None, []

        exact_alias = parts[0]
        if exact_alias in device_map:
            return exact_alias, parts[1:]

        best_alias = None
        best_len = 0
        for alias in device_map.keys():
            alias_parts = alias.split()
            if parts[:len(alias_parts)] == alias_parts and len(alias_parts) > best_len:
                best_alias = alias
                best_len = len(alias_parts)

        if not best_alias:
            return None, parts
        return best_alias, parts[best_len:]

    def _parse_value(self, val: Any) -> Any:
        if isinstance(val, (int, float, bool)):
            return val
        val_str = str(val).strip()
        val_lower = val_str.lower()

        if val_lower == "true":
            return True
        if val_lower == "false":
            return False
        if re.match(r"^-?\d+$", val_str):
            return int(val_str)
        if re.match(r"^-?\d+\.\d+$", val_str):
            return float(val_str)

        return val_str

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("米家登录")
    async def mihome_login(self, event: AstrMessageEvent):
        yield event.plain_result("⏳ 正在拉起独立沙盒环境...")

        async def cb(url):
            try:
                await event.send(event.plain_result(f"🔔 请使用米家APP扫码授权：\n\n{url}"))
            except Exception as e:
                logger.error(f"[MiHome] 往客户端推送授权链接失败: {e}")

        res = await self.client.login(qr_callback=cb)
        s = res.get("status")
        msg = {
            "success": "🎉 授权成功！",
            "timeout": "❌ 超时了。",
            "qrcode_not_found": "⚠️ 未能抓取到链接。",
            "already_logged_in": "✅ 您已登录。",
            "in_progress": "⚠️ 登录流程正在进行中，请稍候。",
        }.get(s, f"❌ 错误: {res.get('message')}")
        yield event.plain_result(msg)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("米家状态")
    async def mihome_status(self, event: AstrMessageEvent):
        s = await self.client.get_login_status()
        last_device = s["last_control_device"] or "无"
        last_result = "未发生" if not s["last_control_device"] else ("失败" if s["last_control_error"] else "成功")
        yield event.plain_result(
            f"📊 状态报告：\n"
            f"- 凭证存在: {s['auth_exists']}\n"
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
            yield event.plain_result("✅ 登出成功，凭证及状态已重置。" if ok else "⚠️ 凭证不存在，已重置现场。")
        except Exception as e:
            logger.error(f"[MiHome] 登出失败: {e}")
            yield event.plain_result(f"❌ 登出异常: {e}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("刷新米家")
    async def refresh_mihome_devices(self, event: AstrMessageEvent):
        yield event.plain_result("⏳ 正在同步设备列表...")
        device_map = self._parse_device_map()
        try:
            devs = await self.client.get_devices()
            if not devs:
                yield event.plain_result("✅ 拉取成功，未发现可用设备。")
                return

            res = [f"✅ 找到 {len(devs)} 个设备："]
            for i, d in enumerate(devs, 1):
                did_str = str(d.get("did")).strip()
                name = d.get("name")
                status_icon = "🟢" if d.get("isOnline") else "🔴"

                aliases = [k for k, v in device_map.items() if str(v).strip() == did_str]
                alias_str = "/".join(aliases) if aliases else "未配置别名"
                res.append(f"{i}. 【{alias_str}】({name}) [{status_icon}] ({did_str})")

            res.append("\n💡 提示: 发送 /米家详情 [别名] 可查看设备实况，或发送 /米家帮助 [别名] 获取控制示例。")
            yield event.plain_result("\n".join(res))
        except MiHomeClientError as e:
            yield event.plain_result(f"❌ 同步设备失败: {e}")
        except Exception as e:
            yield event.plain_result(f"❌ 未知同步异常: {e}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("米家详情")
    async def mihome_device_detail(self, event: AstrMessageEvent):
        device_map = self._parse_device_map()
        msg = event.message_str.strip()
        cmd_prefix = r"^/?米家详情\s*"
        content = re.sub(cmd_prefix, "", msg).strip()

        if not content:
            yield event.plain_result("❌ 缺少参数。\n格式：/米家详情 [设备别名]\n示例：/米家详情 净化器")
            return

        try:
            parts = shlex.split(content)
        except Exception:
            parts = content.split()

        alias, _ = self._match_device_alias(parts, device_map)
        if not alias:
            yield event.plain_result(
                "❌ 找不到对应的设备别名。\n"
                "⚠️ 为了保障安全，未配置别名的设备不支持查看详情。\n"
                "💡 请先通过 /刷新米家 获取 DID，并在 WebUI 插件设置中为其绑定一个好记的别名。"
            )
            return

        did = device_map[alias]
        state = self.data_manager.load_state()
        did_to_name = state.get("did_to_name", {})

        if did not in did_to_name:
            yield event.plain_result(
                f"⚠️ 尚未同步【{alias}】的底层设备档案。\n"
                f"💡 请先发送一次 /刷新米家，系统将自动加载该设备的专属物模型面板。"
            )
            return

        official_name = did_to_name[did]

        display_map = get_device_display_map(official_name)
        reverse_prop_map = get_reverse_prop_map(official_name)
        fallback_writables = get_device_detail_writable_keys(official_name)
        fallback_readables = get_device_detail_readable_keys(official_name)

        # 阶段1：秒回本地画像
        stage1_lines = [f"📖 【{alias}】本地设备画像:"]

        if fallback_writables:
            translated_writables = sorted(set(reverse_prop_map.get(w, w) for w in fallback_writables))
            stage1_lines.append("✅ 可调属性: " + ", ".join(translated_writables))

        if fallback_readables:
            translated_readables = sorted(set(display_map.get(k, k) for k in fallback_readables))
            stage1_lines.append("📡 状态传感: " + ", ".join(translated_readables))

        stage1_lines.append("\n⏳ 正在向米家云端精准读取实时数据，请稍候...")
        yield event.plain_result("\n".join(stage1_lines))

        # 阶段2：精准读取 detail_readable
        try:
            props_data = await self.client.get_device_props(did, readable_keys=fallback_readables)
            error_msg = props_data.get("__error__")

            stage2_lines = []

            if error_msg:
                stage2_lines.append(f"⚠️ 【{alias}】实况拉取失败:")
                stage2_lines.append(" └─ 该设备通常支持上述状态项，但目前可能离线或休眠，暂无法读取实时数值。")
                stage2_lines.append(f" └─ 原因: {error_msg}")
            else:
                readables = props_data.get("readable", {})
                readable_keys = props_data.get("readable_keys", [])
                cloud_writables = set(props_data.get("writable", []))

                if readables:
                    stage2_lines.append(f"📊 【{alias}】实时状态:")
                    translated_items = []
                    for k, v in readables.items():
                        friendly_name = display_map.get(k, k)
                        translated_items.append((friendly_name, v))
                    translated_items.sort(key=lambda x: x[0])
                    for idx, (name, val) in enumerate(translated_items):
                        prefix = " └─ " if idx == len(translated_items) - 1 else " ├─ "
                        stage2_lines.append(f"{prefix}{name}: {val}")

                # 只展示只读状态项中的缺失项
                filtered_missing = [k for k in readable_keys if k in fallback_readables]
                if filtered_missing:
                    if stage2_lines:
                        stage2_lines.append("")
                    stage2_lines.append("📡 已知状态项 (当前暂无数据):")
                    translated_keys = sorted(set(display_map.get(k, k) for k in filtered_missing))
                    keys_str = ", ".join(translated_keys[:40])
                    if len(translated_keys) > 40:
                        keys_str += f" ... 共{len(translated_keys)}项"
                    stage2_lines.append(" └─ " + keys_str)

                # 云端图纸推断出的可控项，比本地白名单多的部分
                new_discovered_writables = cloud_writables - set(fallback_writables)
                if new_discovered_writables:
                    if stage2_lines:
                        stage2_lines.append("")
                    stage2_lines.append("🔍 云端嗅探到的未知可控能力:")
                    translated_unknown = sorted(set(reverse_prop_map.get(k, k) for k in new_discovered_writables))
                    stage2_lines.append(" └─ " + ", ".join(translated_unknown))

                if not stage2_lines:
                    stage2_lines.append(f"✅ 【{alias}】在线就绪，但当前无实况数据返回。")

            yield event.plain_result("\n".join(stage2_lines))

        except Exception as e:
            logger.error(f"[MiHome] 获取属性异常: {e}")
            yield event.plain_result(f"❌ 内部处理异常: {e}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("米家帮助")
    async def mihome_control_help(self, event: AstrMessageEvent):
        device_map = self._parse_device_map()
        msg = event.message_str.strip()
        cmd_prefix = r"^/?米家帮助\s*"
        content = re.sub(cmd_prefix, "", msg).strip()

        if not content:
            yield event.plain_result("❌ 缺少参数。\n格式：/米家帮助 [设备别名]\n示例：/米家帮助 净化器")
            return

        try:
            parts = shlex.split(content)
        except Exception:
            parts = content.split()

        alias, _ = self._match_device_alias(parts, device_map)
        if not alias:
            yield event.plain_result(
                "❌ 找不到对应的设备别名。\n"
                "💡 请先通过 /刷新米家 获取 DID，并在 WebUI 插件设置中为其绑定一个好记的别名。"
            )
            return

        did = device_map[alias]
        state = self.data_manager.load_state()
        did_to_name = state.get("did_to_name", {})

        if did not in did_to_name:
            yield event.plain_result(
                f"⚠️ 尚未同步【{alias}】的专属控制指南，以下为通用控制格式：\n\n"
                f"基础开关:\n"
                f"- /米家控制 {alias} 开\n"
                f"- /米家控制 {alias} 关\n\n"
                f"高级格式:\n"
                f"- /米家控制 {alias} [属性] [值]\n\n"
                f"💡 建议先发送 /刷新米家 获取该设备更精准的专属示例。"
            )
            return

        official_name = did_to_name[did]
        reverse_prop_map = get_reverse_prop_map(official_name)
        fallback_writables = get_device_detail_writable_keys(official_name)
        help_examples = get_device_help_examples(official_name)
        help_hints = get_device_help_hints(official_name)

        msg_lines = []

        if fallback_writables:
            translated_writables = sorted(set(reverse_prop_map.get(w, w) for w in fallback_writables))
            msg_lines.append(f"✅ 【{alias}】支持控制的属性:\n" + ", ".join(translated_writables) + "\n")

        msg_lines.append("常用控制示例:")
        msg_lines.append(f"- /米家控制 {alias} 开")
        msg_lines.append(f"- /米家控制 {alias} 关")

        advanced_props = [k for k in fallback_writables if k != "on"]
        if advanced_props:
            if help_examples:
                for prop_cn, vals in help_examples.items():
                    for idx, val in enumerate(vals):
                        hint_str = f"  ({help_hints[prop_cn]})" if prop_cn in help_hints and idx == 0 else ""
                        msg_lines.append(f"- /米家控制 {alias} {prop_cn} {val}{hint_str}")
            else:
                for eng_k in advanced_props:
                    prop_cn = reverse_prop_map.get(eng_k, eng_k)
                    msg_lines.append(f"- /米家控制 {alias} {prop_cn} [对应值]")

        yield event.plain_result("\n".join(msg_lines))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("米家控制")
    async def control_mihome_device(self, event: AstrMessageEvent):
        device_map = self._parse_device_map()
        msg = event.message_str.strip()
        cmd_prefix = r"^/?米家控制\s*"
        content = re.sub(cmd_prefix, "", msg).strip()

        if not content:
            yield event.plain_result(
                "❌ 缺少参数。\n"
                "格式：/米家控制 [设备名] [动作/属性] [值]\n"
                "示例：\n"
                "/米家控制 空调 开\n"
                "/米家控制 空调 温度 26"
            )
            return

        try:
            parts = shlex.split(content)
        except Exception as e:
            logger.warning(f"[MiHome] shlex解析异常: {e}")
            parts = content.split()

        alias, remaining_parts = self._match_device_alias(parts, device_map)

        if not alias:
            yield event.plain_result(
                "❌ 找不到对应的设备别名。\n"
                "💡 请先通过 /刷新米家 获取 DID，并在 WebUI 中为其绑定别名。"
            )
            return

        if not remaining_parts:
            yield event.plain_result(f"❌ 请指定控制动作。\n💡 提示: 发送 /米家帮助 {alias} 查看该设备的控制范例。")
            return

        did = device_map[alias]
        state = self.data_manager.load_state()
        did_to_name = state.get("did_to_name", {})

        official_name = did_to_name.get(did)
        if not official_name:
            logger.info(f"[MiHome] 设备 {did} 缺少官方名缓存，控制链路回退到通用模式。")
            official_name = alias
            yield event.plain_result(
                f"⚠️ 尚未同步【{alias}】的专属设备档案，本次将以通用模式尝试控制。\n"
                f"💡 如需更准确的中文指令映射，建议稍后发送一次 /刷新米家"
            )

        prop_map = get_device_prop_map(official_name)
        val_map = get_device_val_map(official_name)

        # 单参数：可能是开/关，也可能是缺值属性
        if len(remaining_parts) == 1:
            token = remaining_parts[0]
            token_lower = token.lower()

            prop_values_lower = {str(v).lower() for v in prop_map.values()}
            prop_alias_norm = {str(k).strip().lower(): v for k, v in prop_map.items()}
            is_prop_candidate = (token_lower in prop_alias_norm) or (token_lower in prop_values_lower)

            if token_lower in self.action_alias:
                yield event.plain_result(f"⏳ 正在向【{alias}】下发开关指令...")
                try:
                    await self.client.control_power(did, self.action_alias[token_lower], alias)
                    yield event.plain_result("✅ 成功！")
                except MiHomeAuthError:
                    yield event.plain_result("❌ 鉴权失效，请重新登录。")
                except MiHomeControlError as e:
                    err = str(e)
                    if err == "device_not_found":
                        yield event.plain_result("❌ 云端找不到设备或权限受限。")
                    elif err == "device_rejected":
                        yield event.plain_result(
                            f"❌ 设备在线但拒绝了请求。\n💡 提示: 发送 /米家帮助 {alias} 检查指令是否越界。"
                        )
                    else:
                        yield event.plain_result(f"❌ 控制失败: {err}")
                except MiHomeClientError as e:
                    yield event.plain_result(f"❌ API/网络异常: {e}")
                except Exception:
                    yield event.plain_result("❌ 内部错误。")
                return

            elif is_prop_candidate:
                yield event.plain_result(f"❌ 缺少属性值。\n💡 提示: 发送 /米家帮助 {alias} 查看该设备的控制范例。")
                return

            else:
                yield event.plain_result(
                    f"❌ 不支持的动作或属性不完整: {token}\n"
                    f"💡 提示: 发送 /米家帮助 {alias} 查看支持的控制指令。"
                )
                return

        # 多参数：高级控制
        raw_prop = remaining_parts[0]
        raw_val_str = " ".join(remaining_parts[1:])

        prop_alias_norm = {str(k).strip().lower(): v for k, v in prop_map.items()}
        prop = prop_alias_norm.get(raw_prop.strip().lower(), raw_prop.strip())

        raw_val_norm = raw_val_str.strip()
        val_alias_norm = {str(k).strip().lower(): v for k, v in val_map.items()}
        val_mapped = val_alias_norm.get(raw_val_norm.lower(), raw_val_norm)

        val = self._parse_value(val_mapped)

        yield event.plain_result(f"⏳ 正在向【{alias}】尝试下发属性 [{prop}]={val}...")
        try:
            await self.client.set_property(did, prop, val, alias)
            yield event.plain_result("✅ 属性下发成功！")
        except MiHomeAuthError:
            yield event.plain_result("❌ 鉴权失效，请重新登录。")
        except MiHomeControlError as e:
            err = str(e)
            if err == "device_not_found":
                yield event.plain_result("❌ 云端找不到设备。")
            elif err == "device_rejected":
                yield event.plain_result(
                    f"❌ 设备拒绝请求 (可能值越界或为只读属性)。\n💡 提示: 发送 /米家帮助 {alias} 检查正确用法。"
                )
            else:
                yield event.plain_result(f"❌ 设置失败: {err}")
        except MiHomeClientError as e:
            yield event.plain_result(f"❌ API/网络异常: {e}")
        except Exception:
            yield event.plain_result("❌ 内部错误。")

    async def terminate(self):
        await self.client.terminate()
