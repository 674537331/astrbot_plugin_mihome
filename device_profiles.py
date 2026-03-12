# -*- coding: utf-8 -*-
from typing import Any, Dict, List

CATEGORY_NONE = "无类别"
CATEGORY_AC = "空调类别"
CATEGORY_PURIFIER = "净化器类别"
CATEGORY_FAN = "风扇类别"
CATEGORY_COOKER = "蒸煮锅类别"
CATEGORY_AIR_FRYER = "空气炸锅类别"

VALID_CATEGORIES = {
    CATEGORY_NONE,
    CATEGORY_AC,
    CATEGORY_PURIFIER,
    CATEGORY_FAN,
    CATEGORY_COOKER,
    CATEGORY_AIR_FRYER,
}

# ==========================================
# 🌐 全局兜底字典 (控制用: 中文 -> 英文)
# ==========================================
GLOBAL_PROP_MAP = {
    "开关": "on",
    "温度": "target_temperature",
    "风速": "fan_level",
    "模式": "mode",
    "亮度": "brightness",
}

GLOBAL_VAL_MAP = {
    "低": "low",
    "中": "medium",
    "高": "high",
    "一档": 1,
    "二档": 2,
    "三档": 3,
}

# ==========================================
# 📺 全局展示字典 (详情用: 英文 -> 中文)
# ==========================================
GLOBAL_DISPLAY_MAP = {
    "temperature": "当前温度",
    "relative_humidity": "当前湿度",
    "pm2.5_density": "PM2.5浓度",
    "filter_left_time": "滤芯剩余天数",
    "filter_life_level": "滤芯寿命百分比",
    "mode": "运行模式",
    "fan_level": "风速档位",
    "on": "电源状态",
    "status": "当前状态",
    "left_time": "剩余时间",
    "physical_controls_locked": "童锁状态",
    "brightness": "屏幕亮度",
    "alarm": "提示音状态",
    "target_time": "设定的目标时间",
    "heat_level": "当前火力",
    "keepwarm_set": "保温设置",
    "electric_power": "功率",
    "target_temperature": "设定温度",
    "ac_state": "空调状态",
    "ac_work_mode": "空调工作模式",
    "vertical_swing": "上下扫风状态",
    "horizontal_swing": "摇头状态",
    "recipe_name": "当前食谱",
    "recipeid": "食谱编号",
    "recipename": "当前食谱",
    "current_keep_warm": "当前保温状态",
    "reservation_left_time": "距离预约开始",
    "texture": "口感设置",
    "warm_temperature": "保温温度",
    "cook_done": "烹饪是否完成",
    "switch_pausetoadd": "中途加料",
    "cookreservation": "预约设置",
}

# ==========================================
# 📦 类别模板库
# ==========================================
CATEGORY_PROFILES = {
    CATEGORY_AC: {
        "prop_map": {
            "温度": "target_temperature",
            "风速": "fan_level",
            "模式": "mode",
            "上下扫风": "vertical_swing",
            "扫风": "vertical_swing",
        },
        "value_map": {
            "制冷": "cool",
            "制热": "heat",
            "自动": "auto",
            "开": True,
            "关": False,
            "开扫风": True,
            "关扫风": False,
        },
        "display_map": {
            "ac_work_mode": "空调工作模式",
            "vertical_swing": "上下扫风状态",
            "target_temperature": "设定温度",
            "ac_state": "空调状态",
        },
        "detail_writable": ["on", "mode", "target_temperature", "fan_level", "vertical_swing"],
        "detail_readable": ["ac_work_mode", "electric_power", "ac_state"],
        "help_examples": {
            "模式": ["制冷", "制热", "自动"],
            "温度": ["26", "24"],
            "扫风": ["开", "关"],
        },
        "help_hints": {
            "温度": "通常输入 16~30 之间的整数",
        },
    },
    CATEGORY_PURIFIER: {
        "prop_map": {
            "模式": "mode",
            "风速": "fan_level",
            "童锁": "physical_controls_locked",
            "提示音": "alarm",
            "屏幕": "brightness",
        },
        "value_map": {
            "自动": 0,
            "睡眠": 1,
            "最爱": 2,
            "亮": 0,
            "暗": 1,
            "熄灭": 2,
        },
        "detail_writable": ["on", "mode", "fan_level", "physical_controls_locked", "alarm", "brightness"],
        "detail_readable": ["temperature", "relative_humidity", "pm2.5_density", "filter_left_time", "filter_life_level"],
        "help_examples": {
            "模式": ["自动", "睡眠", "最爱"],
            "童锁": ["开", "关"],
            "屏幕": ["亮", "暗", "熄灭"],
            "提示音": ["开", "关"],
        },
        "help_hints": {},
    },
    CATEGORY_AIR_FRYER: {
        "prop_map": {
            "模式": "mode",
            "时间": "target_time",
            "温度": "target_temperature",
            "自动保温": "auto_keep_warm",
            "翻面提醒": "turn_pot_config",
            "口感": "texture",
            "重量": "cooking_weight",
        },
        "value_map": {
            "开": True,
            "关": False,
        },
        "display_map": {
            "target_time": "设定时间",
            "target_temperature": "设定温度",
            "recipe_name": "当前食谱",
            "turn_pot": "翻锅提醒状态",
            "current_keep_warm": "当前正处保温",
            "reservation_left_time": "距离预约开始",
            "texture": "口感设置",
        },
        "detail_writable": [
            "on",
            "mode",
            "target_time",
            "target_temperature",
            "auto_keep_warm",
            "turn_pot_config",
            "texture",
            "cooking_weight",
        ],
        "detail_readable": ["status", "left_time", "recipe_name", "current_keep_warm", "reservation_left_time"],
        "help_examples": {
            "温度": ["180", "200"],
            "时间": ["15", "20"],
            "翻面提醒": ["开", "关"],
        },
        "help_hints": {
            "时间": "输入预计分钟数",
        },
    },
    CATEGORY_COOKER: {
        "prop_map": {
            "模式": "mode",
            "时间": "target_time",
            "温度": "target_temperature",
            "火力": "heat_level",
            "保温": "keepwarm_set",
            "提示音": "alarm",
            "预约": "cookreservation",
            "暂停": "switch_pausetoadd",
        },
        "value_map": {
            "开": True,
            "关": False,
        },
        "display_map": {
            "target_time": "设定时间",
            "target_temperature": "设定温度",
            "temperature": "锅内实时温度",
            "cook_done": "烹饪是否完成",
            "recipename": "当前食谱",
            "warm_temperature": "设定保温温度",
            "switch_pausetoadd": "中途加料开关",
        },
        "detail_writable": [
            "on",
            "mode",
            "target_time",
            "target_temperature",
            "heat_level",
            "keepwarm_set",
            "alarm",
            "cookreservation",
            "switch_pausetoadd",
        ],
        "detail_readable": ["status", "left_time", "temperature", "cook_done", "recipename"],
        "help_examples": {
            "火力": ["1", "3", "5"],
            "暂停": ["开", "关"],
            "保温": ["开", "关"],
        },
        "help_hints": {},
    },
    CATEGORY_FAN: {
        "prop_map": {
            "模式": "mode",
            "风速": "fan_level",
            "摇头": "horizontal_swing",
            "童锁": "physical_controls_locked",
        },
        "value_map": {
            "直吹": "straight",
            "自然风": "nature",
            "开": True,
            "关": False,
        },
        "detail_writable": ["on", "mode", "fan_level", "horizontal_swing", "physical_controls_locked"],
        "detail_readable": [],
        "help_examples": {
            "模式": ["直吹", "自然风"],
            "风速": ["1", "2", "3", "4"],
            "摇头": ["开", "关"],
        },
        "help_hints": {},
    },
}


def normalize_category(category: str) -> str:
    category = str(category or "").strip()
    return category if category in VALID_CATEGORIES else CATEGORY_NONE


def get_profile(category: str) -> Dict[str, Any]:
    category = normalize_category(category)
    return CATEGORY_PROFILES.get(category, {})


def get_device_prop_map(category: str) -> Dict[str, str]:
    profile = get_profile(category)
    return {**GLOBAL_PROP_MAP, **profile.get("prop_map", {})}


def get_device_val_map(category: str) -> Dict[str, Any]:
    profile = get_profile(category)
    return {**GLOBAL_VAL_MAP, **profile.get("value_map", {})}


def get_device_display_map(category: str) -> Dict[str, str]:
    profile = get_profile(category)
    return {**GLOBAL_DISPLAY_MAP, **profile.get("display_map", {})}


def get_reverse_prop_map(category: str) -> Dict[str, str]:
    forward_map = get_device_prop_map(category)
    return {v: k for k, v in forward_map.items()}


def get_device_detail_writable_keys(category: str) -> List[str]:
    profile = get_profile(category)
    return profile.get("detail_writable", [])


def get_device_detail_readable_keys(category: str) -> List[str]:
    profile = get_profile(category)
    return profile.get("detail_readable", [])


def get_device_help_examples(category: str) -> Dict[str, List[str]]:
    profile = get_profile(category)
    return profile.get("help_examples", {})


def get_device_help_hints(category: str) -> Dict[str, str]:
    profile = get_profile(category)
    return profile.get("help_hints", {})
