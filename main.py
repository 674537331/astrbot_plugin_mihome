# -*- coding: utf-8 -*-
import json
import shlex
import re
from typing import Any, Dict, List, Tuple, Optional

from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig

from .data_manager import MiHomeDataManager
from .mihome_client import MiHomeClient, MiHomeAuthError, MiHomeControlError, MiHomeClientError

@register("astrbot_plugin_mihome", "Ryan", "米家云端智能管家", "6.2.9")
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
        
        self.prop_alias = {
            "温度": "target_temperature", "环境温度": "temperature",
            "风速": "fan_level", "风量": "motor_speed",
            "模式": "mode", "亮度": "brightness", "颜色": "color"
        }
        
        self.val_alias = {
            "制冷": "cool", "制热": "heat", "送风": "fan", "除湿": "dry",
            "睡眠": "sleep", "自动": "auto", "静音": "silent",
            "低": "low", "中": "medium", "高": "high",
            "低档": 1, "中档": 2, "高档": 3,
            "一档": 1, "二档": 2, "三档": 3
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
            
        if re.match(r'^-?\d+$', val_str):
            return int(val_str)
        if re.match(r'^-?\d+\.\d+$', val_str):
            return float(val_str)
            
        return val_str

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("米家登录")
    async def mihome_login(self, event: AstrMessageEvent):
        yield event.plain_result("⏳ 正在拉起独立沙盒环境...")
        async def cb(url): 
            await event.send(event.plain_result(f"🔔 请使用米家APP扫码授权：\n\n{url}"))
        res = await self.client.login(qr_callback=cb)
        s = res.get("status")
        msg = {
            "success": "🎉 授权成功！", "timeout": "❌ 超时了。", 
            "qrcode_not_found": "⚠️ 未能抓取到链接。", "already_logged_in": "✅ 您已登录。"
        }.get(s, f"❌ 错误: {res.get('message')}")
        yield event.plain_result(msg)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("米家状态")
    async def mihome_status(self, event: AstrMessageEvent):
        s = await self.client.get_login_status()
        last_device = s['last_control_device'] or '无'
        if not s['last_control_device']:
            last_result = '未发生'
        else:
            last_result = '失败' if s['last_control_error'] else '成功'
            
        yield event.plain_result(
            f"📊 状态报告：\n- 凭证存在: {s['auth_exists']}\n- 登录异常: {s['last_login_error'] or '无'}\n"
            f"- 共享异常: {s['last_shared_error'] or '无'}\n- 最近控制: {last_device} ({last_result})"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("米家登出")
    async def mihome_logout(self, event: AstrMessageEvent):
        yield event.plain_result("⏳ 正在登出...")
        try:
            ok = await self.client.logout()
            if ok:
                yield event.plain_result("✅ 登出成功，凭证及状态已重置。")
            else:
                yield event.plain_result("⚠️ 凭证不存在，已重置现场。")
        except Exception as e:
            logger.error(f"[MiHome] 登出失败: {e}")
            yield event.plain_result(f"❌ 登出异常: {e}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("刷新米家")
    async def refresh_mihome_devices(self, event: AstrMessageEvent):
        yield event.plain_result("⏳ 正在同步设备列表并探测属性...")
        device_map = self._parse_device_map()
        try:
            devs = await self.client.get_devices()
            if not devs:
                yield event.plain_result("✅ 拉取成功，未发现可用设备。")
                return
            
            res = [f"✅ 找到 {len(devs)} 个设备："]
            for i, d in enumerate(devs[:20], 1):
                did_str = str(d.get('did')).strip()
                name = d.get('name')
                is_online = d.get('isOnline')
                status_icon = '🟢' if is_online else '🔴'
                
                aliases = [k for k, v in device_map.items() if str(v).strip() == did_str]
                alias_str = "/".join(aliases) if aliases else "未配置别名"
                
                if aliases:
                    if is_online:
                        props = await self.client.get_device_props(did_str)
                        if props.get("__error__"):
                            menu = f"探测异常 ({props['__error__']})"
                        else:
                            menu = ", ".join(props.keys()) if props else "未探测到属性"
                        res.append(f"{i}. 【{alias_str}】({name}) [{status_icon}] ({did_str})\n   └─ 🔎 探测属性: {menu}")
                    else:
                        res.append(f"{i}. 【{alias_str}】({name}) [{status_icon}] ({did_str})\n   └─ 💤 设备离线，跳过属性探测")
                else:
                    res.append(f"{i}. 【未配置别名】({name}) [{status_icon}] ({did_str})")
                    
            yield event.plain_result("\n".join(res))
        except MiHomeClientError as e:
            yield event.plain_result(f"❌ 同步设备失败: {e}")
        except Exception as e:
            yield event.plain_result(f"❌ 未知同步异常: {e}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("米家控制")
    async def control_mihome_device(self, event: AstrMessageEvent):
        device_map = self._parse_device_map()
        msg = event.message_str.strip()
        cmd_prefix = r'^/?米家控制\s*'
        content = re.sub(cmd_prefix, '', msg).strip()

        if not content:
            yield event.plain_result("❌ 缺少参数。\n格式：/米家控制 [设备名] [动作/属性] [值]")
            return

        try:
            parts = shlex.split(content)
        except Exception as e:
            logger.warning(f"[MiHome] shlex解析异常，回退普通分割: {e}")
            parts = content.split()

        alias, remaining_parts = self._match_device_alias(parts, device_map)
        
        if not alias:
            yield event.plain_result(f"❌ 找不到对应设备。请检查 WebUI 中的别名配置。")
            return

        if not remaining_parts:
            yield event.plain_result(f"❌ 请指定控制动作。例如：/米家控制 {alias} 开")
            return

        did = device_map[alias]

        if len(remaining_parts) == 1:
            token = remaining_parts[0]
            token_lower = token.lower()
            
            prop_values_lower = {str(v).lower() for v in self.prop_alias.values()}
            prop_alias_norm = {str(k).strip().lower(): v for k, v in self.prop_alias.items()}
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
                        yield event.plain_result("❌ 设备在线但拒绝了请求。")
                    else: 
                        yield event.plain_result(f"❌ 控制失败: {err}")
                except MiHomeClientError as e: 
                    yield event.plain_result(f"❌ API/网络异常: {e}")
                except Exception as e:
                    logger.error(f"[MiHome] 控制未知异常: {e}")
                    yield event.plain_result(f"❌ 内部错误。")
                return
            elif is_prop_candidate:
                yield event.plain_result(f"❌ 缺少属性值。示例：/米家控制 {alias} {token} 26")
                return
            else:
                yield event.plain_result(f"❌ 不支持的动作或属性不完整: {token}")
                return

        # 高级属性透传链路
        raw_prop = remaining_parts[0]
        raw_val_str = " ".join(remaining_parts[1:])
        
        prop_alias_norm = {str(k).strip().lower(): v for k, v in self.prop_alias.items()}
        prop = prop_alias_norm.get(raw_prop.strip().lower(), raw_prop.strip())
        
        raw_val_norm = raw_val_str.strip()
        val_alias_norm = {str(k).strip().lower(): v for k, v in self.val_alias.items()}
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
                yield event.plain_result("❌ 设备拒绝请求 (只读属性、属性名错误或值越界)。")
            else: 
                yield event.plain_result(f"❌ 设置失败: {err}")
        except MiHomeClientError as e: 
            yield event.plain_result(f"❌ API/网络异常: {e}")
        except Exception as e:
            logger.error(f"[MiHome] 设置异常: {e}")
            yield event.plain_result(f"❌ 内部错误。")

    async def terminate(self):
        await self.client.terminate()
