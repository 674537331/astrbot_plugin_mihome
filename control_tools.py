# -*- coding: utf-8 -*-
"""安全的米家 LLM 设备发现与控制服务。

本模块吸收了 @Siq5005 在 PR #13 中提出的能力聚合与四 Tool 设计，
但执行时只信任管理员明确配置的别名、类别/型号静态画像和设备白名单。
动态发现结果仅用于检查，可控制范围由静态型号画像和管理员白名单共同确定。
"""

import asyncio
import json
import math
import time
from typing import Any, Dict, List, Optional

from astrbot.api import logger

from .device_profiles import (
    CATEGORY_NONE,
    get_device_action_map,
    get_device_detail_actions,
    get_device_detail_readable_keys,
    get_device_detail_writable_keys,
    get_device_display_map,
    get_device_help_examples,
    get_device_help_hints,
    get_device_property_value_map,
    get_device_prop_map,
    get_device_val_map,
    get_reverse_action_map as get_device_reverse_action_map,
    get_reverse_prop_map as get_device_reverse_prop_map,
    has_model_profile,
    normalize_category,
    resolve_effective_category,
)
from .mihome_client import (
    MiHomeAuthError,
    MiHomeClientError,
    MiHomeControlError,
)


MAX_CONTROL_OPERATIONS = 5
MAX_ACTION_PARAMETERS = 4
MAX_TEXT_PARAMETER_LENGTH = 500
MAX_PROPERTY_VALUE_LENGTH = 256
CONTROL_COOLDOWN_SECONDS = 3.0
MAX_COOLDOWN_TRACKED_DEVICES = 256
DENIED_CROSS_DEVICE_ACTIONS = frozenset(
    {
        "execute-text-directive",
        "tv-switchon",
    }
)


def is_denied_device_action(action: Any) -> bool:
    """统一拦截可把指令转发到其他设备的高风险动作。"""

    return str(action or "").strip().lower() in DENIED_CROSS_DEVICE_ACTIONS


# 只有 @Siq5005 在 PR #13 中提供过实机数据的精确型号才开放带参动作。
# 不能按动作名或“音箱类别”全局复用 PIID。
PARAMETERIZED_ACTIONS_BY_MODEL: Dict[str, Dict[str, List[Dict[str, Any]]]] = {
    "xiaomi.wifispeaker.oh2p": {
        "play-text": [
            {
                "piid": 1,
                "type": "string",
                "name": "text-content",
                "description": "要播放的文本",
            }
        ],
    }
}

_SENSITIVE_CAPABILITY_PARTS = {
    "account",
    "auth",
    "cookie",
    "device_id",
    "ip_address",
    "mac",
    "pass",
    "secret",
    "ssid",
    "token",
    "user",
}


class MiHomeControlTools:
    """为主插件提供可测试的 Tool 业务逻辑。"""

    def __init__(self, plugin: Any):
        self.plugin = plugin
        self._last_execution_at: Dict[str, float] = {}
        self._execution_lock = asyncio.Lock()

    @staticmethod
    def _parse_array(value: Any, field_name: str) -> tuple[Optional[List[Any]], str]:
        if value is None:
            return [], ""
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return None, f"{field_name} 必须是数组，旧版 JSON 字符串也必须是合法数组。"
        if not isinstance(value, list):
            return None, f"{field_name} 必须是数组。"
        return value, ""

    def settings(self) -> Dict[str, Any]:
        raw = self.plugin.config.get("control_tool", {})
        if not isinstance(raw, dict):
            raw = {}
        allowed, _error = self._parse_array(raw.get("allowed_devices", []), "allowed_devices")
        allowed_aliases = []
        for value in allowed or []:
            alias = str(value or "").strip()
            if alias and alias not in allowed_aliases:
                allowed_aliases.append(alias)
        return {
            "enable": bool(raw.get("enable", False)),
            "admin_only": bool(raw.get("admin_only", True)),
            "allowed_devices": allowed_aliases,
        }

    def check_access(self, event: Any) -> Optional[str]:
        settings = self.settings()
        if not settings["enable"]:
            return "米家设备控制 Tool 当前未启用。"
        if settings["admin_only"] and not self.plugin._event_is_admin(event):
            return "米家设备控制 Tool 当前仅允许 AstrBot 管理员调用。"
        if not settings["allowed_devices"]:
            return "米家设备控制 Tool 尚未配置设备白名单，当前控制范围为空。"
        return None

    def _resolve_allowed_device(
        self,
        alias: str,
    ) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
        alias = str(alias or "").strip()
        if not alias:
            return None, "device_alias 不能为空。"

        settings = self.settings()
        if alias not in settings["allowed_devices"]:
            return None, f"设备“{alias}”不在控制 Tool 白名单中。"

        device_map = self.plugin._parse_device_map()
        if alias not in device_map:
            return None, f"设备白名单中的别名“{alias}”没有有效 DID 映射，请在管理页面修复。"

        did = device_map[alias]
        configured_category = normalize_category(
            self.plugin._parse_category_map().get(alias, CATEGORY_NONE)
        )
        model = self.plugin._get_model_by_did(did)
        effective_category = resolve_effective_category(
            model=model,
            category=configured_category,
        )
        is_ir = self._is_ir_device(did, model)
        # 手工类别映射可继续服务人工命令和状态展示，但不能替代精确型号画像
        # 成为 LLM 物理控制的授权依据。
        has_static_profile = bool(has_model_profile(model))
        return {
            "alias": alias,
            "did": did,
            "model": model,
            "configured_category": configured_category,
            "category": effective_category,
            "is_ir": is_ir,
            "has_static_profile": has_static_profile,
        }, None

    @staticmethod
    def _is_ir_device(did: str, model: str) -> bool:
        return str(did or "").startswith("ir.") or str(model or "").lower().startswith(
            "miir."
        )

    @staticmethod
    def _parameter_schema(model: str, action: str) -> List[Dict[str, Any]]:
        return PARAMETERIZED_ACTIONS_BY_MODEL.get(str(model or ""), {}).get(
            action,
            [],
        )

    def _aggregate_capabilities(self, device: Dict[str, Any]) -> Dict[str, Any]:
        model = device["model"]
        category = device["category"]
        writable_keys = get_device_detail_writable_keys(
            model=model,
            category=category,
        )
        readable_keys = get_device_detail_readable_keys(
            model=model,
            category=category,
        )
        action_keys = [
            action
            for action in get_device_detail_actions(
                model=model,
                category=category,
            )
            if not is_denied_device_action(action)
        ]
        reverse_props = get_device_reverse_prop_map(model=model, category=category)
        reverse_actions = get_device_reverse_action_map(
            model=model,
            category=category,
        )
        display_map = get_device_display_map(model=model, category=category)
        examples = get_device_help_examples(model=model, category=category)
        hints = get_device_help_hints(model=model, category=category)

        writable = []
        for key in writable_keys:
            name = reverse_props.get(key, display_map.get(key, key))
            writable.append(
                {
                    "key": key,
                    "name": name,
                    # PR #13 把示例误当成穷举枚举；v8 明确标为 examples。
                    "examples": [str(item) for item in examples.get(name, [])],
                    "hint": str(hints.get(name, "")),
                }
            )

        actions = []
        for key in action_keys:
            item: Dict[str, Any] = {
                "key": key,
                "name": reverse_actions.get(key, key),
            }
            schema = self._parameter_schema(model, key)
            if schema:
                item["parameters"] = schema
            actions.append(item)

        direct_control_supported = bool(
            device["has_static_profile"]
            and not device["is_ir"]
            and (writable or actions)
        )
        result: Dict[str, Any] = {
            "alias": device["alias"],
            "model": model or "未知",
            "category": category,
            "direct_control_supported": direct_control_supported,
            "writable_properties": writable,
            "readable_properties": [
                {"key": key, "name": display_map.get(key, key)}
                for key in readable_keys
            ],
            "actions": actions,
        }
        if device["is_ir"]:
            result["unsupported_reason"] = "红外设备不开放直接控制，请改用米家场景。"
        elif not device["has_static_profile"]:
            result["unsupported_reason"] = (
                "未配置受信任的型号或类别画像；动态发现结果仅供检查。"
            )
        elif not writable and not actions:
            result["unsupported_reason"] = "当前静态画像没有可写属性或动作。"
        return result

    @staticmethod
    def _safe_observed_capabilities(payload: Dict[str, Any]) -> Dict[str, Any]:
        if payload.get("__error__"):
            return {"error": str(payload["__error__"])}

        def safe_keys(field: str) -> List[str]:
            result = []
            for raw in payload.get(field, []):
                key = str(raw or "").strip()
                lowered = key.lower()
                if not key or any(part in lowered for part in _SENSITIVE_CAPABILITY_PARTS):
                    continue
                if key not in result:
                    result.append(key)
                if len(result) >= 100:
                    break
            return result

        return {
            "readable": safe_keys("readable"),
            "writable": safe_keys("writable"),
            "actions": safe_keys("actions"),
            "notice": "云端发现结果只用于诊断，可控制白名单须由管理员显式配置。",
        }

    async def list_devices(self, event: Any) -> str:
        denied = self.check_access(event)
        if denied:
            return denied

        rows = []
        for alias in self.settings()["allowed_devices"]:
            device, error = self._resolve_allowed_device(alias)
            if error or not device:
                rows.append({"alias": alias, "available": False, "reason": error})
                continue
            capabilities = self._aggregate_capabilities(device)
            rows.append(
                {
                    "alias": alias,
                    "model": capabilities["model"],
                    "category": capabilities["category"],
                    "direct_control_supported": capabilities[
                        "direct_control_supported"
                    ],
                    "writable_property_count": len(
                        capabilities["writable_properties"]
                    ),
                    "action_count": len(capabilities["actions"]),
                    **(
                        {"reason": capabilities["unsupported_reason"]}
                        if capabilities.get("unsupported_reason")
                        else {}
                    ),
                }
            )
        return json.dumps(
            {
                "devices": rows,
                "notice": "仅列出管理员配置的控制白名单；结果不包含 DID。",
            },
            ensure_ascii=False,
        )

    async def inspect_device(self, event: Any, alias: str) -> str:
        denied = self.check_access(event)
        if denied:
            return denied
        device, error = self._resolve_allowed_device(alias)
        if error or not device:
            return error or "无法解析设备。"

        result = self._aggregate_capabilities(device)
        try:
            observed = await self.plugin.client.get_device_capabilities(device["did"])
            result["observed_capabilities"] = self._safe_observed_capabilities(
                observed
            )
        except Exception as exc:
            logger.warning(
                f"[MiHome] Tool 能力检查失败: {type(exc).__name__}"
            )
            result["observed_capabilities"] = {
                "error": "动态能力检查失败，请稍后重试。"
            }
        result["notice"] = (
            "执行控制时只使用上述静态 writable_properties/actions，"
            "可控制范围仅使用静态型号画像和管理员白名单，忽略 observed_capabilities。"
        )
        return json.dumps(result, ensure_ascii=False)

    def _ensure_direct_control(
        self,
        device: Dict[str, Any],
    ) -> Optional[str]:
        capabilities = self._aggregate_capabilities(device)
        if capabilities["direct_control_supported"]:
            return None
        return str(
            capabilities.get("unsupported_reason")
            or "该设备当前不支持直接控制。"
        )

    def _reserve_execution(
        self,
        device_key: str,
        display_alias: str,
    ) -> Optional[str]:
        """按 DID 限制连续物理操作，防止同设备多别名绕过冷却。"""

        now = time.monotonic()
        expired_before = now - CONTROL_COOLDOWN_SECONDS
        expired_keys = [
            key
            for key, executed_at in self._last_execution_at.items()
            if executed_at <= expired_before
        ]
        for key in expired_keys:
            self._last_execution_at.pop(key, None)

        last = self._last_execution_at.get(device_key)
        remaining = (
            CONTROL_COOLDOWN_SECONDS - (now - last)
            if last is not None
            else 0.0
        )
        if last is not None and remaining > 0:
            return (
                f"设备“{display_alias}”操作过于频繁，请至少等待 "
                f"{CONTROL_COOLDOWN_SECONDS:g} 秒后再试。"
            )
        if (
            device_key not in self._last_execution_at
            and len(self._last_execution_at) >= MAX_COOLDOWN_TRACKED_DEVICES
        ):
            return "短时间内设备控制请求过多，请稍后再试。"
        # 本方法在首个 await 之前同步执行，事件循环内的检查与占位不可分割。
        self._last_execution_at[device_key] = now
        return None

    def _translate_property(
        self,
        device: Dict[str, Any],
        prop: Any,
        value: Any,
    ) -> Dict[str, Any]:
        model = device["model"]
        category = device["category"]
        prop_map = get_device_prop_map(model=model, category=category)
        writable = get_device_detail_writable_keys(model=model, category=category)
        reverse = get_device_reverse_prop_map(model=model, category=category)

        prop_text = str(prop or "").strip()
        if prop_text in prop_map:
            cloud_prop = prop_map[prop_text]
        elif prop_text in writable:
            cloud_prop = prop_text
        else:
            normalized = prop_text.replace("-", "_").lower()
            cloud_prop = next(
                (
                    item
                    for item in writable
                    if item.replace("-", "_").lower() == normalized
                ),
                "",
            )
        if not cloud_prop or cloud_prop not in writable:
            return {
                "ok": False,
                "error": f"未知或不可写属性：{prop_text or '(空)'}",
                "available_properties": [
                    reverse.get(item, item) for item in writable
                ],
            }

        if value is None or isinstance(value, (dict, list, tuple, set)):
            return {
                "ok": False,
                "error": "属性值必须是字符串、数字或布尔值",
                "available_properties": [
                    reverse.get(item, item) for item in writable
                ],
            }
        if isinstance(value, float) and not math.isfinite(value):
            return {
                "ok": False,
                "error": "属性值不能是 NaN 或无穷大",
                "available_properties": [
                    reverse.get(item, item) for item in writable
                ],
            }
        if isinstance(value, str) and len(value) > MAX_PROPERTY_VALUE_LENGTH:
            return {
                "ok": False,
                "error": (
                    f"属性字符串最长允许 {MAX_PROPERTY_VALUE_LENGTH} 个字符"
                ),
                "available_properties": [
                    reverse.get(item, item) for item in writable
                ],
            }

        property_val_map = get_device_property_value_map(
            model=model,
            category=category,
            property_key=cloud_prop,
        )
        if property_val_map:
            if isinstance(value, str) and value.strip() in property_val_map:
                cloud_value = property_val_map[value.strip()]
            else:
                cloud_value = self.plugin._parse_value(value)
                if cloud_value not in property_val_map.values():
                    return {
                        "ok": False,
                        "error": (
                            f"属性“{reverse.get(cloud_prop, cloud_prop)}”"
                            "的值不在型号画像允许范围内"
                        ),
                        "available_properties": [
                            reverse.get(item, item) for item in writable
                        ],
                    }
        else:
            val_map = get_device_val_map(model=model, category=category)
            if isinstance(value, str) and value.strip() in val_map:
                cloud_value = val_map[value.strip()]
            else:
                cloud_value = self.plugin._parse_value(value)
        return {
            "ok": True,
            "property": cloud_prop,
            "display_name": reverse.get(cloud_prop, cloud_prop),
            "value": cloud_value,
        }

    @staticmethod
    def _format_client_error(error: Exception) -> str:
        if isinstance(error, MiHomeAuthError):
            return "米家登录已失效，请管理员重新登录。"
        if isinstance(error, MiHomeControlError):
            reason = str(error)
            if reason == "device_not_found":
                return "米家云端找不到该设备。"
            if reason == "cloud_no_response":
                return "米家云端没有返回有效数据，请稍后重试。"
            if reason.startswith("cloud_rejected:"):
                return "米家云端或设备拒绝了这项操作。"
            if reason.startswith("action_not_found:"):
                return "设备当前能力清单中没有该动作。"
            if reason == "invalid_value_or_capability":
                return "属性值或设备能力与当前请求不匹配。"
            if reason.startswith("action_schema_missing:"):
                return "设备动作规格缺少调用参数，请重新同步或提交适配。"
            if reason == "internal_error":
                return "插件处理设备操作时发生内部错误。"
            return "设备操作失败，请检查能力与参数。"
        if isinstance(error, MiHomeClientError):
            return "米家 API 或网络异常，请稍后重试。"
        return "插件内部执行异常。"

    async def control_device(
        self,
        event: Any,
        alias: str,
        operations: Any,
    ) -> str:
        denied = self.check_access(event)
        if denied:
            return denied
        device, error = self._resolve_allowed_device(alias)
        if error or not device:
            return error or "无法解析设备。"
        unsupported = self._ensure_direct_control(device)
        if unsupported:
            return unsupported

        parsed, parse_error = self._parse_array(operations, "operations")
        if parse_error:
            return parse_error
        if not parsed:
            return "operations 不能为空。"
        if len(parsed) > MAX_CONTROL_OPERATIONS:
            return f"单次最多允许 {MAX_CONTROL_OPERATIONS} 项操作。"

        translated: List[Dict[str, Any]] = []
        seen_properties = set()
        for index, operation in enumerate(parsed, 1):
            if not isinstance(operation, dict):
                return f"第 {index} 项操作必须是对象。"
            if set(operation) - {"prop", "value"}:
                return f"第 {index} 项操作只允许 prop 和 value 字段。"
            if "prop" not in operation or "value" not in operation:
                return f"第 {index} 项操作必须同时包含 prop 和 value。"
            item = self._translate_property(
                device,
                operation["prop"],
                operation["value"],
            )
            if not item["ok"]:
                available = "、".join(item.get("available_properties", []))
                return (
                    f"第 {index} 项失败：{item['error']}"
                    + (f"；可用属性：{available}" if available else "")
                )
            if item["property"] in seen_properties:
                return f"同一批次不能重复设置属性：{item['display_name']}。"
            seen_properties.add(item["property"])
            translated.append(item)

        if self._execution_lock.locked():
            return "已有米家设备控制任务正在执行，请等待完成后再试。"
        async with self._execution_lock:
            rate_limit_error = self._reserve_execution(
                device["did"],
                device["alias"],
            )
            if rate_limit_error:
                return rate_limit_error

            results = []
            for item in translated:
                try:
                    confirmed = await self.plugin.client.set_property(
                        device["did"],
                        item["property"],
                        item["value"],
                        device["alias"],
                    )
                    results.append(
                        {
                            "property": item["display_name"],
                            "status": (
                                "success"
                                if confirmed is True
                                else "unconfirmed"
                            ),
                        }
                    )
                except Exception as exc:
                    results.append(
                        {
                            "property": item["display_name"],
                            "status": "failed",
                            "reason": self._format_client_error(exc),
                        }
                    )
            self._last_execution_at[device["did"]] = time.monotonic()

        success_count = sum(
            1 for item in results if item["status"] == "success"
        )
        unconfirmed_count = sum(
            1 for item in results if item["status"] == "unconfirmed"
        )
        return json.dumps(
            {
                "device": device["alias"],
                "summary": {
                    "success": success_count,
                    "unconfirmed": unconfirmed_count,
                    "failed": len(results)
                    - success_count
                    - unconfirmed_count,
                    "atomic": False,
                },
                "results": results,
                "notice": (
                    "操作按顺序执行，已完成项保留结果；unconfirmed 只表示"
                    "网关已接收，不能据此声称设备已经完成操作。"
                ),
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _coerce_parameter(value: Any, schema: Dict[str, Any]) -> Any:
        expected = schema["type"]
        if expected == "string":
            if not isinstance(value, str):
                raise ValueError(f"{schema['name']} 必须是字符串")
            if len(value) > MAX_TEXT_PARAMETER_LENGTH:
                raise ValueError(
                    f"{schema['name']} 最长允许 {MAX_TEXT_PARAMETER_LENGTH} 个字符"
                )
            return value
        if expected == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in {"true", "1", "yes", "开", "是"}:
                    return True
                if lowered in {"false", "0", "no", "关", "否"}:
                    return False
            raise ValueError(f"{schema['name']} 必须是布尔值")
        if expected == "int":
            if isinstance(value, bool):
                raise ValueError(f"{schema['name']} 必须是整数")
            try:
                return int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{schema['name']} 必须是整数") from exc
        raise ValueError("插件不支持该参数类型")

    async def call_action(
        self,
        event: Any,
        alias: str,
        action: str,
        params: Any = None,
    ) -> str:
        denied = self.check_access(event)
        if denied:
            return denied
        device, error = self._resolve_allowed_device(alias)
        if error or not device:
            return error or "无法解析设备。"
        unsupported = self._ensure_direct_control(device)
        if unsupported:
            return unsupported

        action_map = get_device_action_map(
            model=device["model"],
            category=device["category"],
        )
        allowed_actions = [
            item
            for item in get_device_detail_actions(
                model=device["model"],
                category=device["category"],
            )
            if not is_denied_device_action(item)
        ]
        reverse_actions = get_device_reverse_action_map(
            model=device["model"],
            category=device["category"],
        )
        action_text = str(action or "").strip()
        candidate_action = action_map.get(action_text, action_text)
        if (
            candidate_action not in allowed_actions
            or is_denied_device_action(candidate_action)
        ):
            available = "、".join(
                reverse_actions.get(item, item) for item in allowed_actions
            )
            return (
                f"设备“{device['alias']}”不支持动作“{action_text or '(空)'}”。"
                + (f"可用动作：{available}" if available else "当前没有可用动作。")
            )
        cloud_action = candidate_action

        parsed_params, parse_error = self._parse_array(params, "params")
        if parse_error:
            return parse_error
        if len(parsed_params or []) > MAX_ACTION_PARAMETERS:
            return f"单次最多允许 {MAX_ACTION_PARAMETERS} 个动作参数。"

        schema = self._parameter_schema(device["model"], cloud_action)
        if schema:
            if len(parsed_params or []) != len(schema):
                return (
                    f"动作“{reverse_actions.get(cloud_action, cloud_action)}”需要"
                    f" {len(schema)} 个参数："
                    + "、".join(
                        f"{item['name']}({item['type']})" for item in schema
                    )
                )
            in_params = []
            try:
                for value, item in zip(parsed_params or [], schema):
                    in_params.append(
                        {
                            "piid": item["piid"],
                            "value": self._coerce_parameter(value, item),
                        }
                    )
            except ValueError as exc:
                return f"动作参数无效：{exc}。"
        else:
            if parsed_params:
                return (
                    "该动作未经过带参调用验证，仅接受无参调用。"
                )
            in_params = []

        if self._execution_lock.locked():
            return "已有米家设备控制任务正在执行，请等待完成后再试。"
        async with self._execution_lock:
            rate_limit_error = self._reserve_execution(
                device["did"],
                device["alias"],
            )
            if rate_limit_error:
                return rate_limit_error

            try:
                if schema:
                    confirmed = await self.plugin.client.run_action_with_in(
                        device["did"],
                        cloud_action,
                        in_params,
                        device["alias"],
                    )
                else:
                    confirmed = await self.plugin.client.run_action(
                        device["did"],
                        cloud_action,
                        device["alias"],
                    )
                action_name = reverse_actions.get(cloud_action, cloud_action)
                if confirmed is False:
                    return (
                        f"米家网关已接收设备“{device['alias']}”的动作"
                        f"“{action_name}”，但云端无法确认设备是否执行。"
                    )
                return f"已成功对设备“{device['alias']}”执行动作：{action_name}。"
            except Exception as exc:
                return (
                    f"设备“{device['alias']}”动作执行失败："
                    f"{self._format_client_error(exc)}"
                )
            finally:
                self._last_execution_at[device["did"]] = time.monotonic()
