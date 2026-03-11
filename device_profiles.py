# -*- coding: utf-8 -*-
from typing import Any, Dict

# ==========================================
# 🌐 全局兜底字典 (控制用: 中文 -> 英文)
# ==========================================
GLOBAL_PROP_MAP = {
    "开关": "on",
    "温度": "target_temperature",
    "环境温度": "temperature",
    "风速": "fan_level",
    "模式": "mode",
    "亮度": "brightness",
}

GLOBAL_VAL_MAP = {
    "低": "low", "中": "medium", "高": "high",
    "一档": 1, "二档": 2, "三档": 3
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
    "keepwarm_set": "保温设置"
}

# ==========================================
# 📦 设备专属独立档案库 (特征词匹配)
# ==========================================
DEVICE_PROFILES = {
    "空调插座": {
        "prop_map": {
            "温度": "target_temperature",
            "风速": "fan_level",
            "模式": "mode",
            "上下扫风": "vertical_swing",
            "扫风": "vertical_swing"
        },
        "value_map": {
            "制冷": "cool", "制热": "heat", "自动": "auto",
            "开": True, "关": False,
            "开扫风": True, "关扫风": False
        },
        "display_map": {
            "ac_work_mode": "空调工作模式",
            "vertical_swing": "上下扫风状态",
            "target_temperature": "设定温度"
        }
    },
    
    "空气净化器": {
        "prop_map": {
            "模式": "mode", "风速": "fan_level",
            "童锁": "physical_controls_locked",
            "提示音": "alarm", "屏幕": "brightness"
        },
        "value_map": {
            "自动": 0, "睡眠": 1, "最爱": 2,
            "亮": 0, "暗": 1, "熄灭": 2
        }
    },
    
    "空气炸锅": {
        "prop_map": {
            "状态": "status", "模式": "mode",
            "目标时间": "target_time", "目标温度": "target_temperature",
            "自动保温": "auto_keep_warm", "翻面提醒": "turn_pot_config",
            "口感": "texture", "重量": "cooking_weight"
        },
        "value_map": {
            "开": True, "关": False
        },
        "display_map": {
            "target_time": "设定时间",
            "target_temperature": "设定温度",
            "recipe_name": "当前食谱",
            "turn_pot": "翻锅提醒状态",
            "current_keep_warm": "当前正处保温",
            "reservation_left_time": "距离预约开始",
            "texture": "口感设置"
        }
    },
    
    "蒸煮锅": {
        "prop_map": {
            "模式": "mode", "时间": "target_time", "温度": "target_temperature",
            "火力": "heat_level", "保温": "keepwarm_set",
            "提示音": "alarm", "预约": "cookreservation", "暂停": "switch_pausetoadd"
        },
        "value_map": {
            "开": True, "关": False
        },
        "display_map": {
            "target_time": "设定时间",
            "target_temperature": "设定温度",
            "temperature": "锅内实时温度",
            "cook_done": "烹饪是否完成",
            "recipename": "当前食谱",
            "warm_temperature": "设定保温温度",
            "switch_pausetoadd": "中途加料开关"
        }
    },
    
    "落地扇": {
        "prop_map": {
            "模式": "mode", "风速": "fan_level",
            "摇头": "horizontal_swing", "童锁": "physical_controls_locked"
        },
        "value_map": {
            "直吹": "straight", "自然风": "nature",
            "开": True, "关": False
        }
    }
}

def get_device_prop_map(official_name: str) -> Dict[str, str]:
    for key, profile in DEVICE_PROFILES.items():
        if key in official_name:
            return {**GLOBAL_PROP_MAP, **profile.get("prop_map", {})}
    return GLOBAL_PROP_MAP

def get_device_val_map(official_name: str) -> Dict[str, Any]:
    for key, profile in DEVICE_PROFILES.items():
        if key in official_name:
            return {**GLOBAL_VAL_MAP, **profile.get("value_map", {})}
    return GLOBAL_VAL_MAP

def get_device_display_map(official_name: str) -> Dict[str, str]:
    for key, profile in DEVICE_PROFILES.items():
        if key in official_name:
            return {**GLOBAL_DISPLAY_MAP, **profile.get("display_map", {})}
    return GLOBAL_DISPLAY_MAP

def get_reverse_prop_map(official_name: str) -> Dict[str, str]:
    forward_map = get_device_prop_map(official_name)
    return {v: k for k, v in forward_map.items()}
