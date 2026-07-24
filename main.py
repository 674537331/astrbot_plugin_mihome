# -*- coding: utf-8 -*-
import json
import shlex
import re
from datetime import datetime
from typing import Any, Dict, List, Tuple, Optional

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig

from .data_manager import MiHomeDataManager
from .control_tools import MiHomeControlTools, is_denied_device_action
from .web_api import MiHomeWebAPI
from .mihome_client import (
    MiHomeClient,
    MiHomeAuthError,
    MiHomeControlError,
    MiHomeClientError,
    MiHomeSceneError,
)
from .device_profiles import (
    normalize_category,
    CATEGORY_NONE,
    CATEGORY_AC,
    CATEGORY_PURIFIER,
    CATEGORY_FAN,
    CATEGORY_TH_SENSOR,
    CATEGORY_BODY_SCALE,
    CATEGORY_VACUUM,
    CATEGORY_WATER_HEATER,
    CATEGORY_ROUTER,
    CATEGORY_SPEAKER,
    CATEGORY_SWITCH,
    CATEGORY_DOOR_SENSOR,
    CATEGORY_GAS_SENSOR,
    get_device_property_value_map,
    get_device_prop_map,
    get_device_val_map,
    get_device_display_map,
    get_device_value_display_map,
    get_device_action_map,
    get_reverse_prop_map,
    get_reverse_action_map,
    get_device_detail_writable_keys,
    get_device_detail_readable_keys,
    get_device_detail_actions,
    get_device_help_examples,
    get_device_action_examples,
    get_device_help_hints,
    resolve_effective_category,
    has_model_profile,
    get_model_hidden_props,
)

PLUGIN_NAME = "astrbot_plugin_mihome"

MIHOME_HELP_SECTIONS = (
    (
        "🔑 账号与同步",
        (
            ("/米家登录", "打开内置管理页安全授权米家账号"),
            ("/米家状态", "查看登录、共享设备和最近控制状态"),
            ("/刷新米家", "同步设备列表并刷新 DID、model 缓存"),
            ("/米家登出", "清除登录凭证和本地状态"),
        ),
    ),
    (
        "📡 查询与帮助",
        (
            ("/米家详情 [设备别名]", "查看设备能力与实时状态"),
            ("/米家帮助 [设备别名]", "查看设备专属控制示例"),
        ),
    ),
    (
        "🎬 场景",
        (
            ("/米家场景列表", "同步并列出云端场景"),
            ("/米家场景 [场景名或 scene_id]", "执行指定场景"),
        ),
    ),
    (
        "🎛️ 设备控制",
        (
            ("/米家控制 [设备别名] [开/关]", "控制设备电源"),
            ("/米家控制 [设备别名] [属性] [值]", "设置设备属性"),
            ("/米家控制 [设备别名] [动作名]", "执行设备动作"),
        ),
    ),
)

READONLY_ALLOWED_CATEGORIES = {
    CATEGORY_AC,
    CATEGORY_PURIFIER,
    CATEGORY_FAN,
    CATEGORY_TH_SENSOR,
    CATEGORY_BODY_SCALE,
    CATEGORY_VACUUM,
    CATEGORY_WATER_HEATER,
    CATEGORY_ROUTER,
    CATEGORY_SPEAKER,
    CATEGORY_SWITCH,
    CATEGORY_DOOR_SENSOR,
    CATEGORY_GAS_SENSOR,
}


@register(PLUGIN_NAME, "Ryan", "米家云端智能管家", "8.0.1")
class MiHomeControlPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config if config is not None else {}
        self.data_manager = MiHomeDataManager(PLUGIN_NAME)
        self.client = MiHomeClient(self.data_manager)
        self.control_tools = MiHomeControlTools(self)
        self.web_api = MiHomeWebAPI(self)
        self.web_api.register_routes(context)

        self.action_alias = {
            "开": True,
            "开启": True,
            "打开": True,
            "on": True,
            "关": False,
            "关闭": False,
            "off": False,
        }

    def _parse_json_map(self, key: str) -> Dict[str, str]:
        raw = self.config.get(key, "{}")
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(parsed, dict):
                return {}
            return {str(k).strip(): str(v).strip() for k, v in parsed.items() if str(k).strip()}
        except Exception as e:
            logger.warning(
                f"[MiHome] {key} 解析失败: {type(e).__name__}"
            )
            return {}

    def _parse_device_map(self) -> Dict[str, str]:
        return {
            k: v for k, v in self._parse_json_map("device_map").items()
            if str(v).strip()
        }

    def _parse_category_map(self) -> Dict[str, str]:
        raw_map = self._parse_json_map("device_category_map")
        normalized = {}
        for alias, category in raw_map.items():
            normalized[alias] = normalize_category(category)
        return normalized

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

    def _translate_readable_value(self, key: str, raw_value: Any, value_display_map: Dict[str, Dict[Any, str]]) -> Any:
        mapping = value_display_map.get(key, {})
        if not mapping:
            return raw_value

        try:
            if raw_value in mapping:
                return mapping[raw_value]
        except TypeError:
            pass

        parsed_value = self._parse_value(raw_value)
        try:
            if parsed_value in mapping:
                return mapping[parsed_value]
        except TypeError:
            pass

        raw_str = str(raw_value).strip()
        for map_key, map_val in mapping.items():
            if str(map_key).strip().lower() == raw_str.lower():
                return map_val

        return raw_value

    def _normalize_action_token(self, s: str) -> str:
        return str(s or "").strip().lower().replace("-", "_").replace(" ", "_")

    def _render_command_help(self) -> str:
        lines = [
            "🏠 米家控制 · 指令总览",
            "英文别名：/mihome_help",
            "",
        ]
        for section_name, commands in MIHOME_HELP_SECTIONS:
            lines.append(section_name)
            for command, description in commands:
                lines.append(f"- {command}：{description}")
            lines.append("")

        lines.extend(
            (
                "🚀 快速开始",
                "插件详情 → 米家管理 → 扫码登录 → 同步设备 → 保存别名与类别",
                "",
                "🔒 除本帮助入口外，以上账号、设备与场景指令仅管理员可执行。",
            )
        )
        return "\n".join(lines)

    def _scene_tool_enabled(self) -> bool:
        scene_tool_cfg = self.config.get("scene_tool", {})
        if isinstance(scene_tool_cfg, dict) and "enable" in scene_tool_cfg:
            return bool(scene_tool_cfg.get("enable", False))
        return bool(self.config.get("enable_scene_tool", False))

    def _scene_tool_admin_only(self) -> bool:
        scene_tool_cfg = self.config.get("scene_tool", {})
        if isinstance(scene_tool_cfg, dict) and "admin_only" in scene_tool_cfg:
            return bool(scene_tool_cfg.get("admin_only", True))
        return bool(self.config.get("scene_tool_admin_only", True))

    def _event_is_admin(self, event: AstrMessageEvent) -> bool:
        # 只信任 AstrBot 根据 admins_id 计算出的事件权限。平台侧的群主/
        # 群管理员身份不能提升为 AstrBot 全局管理员，否则可能越权执行场景。
        checker = getattr(event, "is_admin", None)
        try:
            if callable(checker):
                return bool(checker())
            if isinstance(checker, bool):
                return checker
        except Exception:
            return False

        return False

    def _check_scene_tool_access(self, event: AstrMessageEvent) -> Optional[str]:
        if not self._scene_tool_enabled():
            return "米家场景 Tool 当前未启用。"
        if self._scene_tool_admin_only() and not self._event_is_admin(event):
            return "米家场景 Tool 当前仅管理员可调用。"
        return None

    def _readonly_tool_enabled(self) -> bool:
        return bool(self.config.get("enable_readonly_tool", False))

    def _get_cloud_name_by_did(self, did: str) -> str:
        state = self.data_manager.load_state()
        did_to_name = state.get("did_to_name", {})
        return str(did_to_name.get(did, "")).strip()

    def _get_model_by_did(self, did: str) -> str:
        state = self.data_manager.load_state()
        did_to_model = state.get("did_to_model", {})
        return str(did_to_model.get(did, "")).strip()

    def _get_cached_scenes(self) -> List[Dict[str, Any]]:
        state = self.data_manager.load_state()
        scenes = state.get("scenes", [])
        return scenes if isinstance(scenes, list) else []

    def _get_scene_cache_updated_at(self) -> str:
        state = self.data_manager.load_state()
        return str(state.get("scene_cache_updated_at", "")).strip()

    def _format_scene_line(
        self,
        idx: int,
        scene: Dict[str, Any],
        *,
        include_home: bool = False,
    ) -> str:
        scene_name = scene.get("scene_name") or "未命名场景"
        scene_id = scene.get("scene_id") or "unknown"
        line = f"{idx}. {scene_name}  [scene_id={scene_id}]"
        if include_home:
            home_name = scene.get("home_name") or "未知家庭"
            line += f"  (家庭: {home_name})"
        return line

    def _format_alias_line(self, idx: int, alias: str, did: str, category_map: Dict[str, str]) -> str:
        configured_category = normalize_category(category_map.get(alias, CATEGORY_NONE))
        model = self._get_model_by_did(did)
        effective_category = resolve_effective_category(model=model, category=configured_category)
        cloud_name = self._get_cloud_name_by_did(did)

        parts = [f"{idx}. {alias}"]
        if cloud_name and cloud_name != alias:
            parts.append(f"(云端名: {cloud_name})")
        if effective_category != CATEGORY_NONE:
            parts.append(f"[类别: {effective_category}]")
        return " ".join(parts)

    @staticmethod
    def _format_scene_error(error: Exception) -> str:
        reason = str(error)
        if reason == "scene_home_missing":
            return "场景缓存缺少家庭 ID，请先刷新米家场景列表后重试。"
        if reason == "scene_rejected":
            return "米家云端拒绝执行该场景。"
        if reason == "scene_unconfirmed":
            return "米家云端没有确认场景执行成功，请检查设备真实状态。"
        if reason == "cloud_no_response":
            return "米家云端没有返回有效结果，请检查场景、设备和网络状态。"
        return "场景执行失败，请查看诊断信息。"

    async def _resolve_scene_query(self, query: str, prefer_cache: bool = False) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        query = str(query or "").strip()
        if not query:
            return None, "empty"

        scenes = (
            self._get_cached_scenes()
            if prefer_cache
            else await self.client.get_scenes()
        )

        if not scenes:
            return None, "empty_list"

        exact_id = [s for s in scenes if str(s.get("scene_id", "")).strip() == query]
        if exact_id:
            return exact_id[0], None

        exact_name = [s for s in scenes if str(s.get("scene_name", "")).strip() == query]
        if len(exact_name) == 1:
            return exact_name[0], None
        if len(exact_name) > 1:
            lines = [
                f"⚠️ 场景名“{query}”命中了多个结果，请改用 scene_id 执行："
            ]
            for idx, item in enumerate(exact_name, 1):
                lines.append(self._format_scene_line(idx, item))
            lines.append("\n💡 建议：")
            lines.append("- 优先使用 scene_id 执行")
            lines.append("- 或在米家 App 中将同名场景改成更容易区分的名称")
            return None, "\n".join(lines)

        return None, "not_found"

    async def _render_readonly_status_by_alias(self, alias: str) -> str:
        device_map = self._parse_device_map()
        category_map = self._parse_category_map()

        alias = str(alias or "").strip()
        if not alias:
            return "device_alias 不能为空。"

        if alias not in device_map:
            return (
                f"未找到已配置别名：{alias}。\n"
                f"请先调用 list_configured_mihome_aliases 查看当前可读取的设备别名，并使用其中一个精确别名。"
            )

        did = device_map[alias]
        configured_category = normalize_category(category_map.get(alias, CATEGORY_NONE))
        model = self._get_model_by_did(did)
        effective_category = resolve_effective_category(model=model, category=configured_category)
        cloud_name = self._get_cloud_name_by_did(did)

        if effective_category == CATEGORY_NONE:
            return (
                f"设备别名“{alias}”尚未配置有效设备类别，当前不开放给只读 LLM Tool。\n"
                f"请先在 device_category_map 中为该别名配置明确类别后再读取。"
            )

        if effective_category not in READONLY_ALLOWED_CATEGORIES:
            return (
                f"设备别名“{alias}”所属类别为“{effective_category}”，当前不在只读 Tool 开放范围内。"
            )

        readable_keys = get_device_detail_readable_keys(model=model, category=effective_category)
        display_map = get_device_display_map(model=model, category=effective_category)
        value_display_map = get_device_value_display_map(model=model, category=effective_category)

        if not readable_keys:
            return (
                f"设备别名“{alias}”当前没有预定义的可读状态模板，暂不支持通过只读 Tool 查询。"
            )

        props_data = await self.client.get_device_props(did, readable_keys=readable_keys)
        error_msg = props_data.get("__error__")
        if error_msg:
            return (
                f"读取设备“{alias}”实时状态失败：{error_msg}\n"
                f"说明：该 Tool 不使用缓存，只读取当前实时状态。"
            )

        readables = props_data.get("readable", {})
        readable_keys_missing = props_data.get("readable_keys", [])

        lines = [f"设备别名：{alias}"]
        if cloud_name and cloud_name != alias:
            lines.append(f"云端名称：{cloud_name}")
        lines.append(f"设备类别：{effective_category}")
        lines.append(f"读取时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        if readables:
            lines.append("当前状态：")
            translated_items = []
            for k, v in readables.items():
                friendly_name = display_map.get(k, k)
                friendly_val = self._translate_readable_value(k, v, value_display_map)
                translated_items.append((friendly_name, friendly_val))
            translated_items.sort(key=lambda x: x[0])

            for idx, (name, val) in enumerate(translated_items):
                prefix = " └─ " if idx == len(translated_items) - 1 else " ├─ "
                lines.append(f"{prefix}{name}: {val}")
        else:
            lines.append("当前状态：暂无可读取到的实时数据。")

        filtered_missing = [k for k in readable_keys_missing if k in readable_keys]
        if filtered_missing:
            translated_missing = [display_map.get(k, k) for k in filtered_missing]
            lines.append("")
            lines.append("以下状态项当前无数据或读取失败：")
            lines.append(", ".join(translated_missing))

        lines.append("")
        lines.append("说明：本结果为实时读取，不使用缓存；且仅允许从已配置别名中检索设备。")
        return "\n".join(lines)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("米家登录")
    async def mihome_login(self, event: AstrMessageEvent):
        """引导管理员在内置管理页安全授权米家账号"""
        yield event.plain_result(
            "🔐 为避免二维码票据进入群聊或平台消息日志，v8 不再通过聊天发送"
            "米家登录链接。\n"
            "请打开 AstrBot WebUI → 插件管理 → astrbot_plugin_mihome → "
            "米家管理，在“概览”中扫码授权。"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("米家状态")
    async def mihome_status(self, event: AstrMessageEvent):
        """查看登录、共享设备与最近控制状态"""
        s = await self.client.get_login_status()
        last_device = s["last_control_device"] or "无"
        last_result = "未发生" if not s["last_control_device"] else ("失败" if s["last_control_error"] else "成功")
        yield event.plain_result(
            f"📊 状态报告：\n"
            f"- 凭证存在: {s['auth_exists']}\n"
            f"- 登录异常: {s['last_login_error'] or '无'}\n"
            f"- 共享异常: {s['last_shared_error'] or '无'}\n"
            f"- 最近控制: {last_device} ({last_result})\n"
            f"- 场景缓存时间: {s.get('scene_cache_updated_at') or '无'}"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("米家登出")
    async def mihome_logout(self, event: AstrMessageEvent):
        """清除米家登录凭证与本地状态"""
        yield event.plain_result("⏳ 正在登出...")
        try:
            ok = await self.web_api.logout_account()
            yield event.plain_result("✅ 登出成功，凭证及状态已重置。" if ok else "⚠️ 凭证不存在，已重置现场。")
        except Exception as e:
            logger.error(f"[MiHome] 登出失败: {type(e).__name__}")
            yield event.plain_result(
                "❌ 登出失败，请检查插件数据目录权限后重试。"
            )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("刷新米家")
    async def refresh_mihome_devices(self, event: AstrMessageEvent):
        """同步米家设备列表并刷新 DID、model 缓存"""
        yield event.plain_result("⏳ 正在同步设备列表...")
        device_map = self._parse_device_map()

        try:
            devs = await self.client.get_devices()
            if not devs:
                yield event.plain_result("✅ 拉取成功，未发现可用设备。")
                return

            res = [f"✅ 找到 {len(devs)} 个设备："]
            for i, d in enumerate(devs, 1):
                did_str = str(d.get("did", "")).strip()
                cloud_name = str(d.get("name", "未知设备")).strip() or "未知设备"

                model_str = str(d.get("model", "")).strip()
                if not model_str and did_str:
                    model_str = self._get_model_by_did(did_str)
                model_str = model_str or "未知"

                is_online = d.get("isOnline")
                if is_online is True:
                    status_icon = "🟢"
                elif is_online is False:
                    status_icon = "🔴"
                else:
                    status_icon = "⚪"

                aliases = [k for k, v in device_map.items() if str(v).strip() == did_str]
                alias_str = "/".join(aliases) if aliases else "未配置别名"

                res.append(
                    f"{i}. 【{alias_str}】({cloud_name}) [{status_icon}]\n"
                    f"   DID: {did_str or '未知'}\n"
                    f"   model: {model_str}"
                )

            res.append("\n💡 提示: 发送 /米家详情 [别名] 可查看设备实况，发送 /米家帮助 [别名] 获取控制示例，发送 /米家场景列表 查看云端场景。")
            yield event.plain_result("\n".join(res))
        except MiHomeClientError as e:
            logger.warning(
                f"[MiHome] 同步设备失败: {type(e).__name__}"
            )
            yield event.plain_result("❌ 同步设备失败，请查看米家管理诊断。")
        except Exception as e:
            logger.error(
                f"[MiHome] 未知同步异常: {type(e).__name__}"
            )
            yield event.plain_result("❌ 同步设备时发生内部错误。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("米家场景列表")
    async def mihome_scene_list(self, event: AstrMessageEvent):
        """同步并列出可执行的米家云端场景"""
        yield event.plain_result("⏳ 正在同步米家云端场景列表...")
        try:
            scenes = await self.client.get_scenes()
            if not scenes:
                yield event.plain_result("⚠️ 当前账号下未发现可执行场景。")
                return

            lines = [f"✅ 找到 {len(scenes)} 个场景："]
            for idx, item in enumerate(scenes, 1):
                lines.append(self._format_scene_line(idx, item, include_home=True))

            lines.append("\n💡 执行方式：")
            lines.append("- /米家场景 场景名")
            lines.append("- /米家场景 scene_id")
            lines.append("⚠️ 若存在同名场景，系统会要求你改用 scene_id 执行。")
            lines.append("🧠 场景缓存已更新，可供大模型场景 Tool 使用。")
            yield event.plain_result("\n".join(lines))
        except MiHomeAuthError:
            yield event.plain_result("❌ 鉴权失效，请重新登录。")
        except MiHomeClientError as e:
            logger.warning(
                f"[MiHome] 获取场景失败: {type(e).__name__}"
            )
            yield event.plain_result("❌ 获取场景失败，请查看米家管理诊断。")
        except Exception as e:
            logger.error(
                f"[MiHome] 获取场景内部错误: {type(e).__name__}"
            )
            yield event.plain_result("❌ 获取场景时发生内部错误。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("米家场景")
    async def mihome_scene_run(self, event: AstrMessageEvent):
        """按场景名称或 scene_id 执行米家云端场景"""
        msg = event.message_str.strip()
        cmd_prefix = r"^/?米家场景\s*"
        content = re.sub(cmd_prefix, "", msg).strip()

        if not content:
            yield event.plain_result(
                "❌ 缺少参数。\n"
                "格式：/米家场景 [场景名或scene_id]\n"
                "示例：\n"
                "/米家场景 晚安模式\n"
                "/米家场景 123456789"
            )
            return

        try:
            scene, err = await self._resolve_scene_query(content, prefer_cache=False)
            if err == "empty_list":
                yield event.plain_result("⚠️ 当前账号下未发现可执行场景。")
                return
            if err == "not_found":
                yield event.plain_result(
                    f"❌ 未找到场景：{content}\n"
                    f"💡 可先发送 /米家场景列表 查看当前账号下的场景与 scene_id。"
                )
                return
            if err and err not in ("empty", "empty_list", "not_found"):
                yield event.plain_result(err)
                return
            if not scene:
                yield event.plain_result("❌ 无法解析目标场景。")
                return

            scene_name = scene.get("scene_name") or "未命名场景"
            scene_id = scene.get("scene_id") or ""
            home_id = scene.get("home_id") or ""

            yield event.plain_result(f"⏳ 正在执行米家场景【{scene_name}】...")
            await self.client.run_scene(
                scene_id=scene_id,
                home_id=home_id,
                scene_name=scene_name,
            )
            yield event.plain_result(f"✅ 场景执行成功：{scene_name}")
        except MiHomeAuthError:
            yield event.plain_result("❌ 鉴权失效，请重新登录。")
        except MiHomeSceneError as e:
            yield event.plain_result(f"❌ {self._format_scene_error(e)}")
        except MiHomeClientError as e:
            logger.warning(
                f"[MiHome] 场景执行客户端异常: {type(e).__name__}"
            )
            yield event.plain_result("❌ API 或网络异常，请稍后重试。")
        except Exception as e:
            logger.error(
                f"[MiHome] 场景执行内部错误: {type(e).__name__}"
            )
            yield event.plain_result("❌ 场景执行时发生内部错误。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("米家详情")
    async def mihome_device_detail(self, event: AstrMessageEvent):
        """查看指定米家设备的能力与实时状态"""
        device_map = self._parse_device_map()
        category_map = self._parse_category_map()

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
        configured_category = normalize_category(category_map.get(alias, CATEGORY_NONE))
        model = self._get_model_by_did(did)
        model_hit = has_model_profile(model)
        hidden_props = set(get_model_hidden_props(model))
        category = resolve_effective_category(model=model, category=configured_category)
        cloud_name = self._get_cloud_name_by_did(did)

        if category == CATEGORY_NONE:
            yield event.plain_result(f"⏳ 正在探测【{alias}】的能力菜单...")

            cap = await self.client.get_device_capabilities(did)
            if cap.get("__error__"):
                yield event.plain_result(
                    f"⚠️ 【{alias}】能力探测失败:\n"
                    f" └─ 原因: {cap['__error__']}"
                )
                return

            all_props = cap.get("all_props", [])
            actions = [
                action
                for action in cap.get("actions", [])
                if not is_denied_device_action(action)
            ]
            lines = [f"✅ 【{alias}】支持的高级能力:"]
            if cloud_name and cloud_name != alias:
                lines.insert(1, f"☁️ 云端名称: {cloud_name}")

            if all_props:
                lines.append("属性: " + ", ".join(all_props))
            if actions:
                lines.append("动作: " + ", ".join(actions))

            if len(lines) > 1:
                yield event.plain_result("\n".join(lines))
            else:
                yield event.plain_result(
                    f"⚠️ 【{alias}】当前未探测到可用属性菜单。\n"
                    f"💡 可能设备离线、深度休眠，或当前型号暂不支持展开图纸。"
                )
            return

        display_map = get_device_display_map(model=model, category=category)
        value_display_map = get_device_value_display_map(model=model, category=category)
        reverse_prop_map = get_reverse_prop_map(model=model, category=category)
        reverse_action_map = get_reverse_action_map(model=model, category=category)
        fallback_writables = get_device_detail_writable_keys(model=model, category=category)
        fallback_readables = get_device_detail_readable_keys(model=model, category=category)
        fallback_actions = [
            action
            for action in get_device_detail_actions(model=model, category=category)
            if not is_denied_device_action(action)
        ]

        stage1_lines = [f"📖 【{alias}】:"]

        if cloud_name and cloud_name != alias:
            stage1_lines.append(f"☁️ 云端名称: {cloud_name}")

        translated_controls = []
        if fallback_writables:
            translated_controls.extend(reverse_prop_map.get(w, w) for w in fallback_writables)
        if fallback_actions:
            translated_controls.extend(reverse_action_map.get(a, a) for a in fallback_actions)

        if translated_controls:
            translated_controls = sorted(set(translated_controls))
            stage1_lines.append("✅ 可调属性: " + ", ".join(translated_controls))

        if fallback_readables:
            translated_readables = sorted(set(display_map.get(k, k) for k in fallback_readables))
            stage1_lines.append("📡 状态传感: " + ", ".join(translated_readables))

        stage1_lines.append("\n⏳ 正在向米家云端精准读取实时数据，请稍候...")
        yield event.plain_result("\n".join(stage1_lines))

        try:
            props_data = await self.client.get_device_props(did, readable_keys=fallback_readables)
            error_msg = props_data.get("__error__")
            stage2_lines = []

            if error_msg:
                cap = await self.client.get_device_capabilities(did)
                raw_items = cap.get("all_props", [])

                if model_hit:
                    raw_items = [k for k in raw_items if k in fallback_readables]

                stage2_lines.append("📡 已知状态项 (当前实况获取失败或无数据):")
                if raw_items:
                    stage2_lines.append(", ".join(raw_items))
                else:
                    stage2_lines.append(f"└─ 原因: {error_msg}")

                yield event.plain_result("\n".join(stage2_lines))
                return

            readables = props_data.get("readable", {})
            readable_keys = props_data.get("readable_keys", [])

            if readables:
                stage2_lines.append(f"📊 【{alias}】实时状态:")
                translated_items = []
                for k, v in readables.items():
                    friendly_name = display_map.get(k, k)
                    friendly_val = self._translate_readable_value(k, v, value_display_map)
                    translated_items.append((friendly_name, friendly_val))
                translated_items.sort(key=lambda x: x[0])
                for idx, (name, val) in enumerate(translated_items):
                    prefix = " └─ " if idx == len(translated_items) - 1 else " ├─ "
                    stage2_lines.append(f"{prefix}{name}: {val}")

            filtered_missing = [k for k in readable_keys if k in fallback_readables]
            if filtered_missing:
                if stage2_lines:
                    stage2_lines.append("")
                stage2_lines.append("📡 已知状态项 (当前实况获取失败或无数据):")
                stage2_lines.append(", ".join(filtered_missing))

            if not model_hit:
                cap = await self.client.get_device_capabilities(did)
                all_props = set(cap.get("all_props", []))
                all_props = {k for k in all_props if k not in hidden_props}
                known_template = set(fallback_writables) | set(fallback_readables)
                extra_raw = sorted(all_props - known_template)

                if extra_raw:
                    if stage2_lines:
                        stage2_lines.append("")
                    stage2_lines.append("🔍 未纳入当前中文模板的原始属性:")
                    stage2_lines.append(", ".join(extra_raw))

            if not stage2_lines:
                cap = await self.client.get_device_capabilities(did)
                all_props = set(cap.get("all_props", []))
                if model_hit:
                    all_props = {k for k in all_props if k in set(fallback_readables) | set(fallback_writables)}
                else:
                    all_props = {k for k in all_props if k not in hidden_props}

                if all_props:
                    stage2_lines.append("📡 已知状态项 (当前实况获取失败或无数据):")
                    stage2_lines.append(", ".join(sorted(all_props)))
                else:
                    stage2_lines.append(f"✅ 【{alias}】在线就绪，但当前无实况数据返回。")

            yield event.plain_result("\n".join(stage2_lines))

        except Exception as e:
            logger.error(
                f"[MiHome] 获取属性异常: {type(e).__name__}"
            )
            yield event.plain_result("❌ 读取设备状态时发生内部错误。")

    @filter.command("米家帮助", alias={"mihome_help", "mihomehelp"})
    async def mihome_control_help(self, event: AstrMessageEvent):
        """查看米家插件指令总览，或按设备别名查看控制示例"""

        msg = event.message_str.strip()
        cmd_prefix = r"^/?(?:米家帮助|mihome_help|mihomehelp)\s*"
        content = re.sub(cmd_prefix, "", msg).strip()

        if not content:
            yield event.plain_result(self._render_command_help())
            return

        device_map = self._parse_device_map()
        category_map = self._parse_category_map()

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
        configured_category = normalize_category(category_map.get(alias, CATEGORY_NONE))
        model = self._get_model_by_did(did)
        category = resolve_effective_category(model=model, category=configured_category)

        if category == CATEGORY_NONE:
            yield event.plain_result(
                f"⚠️ 【{alias}】未配置有效设备类别，以下为通用控制格式：\n\n"
                f"基础开关:\n"
                f"- /米家控制 {alias} 开\n"
                f"- /米家控制 {alias} 关\n\n"
                f"高级格式:\n"
                f"- /米家控制 {alias} [属性] [值]\n"
                f"- /米家控制 {alias} [原始动作名]\n\n"
                f"💡 若你已知道设备的原始英文属性或动作，可直接透传，例如：\n"
                f"- /米家控制 {alias} mode 1\n"
                f"- /米家控制 {alias} start_sweep"
            )
            return

        reverse_prop_map = get_reverse_prop_map(model=model, category=category)
        reverse_action_map = get_reverse_action_map(model=model, category=category)
        fallback_writables = get_device_detail_writable_keys(model=model, category=category)
        fallback_actions = [
            action
            for action in get_device_detail_actions(model=model, category=category)
            if not is_denied_device_action(action)
        ]
        help_examples = get_device_help_examples(model=model, category=category)
        action_examples = get_device_action_examples(model=model, category=category)
        help_hints = get_device_help_hints(model=model, category=category)

        msg_lines = [f"✅ 【{alias}】控制指南:"]

        if fallback_writables:
            translated_writables = sorted(set(reverse_prop_map.get(w, w) for w in fallback_writables))
            msg_lines.append("支持控制的属性:")
            msg_lines.append(", ".join(translated_writables))
            msg_lines.append("")

        if fallback_actions:
            translated_actions = sorted(set(reverse_action_map.get(a, a) for a in fallback_actions))
            msg_lines.append("支持执行的动作:")
            msg_lines.append(", ".join(translated_actions))
            msg_lines.append("")

        msg_lines.append("常用控制示例:")

        if "on" in fallback_writables:
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

        if action_examples:
            for act_cn in action_examples:
                msg_lines.append(f"- /米家控制 {alias} {act_cn}")

        if len(msg_lines) <= 2:
            msg_lines.append("该设备当前以状态查看为主，暂无推荐控制项。")
            msg_lines.append(f"💡 可发送 /米家详情 {alias} 查看实时状态。")

        yield event.plain_result("\n".join(msg_lines))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("米家控制")
    async def control_mihome_device(self, event: AstrMessageEvent):
        """控制指定米家设备的开关、属性或动作"""
        device_map = self._parse_device_map()
        category_map = self._parse_category_map()

        msg = event.message_str.strip()
        cmd_prefix = r"^/?米家控制\s*"
        content = re.sub(cmd_prefix, "", msg).strip()

        if not content:
            yield event.plain_result(
                "❌ 缺少参数。\n"
                "格式：/米家控制 [设备名] [动作/属性] [值]\n"
                "示例：\n"
                "/米家控制 空调 开\n"
                "/米家控制 空调 温度 26\n"
                "/米家控制 扫地机 开始清扫"
            )
            return

        try:
            parts = shlex.split(content)
        except Exception as e:
            logger.warning(
                f"[MiHome] shlex 解析异常: {type(e).__name__}"
            )
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
        configured_category = normalize_category(category_map.get(alias, CATEGORY_NONE))
        model = self._get_model_by_did(did)
        category = resolve_effective_category(model=model, category=configured_category)

        prop_map = get_device_prop_map(model=model, category=category)
        val_map = get_device_val_map(model=model, category=category)
        action_map = {
            alias: action
            for alias, action in get_device_action_map(
                model=model,
                category=category,
            ).items()
            if not is_denied_device_action(action)
        }

        prop_alias_norm = {str(k).strip().lower(): v for k, v in prop_map.items()}

        action_alias_norm = {
            self._normalize_action_token(k): v
            for k, v in action_map.items()
        }
        action_raw_norm = {
            self._normalize_action_token(v): v
            for v in action_map.values()
        }

        capability_actions = {}
        try:
            cap = await self.client.get_device_capabilities(did)
            for act in cap.get("actions", []):
                if is_denied_device_action(act):
                    continue
                capability_actions[self._normalize_action_token(act)] = act
        except Exception as e:
            logger.debug(
                f"[MiHome] 动态动作菜单探测失败: {type(e).__name__}"
            )

        full_command_norm = self._normalize_action_token(" ".join(remaining_parts))
        compact_command_norm = self._normalize_action_token("".join(remaining_parts))

        matched_action = None
        if full_command_norm in action_alias_norm:
            matched_action = action_alias_norm[full_command_norm]
        elif compact_command_norm in action_alias_norm:
            matched_action = action_alias_norm[compact_command_norm]
        elif full_command_norm in action_raw_norm:
            matched_action = action_raw_norm[full_command_norm]
        elif compact_command_norm in action_raw_norm:
            matched_action = action_raw_norm[compact_command_norm]
        elif full_command_norm in capability_actions:
            matched_action = capability_actions[full_command_norm]
        elif compact_command_norm in capability_actions:
            matched_action = capability_actions[compact_command_norm]

        if matched_action:
            if is_denied_device_action(matched_action):
                yield event.plain_result("❌ 该动作涉及跨设备指令，插件已拒绝执行。")
                return
            yield event.plain_result(f"⏳ 正在向【{alias}】执行动作 [{matched_action}]...")
            try:
                confirmed = await self.client.run_action(did, matched_action, alias)
                yield event.plain_result(
                    "✅ 动作执行成功！"
                    if confirmed
                    else "⚠️ 米家网关已接收动作，但无法确认设备是否执行。"
                )
            except MiHomeAuthError:
                yield event.plain_result("❌ 鉴权失效，请重新登录。")
            except MiHomeControlError as e:
                err = str(e)
                if err == "device_not_found":
                    yield event.plain_result("❌ 云端找不到设备。")
                elif err == "cloud_no_response":
                    yield event.plain_result("❌ 米家云端没有返回有效数据，请稍后重试。")
                elif err == "device_rejected":
                    yield event.plain_result(
                        f"❌ 设备拒绝执行该动作。\n💡 提示: 发送 /米家帮助 {alias} 检查动作是否支持。"
                    )
                else:
                    yield event.plain_result("❌ 动作执行失败，请检查设备能力与参数。")
            except MiHomeClientError as e:
                logger.warning(
                    f"[MiHome] 动作执行客户端异常: {type(e).__name__}"
                )
                yield event.plain_result("❌ API 或网络异常，请稍后重试。")
            except Exception:
                yield event.plain_result("❌ 内部错误。")
            return

        if len(remaining_parts) == 1:
            token = remaining_parts[0]
            token_lower = token.lower()
            token_action_norm = self._normalize_action_token(token)

            prop_values_lower = {str(v).lower() for v in prop_map.values()}
            is_prop_candidate = (token_lower in prop_alias_norm) or (token_lower in prop_values_lower)
            is_action_candidate = (
                token_action_norm in action_alias_norm
                or token_action_norm in action_raw_norm
                or token_action_norm in capability_actions
            )

            if token_lower in self.action_alias:
                yield event.plain_result(f"⏳ 正在向【{alias}】下发开关指令...")
                try:
                    confirmed = await self.client.control_power(
                        did,
                        self.action_alias[token_lower],
                        alias,
                    )
                    yield event.plain_result(
                        "✅ 成功！"
                        if confirmed
                        else "⚠️ 米家网关已接收指令，但无法确认设备是否执行。"
                    )
                except MiHomeAuthError:
                    yield event.plain_result("❌ 鉴权失效，请重新登录。")
                except MiHomeControlError as e:
                    err = str(e)
                    if err == "device_not_found":
                        yield event.plain_result("❌ 云端找不到设备或权限受限。")
                    elif err == "cloud_no_response":
                        yield event.plain_result("❌ 米家云端没有返回有效数据，请稍后重试。")
                    elif err == "device_rejected":
                        yield event.plain_result(
                            f"❌ 设备在线但拒绝了请求。\n💡 提示: 发送 /米家帮助 {alias} 检查指令是否越界。"
                        )
                    else:
                        yield event.plain_result("❌ 控制失败，请检查设备能力与参数。")
                except MiHomeClientError as e:
                    logger.warning(
                        f"[MiHome] 开关控制客户端异常: {type(e).__name__}"
                    )
                    yield event.plain_result("❌ API 或网络异常，请稍后重试。")
                except Exception:
                    yield event.plain_result("❌ 内部错误。")
                return

            elif is_action_candidate:
                eng_action = (
                    action_alias_norm.get(token_action_norm)
                    or action_raw_norm.get(token_action_norm)
                    or capability_actions.get(token_action_norm)
                )
                if is_denied_device_action(eng_action):
                    yield event.plain_result("❌ 该动作涉及跨设备指令，插件已拒绝执行。")
                    return
                yield event.plain_result(f"⏳ 正在向【{alias}】执行动作 [{eng_action}]...")
                try:
                    confirmed = await self.client.run_action(did, eng_action, alias)
                    yield event.plain_result(
                        "✅ 动作执行成功！"
                        if confirmed
                        else "⚠️ 米家网关已接收动作，但无法确认设备是否执行。"
                    )
                except MiHomeAuthError:
                    yield event.plain_result("❌ 鉴权失效，请重新登录。")
                except MiHomeControlError as e:
                    err = str(e)
                    if err == "device_not_found":
                        yield event.plain_result("❌ 云端找不到设备。")
                    elif err == "cloud_no_response":
                        yield event.plain_result("❌ 米家云端没有返回有效数据，请稍后重试。")
                    elif err == "device_rejected":
                        yield event.plain_result(
                            f"❌ 设备拒绝执行该动作。\n💡 提示: 发送 /米家帮助 {alias} 检查动作是否支持。"
                        )
                    else:
                        yield event.plain_result("❌ 动作执行失败，请检查设备能力与参数。")
                except MiHomeClientError as e:
                    logger.warning(
                        f"[MiHome] 动作执行客户端异常: {type(e).__name__}"
                    )
                    yield event.plain_result("❌ API 或网络异常，请稍后重试。")
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

        raw_prop = remaining_parts[0]
        raw_val_str = " ".join(remaining_parts[1:])

        prop = prop_alias_norm.get(raw_prop.strip().lower(), raw_prop.strip())

        raw_val_norm = raw_val_str.strip()
        property_val_map = get_device_property_value_map(
            model=model,
            category=category,
            property_key=prop,
        )
        val_alias_norm = {str(k).strip().lower(): v for k, v in val_map.items()}
        val_alias_norm.update(
            {
                str(k).strip().lower(): v
                for k, v in property_val_map.items()
            }
        )
        val_mapped = val_alias_norm.get(raw_val_norm.lower(), raw_val_norm)

        val = self._parse_value(val_mapped)

        yield event.plain_result(f"⏳ 正在向【{alias}】尝试下发属性 [{prop}]={val}...")
        try:
            confirmed = await self.client.set_property(did, prop, val, alias)
            yield event.plain_result(
                "✅ 属性下发成功！"
                if confirmed
                else "⚠️ 米家网关已接收属性指令，但无法确认设备是否执行。"
            )
        except MiHomeAuthError:
            yield event.plain_result("❌ 鉴权失效，请重新登录。")
        except MiHomeControlError as e:
            err = str(e)
            if err == "device_not_found":
                yield event.plain_result("❌ 云端找不到设备。")
            elif err == "cloud_no_response":
                yield event.plain_result("❌ 米家云端没有返回有效数据，请稍后重试。")
            elif err == "device_rejected":
                yield event.plain_result(
                    f"❌ 设备拒绝请求 (可能值越界或为只读属性)。\n💡 提示: 发送 /米家帮助 {alias} 检查正确用法。"
                )
            else:
                yield event.plain_result("❌ 设置失败，请检查设备能力与属性值。")
        except MiHomeClientError as e:
            logger.warning(
                f"[MiHome] 属性控制客户端异常: {type(e).__name__}"
            )
            yield event.plain_result("❌ API 或网络异常，请稍后重试。")
        except Exception:
            yield event.plain_result("❌ 内部错误。")

    @filter.llm_tool(name="list_configured_mihome_aliases")
    async def list_configured_mihome_aliases_tool(self, event: AstrMessageEvent) -> str:
        """
        列出当前插件中已配置的米家设备别名（只读工具）。
        使用限制：
        1. 仅返回 device_map 中已显式配置的设备别名，未配置别名不在返回范围内。
        2. 该工具只列出别名，设备控制与实时状态读取由其他工具负责。
        3. 当你需要确定某个设备的精确别名时，才应调用本工具。
        4. 若用户只是普通聊天或寒暄，不应调用本工具。
        """
        if not self._readonly_tool_enabled():
            return "米家设备只读 Tool 当前未启用。"

        device_map = self._parse_device_map()
        category_map = self._parse_category_map()

        if not device_map:
            return "当前没有已配置的米家设备别名，请先在插件配置的 device_map 中添加别名。"

        lines = [f"当前已配置 {len(device_map)} 个米家设备别名："]
        for idx, alias in enumerate(sorted(device_map.keys()), 1):
            did = device_map[alias]
            lines.append(self._format_alias_line(idx, alias, did, category_map))

        lines.append("")
        lines.append("说明：只读查询工具只能从以上别名中检索设备。")
        return "\n".join(lines)

    @filter.llm_tool(name="read_mihome_device_status_by_alias")
    async def read_mihome_device_status_by_alias_tool(self, event: AstrMessageEvent, device_alias: str) -> str:
        """
        按设备别名实时读取米家设备当前状态（只读工具）。
        使用限制（必须遵守）：
        1. 该工具只允许读取状态，不允许执行任何控制、动作或场景。
        2. 该工具只能从 device_map 中已配置的精确别名中检索设备，不能读取未配置别名的设备。
        3. 该工具每次调用均实时读取设备状态。
        4. 若不确定设备精确别名，应先调用 list_configured_mihome_aliases 获取别名列表。
        5. 优先用于温湿度、空气质量、电源状态、工作模式等状态查询场景。
        Args:
            device_alias(string): 需要读取状态的设备精确别名，必须来自 device_map
        """
        if not self._readonly_tool_enabled():
            return "米家设备只读 Tool 当前未启用。"

        try:
            return await self._render_readonly_status_by_alias(device_alias)
        except MiHomeAuthError:
            return "米家登录已失效，请先重新登录。"
        except MiHomeClientError as e:
            logger.warning(
                f"[MiHome] 只读 Tool 客户端异常: {type(e).__name__}"
            )
            return "读取设备状态失败，请稍后重试或查看米家管理诊断。"
        except Exception as e:
            logger.error(
                f"[MiHome] 只读 Tool 内部错误: {type(e).__name__}"
            )
            return "读取设备状态时发生内部错误。"

    @filter.llm_tool(name="list_cached_mihome_scenes")
    async def list_cached_mihome_scenes_tool(self, event: AstrMessageEvent) -> str:
        """
        读取本插件缓存的米家场景列表（只读工具）。

        使用限制：
        1. 仅用于查询当前已同步到插件缓存中的米家场景，不实时访问云端。
        2. 当用户明确提到“场景”、要求执行家居控制、或你需要确认可执行场景名称时，才应调用本工具。
        3. 不要因为普通寒暄或自然表达（例如“晚安”“早安”“我要睡了”“我出门了”）就主动调用本工具。
        4. 若缓存为空，应提示用户先手动执行 /米家场景列表 完成同步。
        5. 本工具只负责“列出可用场景”，不代表任何场景已经执行成功。
        6. 在未调用 execute_mihome_scene 且未收到明确成功结果前，不得向用户声称任何家居动作已经完成。

        返回结果使用要求：
        1. 若仅调用本工具，你只能告诉用户“有哪些场景可用”。
        2. 不允许因为看到了类似“关净化器”“晚安模式”的场景名，就直接说“已经帮你执行了”。
        3. 场景是否真正执行成功，必须以后续 execute_mihome_scene 工具返回结果为准。
        """
        deny_msg = self._check_scene_tool_access(event)
        if deny_msg:
            return deny_msg

        scenes = self._get_cached_scenes()
        updated_at = self._get_scene_cache_updated_at()

        if not scenes:
            return "当前没有已缓存的米家场景列表，请先手动执行 /米家场景列表 同步场景。"

        lines = [f"当前已缓存 {len(scenes)} 个米家场景："]
        for idx, item in enumerate(scenes, 1):
            lines.append(self._format_scene_line(idx, item))
        if updated_at:
            lines.append(f"\n缓存更新时间：{updated_at}")

        return "\n".join(lines)

    @filter.llm_tool(name="execute_mihome_scene")
    async def execute_mihome_scene_tool(self, event: AstrMessageEvent, scene_name: str) -> str:
        """
        执行米家云端场景。

        使用限制（必须遵守）：
        1. 仅当用户明确要求执行某个场景，或明确表达家居电器控制意图时，才可以调用本工具。
        2. 允许的典型情况包括：
           - “执行晚安场景”
           - “帮我关下灯”
           - “帮我把净化器关了”
           - “执行离家场景”
        3. 禁止在以下情况下调用本工具：
           - 普通寒暄或礼貌表达，如“晚安”“早安”
           - 单纯状态表达，如“我要睡了”“我要出门了”
           - 没有明确家居控制意图的日常聊天
        4. 若不确定应执行哪个场景，应先调用 list_cached_mihome_scenes 查询缓存场景列表，再决定是否执行。
        5. 若缓存中存在同名场景导致歧义，不应擅自执行，应提示用户改用更明确的场景名或 scene_id。

        结果确认规则（必须遵守）：
        1. 在本工具真正返回结果之前，禁止向用户声称“已经执行成功”“已经打开”“已经关闭”“已经完成”。
        2. 调用本工具前，只能使用类似“我来试试”“我先帮你执行”“我先检查一下”的表述。
        3. 只有当本工具返回明确成功结果后，才可以告诉用户场景已经执行成功。
        4. 如果本工具返回未找到场景、缓存为空、执行失败、权限不足、参数错误等结果，必须如实告诉用户失败原因。
        5. 不允许在工具执行前预设成功结果，不允许先说“净化器开了”“场景执行好了”再调用工具。
        6. 若当前只有“关净化器”场景，而没有“开净化器”场景，不得伪装成已经打开净化器，必须明确说明未找到对应场景。
        7. 若用户表达的是“帮我开一下净化器”“帮我开灯”这类控制意图，而当前缓存中没有可直接匹配的开启场景，也不得脑补成功，必须诚实说明当前无法完成，并建议用户补充场景或改用明确指令。

        输出风格要求：
        1. 在工具执行前，禁止输出“好了”“已完成”“成功了”这类结论性表述。
        2. 若需要先回复一句过渡语，只能使用简短中性表述，例如：
           - “我来试试。”
           - “稍等，我帮你执行。”
           - “我先检查一下有没有对应场景。”
        3. 若工具执行失败，必须直接说明失败，不要用含糊话术掩盖失败结果。
        Args:
            scene_name(string): 需要执行的米家场景名称或 scene_id
        """
        deny_msg = self._check_scene_tool_access(event)
        if deny_msg:
            return deny_msg

        try:
            scene, err = await self._resolve_scene_query(scene_name, prefer_cache=True)

            if err == "empty_list":
                return "当前没有已缓存的米家场景列表，请先手动执行 /米家场景列表 同步场景。"
            if err == "not_found":
                return f"未在缓存中找到米家场景：{scene_name}。请先确认场景名，或先执行 /米家场景列表 刷新。"
            if err and err not in ("empty", "empty_list", "not_found"):
                return err
            if not scene:
                return "无法解析要执行的米家场景。"

            final_scene_name = scene.get("scene_name") or "未命名场景"
            final_scene_id = scene.get("scene_id") or ""
            final_home_id = scene.get("home_id") or ""

            await self.client.run_scene(
                scene_id=final_scene_id,
                home_id=final_home_id,
                scene_name=final_scene_name,
            )
            return f"已成功执行米家场景：{final_scene_name}"
        except MiHomeAuthError:
            return "米家登录已失效，请先重新登录。"
        except MiHomeSceneError as e:
            return self._format_scene_error(e)
        except MiHomeClientError as e:
            logger.warning(
                f"[MiHome] 场景 Tool 客户端异常: {type(e).__name__}"
            )
            return "场景执行失败，请稍后重试或查看米家管理诊断。"
        except Exception as e:
            logger.error(
                f"[MiHome] 场景 Tool 内部错误: {type(e).__name__}"
            )
            return "场景执行时发生内部错误。"

    @filter.llm_tool(name="list_mihome_devices")
    async def list_mihome_devices_tool(self, event: AstrMessageEvent) -> str:
        """
        列出管理员已加入米家设备控制白名单的设备及静态能力概览。

        使用限制：
        1. 本工具只列出 control_tool.allowed_devices 中的别名，不返回 DID。
        2. 本工具只返回静态画像概览；云端观测请使用 inspect_mihome_device。
        3. direct_control_supported=false 的设备只能检查，不能直接控制。
        """
        return await self.control_tools.list_devices(event)

    @filter.llm_tool(name="inspect_mihome_device")
    async def inspect_mihome_device_tool(
        self,
        event: AstrMessageEvent,
        device_alias: str,
    ) -> str:
        """
        检查白名单内一台米家设备的静态可控能力与云端观测能力。

        云端观测结果仅用于诊断，属性与动作白名单由静态型号画像确定。
        Args:
            device_alias(string): 管理员已加入控制白名单的精确设备别名
        """
        return await self.control_tools.inspect_device(event, device_alias)

    @filter.llm_tool(name="control_mihome_device")
    async def control_mihome_device_tool(
        self,
        event: AstrMessageEvent,
        device_alias: str,
        operations: List[Dict[str, Any]],
    ) -> str:
        """
        顺序设置白名单内米家设备的一个或多个静态可写属性。

        使用限制：
        1. 仅在用户明确要求控制设备时调用。
        2. 单次最多 5 项，操作按顺序执行，已完成项保留结果。
        3. 只能使用 inspect_mihome_device 返回的 writable_properties。
        4. 在工具返回明确成功前，不得向用户声称操作已经完成。
        Args:
            device_alias(string): 管理员已加入控制白名单的精确设备别名
            operations(array[object]): 操作数组，每项格式为 {"prop": "属性", "value": "值"}
        """
        return await self.control_tools.control_device(
            event,
            device_alias,
            operations,
        )

    @filter.llm_tool(name="call_mihome_action")
    async def call_mihome_action_tool(
        self,
        event: AstrMessageEvent,
        device_alias: str,
        action: str,
        params: Optional[List[Any]] = None,
    ) -> str:
        """
        对白名单内米家设备执行静态画像明确允许的动作。

        使用限制：
        1. 仅在用户明确要求执行设备动作时调用。
        2. 未经精确型号验证的动作不允许透传参数。
        3. 在工具返回明确成功前，不得向用户声称动作已经完成。
        Args:
            device_alias(string): 管理员已加入控制白名单的精确设备别名
            action(string): inspect_mihome_device 返回的动作 key 或中文名称
            params(array): 可选动作参数数组；无参动作请留空
        """
        return await self.control_tools.call_action(
            event,
            device_alias,
            action,
            params,
        )

    async def terminate(self):
        await self.web_api.terminate()
        await self.client.terminate()
