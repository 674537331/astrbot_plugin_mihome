# -*- coding: utf-8 -*-
from typing import Any, Dict, List

# ==========================================
# 📦 类别常量
# ==========================================
CATEGORY_NONE = "无类别"
CATEGORY_AC = "空调类别"
CATEGORY_PURIFIER = "净化器类别"
CATEGORY_FAN = "风扇类别"
CATEGORY_COOKER = "蒸煮锅类别"
CATEGORY_AIR_FRYER = "空气炸锅类别"
CATEGORY_TH_SENSOR = "温湿度计类别"
CATEGORY_BODY_SCALE = "体脂秤类别"

VALID_CATEGORIES = {
    CATEGORY_NONE,
    CATEGORY_AC,
    CATEGORY_PURIFIER,
    CATEGORY_FAN,
    CATEGORY_COOKER,
    CATEGORY_AIR_FRYER,
    CATEGORY_TH_SENSOR,
    CATEGORY_BODY_SCALE,
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
    "recipe_id": "食谱编号",
    "recipename": "当前食谱",
    "current_keep_warm": "当前保温状态",
    "reservation_left_time": "距离预约开始",
    "texture": "口感设置",
    "warm_temperature": "保温温度",
    "cook_done": "烹饪是否完成",
    "switch_pausetoadd": "中途加料",
    "cookreservation": "预约设置",
    "air_quality": "空气质量状态",
    "fault": "故障状态",
    "filter_used_time": "滤芯已使用时长",
    "moto_speed_rpm": "电机转速",
    "battery_level": "电池电量",
    "battery": "电池状态",
    "turn_pot": "翻面提醒状态",
    "auto_keep_warm": "自动保温",
    "current_keep_warm": "当前保温状态",
    "power_set": "功率设置",
    "temp_reached": "到温提醒",
    "temp_reached_set": "提醒温度",
    "target_cooking_measure": "食材份量",
    "cooking_weight": "食材重量",
    "match_state": "匹配状态",
    "unit": "单位",
}

# ==========================================
# 📦 类别模板库
# 说明：
# - 作为通用大类模板
# - 仅在 model 未精确命中时使用
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
            "除湿": "dry",
            "送风": "fan",
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
    CATEGORY_TH_SENSOR: {
        "prop_map": {},
        "value_map": {},
        "display_map": {
            "battery_level": "电池电量",
            "temperature": "当前温度",
            "relative_humidity": "当前湿度",
        },
        "detail_writable": [],
        "detail_readable": ["temperature", "relative_humidity", "battery_level"],
        "help_examples": {},
        "help_hints": {},
    },
    CATEGORY_BODY_SCALE: {
        "prop_map": {},
        "value_map": {},
        "display_map": {
            "fault": "故障状态",
            "battery": "电池状态",
            "unit": "单位",
            "mode": "工作模式",
            "status": "工作状态",
        },
        "detail_writable": [],
        "detail_readable": ["fault", "battery", "unit", "mode", "status"],
        "help_examples": {},
        "help_hints": {},
    },
}

# ==========================================
# 🎯 型号精确模板库
# 规则：
# 1. 优先按 model 精确命中
# 2. 一旦命中 model，就以 model 模板为准
# 3. 即使用户配置了 category，也先用 model
# 4. model 未命中，才回退 category
# ==========================================
MODEL_PROFILES: Dict[str, Dict[str, Any]] = {
    "careli.fryer.maf07c": {
        "category": CATEGORY_AIR_FRYER,
        "prop_map": {
            "模式": "mode",
            "时间": "target_time",
            "温度": "target_temperature",
            "自动保温": "auto_keep_warm",
            "当前保温": "current_keep_warm",
            "翻面提醒": "turn_pot_config",
            "口感": "texture",
            "重量": "cooking_weight",
            "预约": "reservation_left_time",
            "食谱名": "recipe_name",
            "食谱": "recipe_name",
        },
        "value_map": {
            "开": True,
            "关": False,
            "薯条": 1,
            "鸡翅": 2,
            "牛排": 3,
            "羊排": 4,
            "五花肉": 5,
            "鱼": 6,
            "红薯": 7,
            "蛋糕": 8,
            "蛋挞": 9,
            "解冻": 10,
            "果干": 11,
            "酸奶": 12,
            "手动模式": 0,
            "手动": 0,
            "脆烤": 1,
            "嫩烤": 2,
            "脱脂": 3,
        },
        "display_map": {
            "status": "工作状态",
            "fault": "故障状态",
            "target_time": "设定时间",
            "target_temperature": "设定温度",
            "left_time": "剩余时间",
            "auto_keep_warm": "自动保温",
            "current_keep_warm": "当前保温状态",
            "mode": "烹饪模式",
            "recipe_id": "食谱编号",
            "recipe_name": "食谱名称",
            "target_cooking_measure": "食材份量",
            "turn_pot": "翻面提醒状态",
            "turn_pot_config": "翻面提醒设置",
            "texture": "口感设置",
            "reservation_left_time": "预约剩余时间",
            "cooking_weight": "食材重量",
        },
        "detail_writable": [
            "mode",
            "target_time",
            "target_temperature",
            "auto_keep_warm",
            "turn_pot_config",
            "texture",
            "reservation_left_time",
            "cooking_weight",
        ],
        "detail_readable": [
            "status",
            "fault",
            "left_time",
            "current_keep_warm",
            "turn_pot",
            "reservation_left_time",
            "recipe_name",
        ],
        "help_examples": {
            "模式": ["薯条", "鸡翅", "牛排", "手动"],
            "温度": ["180", "200"],
            "时间": ["15", "20"],
            "翻面提醒": ["开", "关"],
            "口感": ["脆烤", "嫩烤", "脱脂"],
        },
        "help_hints": {
            "温度": "支持 40~220，步进 5",
            "时间": "支持 1~1440 分钟",
            "重量": "支持 100~1800 克，步进 100",
        },
    },

    "deerma.mfcp.v3": {
        "category": CATEGORY_COOKER,
        "prop_map": {
            "模式": "mode",
            "时间": "target_time",
            "温度": "target_temperature",
            "火力": "heat_level",
            "提示音": "alarm",
            "功率": "power_set",
            "到温提醒": "temp_reached",
            "提醒温度": "temp_reached_set",
            "预约": "cookreservation",
            "保温": "keepwarm_set",
            "保温温度": "warm_temperature",
            "加料提醒": "switch_pausetoadd",
        },
        "value_map": {
            "开": True,
            "关": False,
            "火锅": 1,
            "蒸煮": 2,
            "煲汤": 3,
            "煮粥": 4,
            "煮面": 5,
            "保温": 6,
            "安全模式": 7,
            "食谱": 8,
            "预约模式": 9,
        },
        "display_map": {
            "status": "工作状态",
            "mode": "工作模式",
            "left_time": "剩余时间",
            "fault": "故障状态",
            "heat_level": "加热档位",
            "target_temperature": "设定温度",
            "temperature": "当前温度",
            "target_time": "设定时间",
            "alarm": "提示音状态",
            "power_set": "功率设置",
            "temp_reached": "到温提醒",
            "cook_done": "完成提醒",
            "temp_reached_set": "提醒温度",
            "recipeid": "菜谱编号",
            "cookreservation": "预约烹饪时间",
            "keepwarm_set": "烹饪后保温",
            "warm_temperature": "保温设定温度",
            "recipename": "菜谱名称",
            "switch_pausetoadd": "加料提醒",
        },
        "detail_writable": [
            "mode",
            "target_time",
            "target_temperature",
            "heat_level",
            "alarm",
            "power_set",
            "temp_reached",
            "cook_done",
            "temp_reached_set",
            "cookreservation",
            "keepwarm_set",
            "warm_temperature",
            "switch_pausetoadd",
        ],
        "detail_readable": [
            "status",
            "left_time",
            "fault",
            "temperature",
            "recipename",
        ],
        "help_examples": {
            "模式": ["火锅", "蒸煮", "煲汤", "煮粥", "煮面", "保温"],
            "火力": ["1", "3", "5", "9"],
            "温度": ["60", "80", "100"],
            "时间": ["10", "30", "60"],
            "保温": ["开", "关"],
        },
        "help_hints": {
            "火力": "支持 1~9 档",
            "温度": "支持 30~100°C",
            "时间": "支持 0~300 分钟",
        },
    },

    "lumi.acpartner.mcn02": {
        "category": CATEGORY_AC,
        "prop_map": {
            "开关": "on",
            "模式": "mode",
            "温度": "target_temperature",
            "风速": "fan_level",
            "上下扫风": "vertical_swing",
            "扫风": "vertical_swing",
        },
        "value_map": {
            "开": True,
            "关": False,
            "自动": 0,
            "制冷": 1,
            "除湿": 2,
            "制热": 3,
            "送风": 4,
            "低": 1,
            "中": 2,
            "高": 3,
        },
        "display_map": {
            "on": "开关状态",
            "mode": "模式",
            "target_temperature": "设定温度",
            "fan_level": "风速档位",
            "vertical_swing": "上下摆风",
            "match_state": "匹配状态",
            "ac_work_mode": "空调工作模式",
            "ac_state": "空调状态",
            "electric_power": "电功率",
        },
        "detail_writable": [
            "on",
            "mode",
            "target_temperature",
            "fan_level",
            "vertical_swing",
        ],
        "detail_readable": [
            "ac_work_mode",
            "ac_state",
            "electric_power",
            "match_state",
        ],
        "help_examples": {
            "模式": ["自动", "制冷", "除湿", "制热", "送风"],
            "温度": ["26", "24"],
            "风速": ["低", "中", "高"],
            "扫风": ["开", "关"],
        },
        "help_hints": {
            "温度": "支持 16~30°C",
        },
    },

    "miaomiaoce.sensor_ht.t9": {
        "category": CATEGORY_TH_SENSOR,
        "prop_map": {},
        "value_map": {},
        "display_map": {
            "battery_level": "电池电量",
            "temperature": "当前温度",
            "relative_humidity": "当前湿度",
        },
        "detail_writable": [],
        "detail_readable": [
            "temperature",
            "relative_humidity",
            "battery_level",
        ],
        "help_examples": {},
        "help_hints": {},
    },

    "yunmai.scales.ms103": {
        "category": CATEGORY_BODY_SCALE,
        "prop_map": {},
        "value_map": {},
        "display_map": {
            "fault": "故障状态",
            "battery": "电池状态",
            "unit": "单位",
            "mode": "工作模式",
            "status": "工作状态",
        },
        "detail_writable": [],
        "detail_readable": [
            "fault",
            "battery",
            "unit",
            "mode",
            "status",
        ],
        "help_examples": {},
        "help_hints": {},
    },

    "zhimi.airp.rma3": {
        "category": CATEGORY_PURIFIER,
        "prop_map": {
            "开关": "on",
            "模式": "mode",
            "风速": "fan_level",
            "童锁": "physical_controls_locked",
            "提示音": "alarm",
            "屏幕": "brightness",
            "亮度": "brightness",
        },
        "value_map": {
            "自动": 0,
            "睡眠": 1,
            "最爱": 2,
            "开": True,
            "关": False,
            "开启": True,
            "关闭": False,
            "息屏": 0,
            "微亮": 1,
            "正常": 2,
            "熄灭": 0,
            "暗": 1,
            "亮": 2,
        },
        "display_map": {
            "on": "开关状态",
            "mode": "运行模式",
            "fan_level": "风速",
            "physical_controls_locked": "童锁状态",
            "alarm": "提示音状态",
            "brightness": "屏幕亮度",
            "temperature": "当前温度",
            "relative_humidity": "当前湿度",
            "pm2.5_density": "PM2.5浓度",
            "air_quality": "空气质量状态",
            "fault": "故障状态",
            "filter_left_time": "滤芯剩余天数",
            "filter_life_level": "滤芯寿命百分比",
            "filter_used_time": "滤芯已使用时长",
            "moto_speed_rpm": "电机转速",
        },
        "detail_writable": [
            "on",
            "mode",
            "fan_level",
            "physical_controls_locked",
            "alarm",
            "brightness",
        ],
        "detail_readable": [
            "pm2.5_density",
            "temperature",
            "relative_humidity",
            "air_quality",
            "filter_left_time",
            "filter_life_level",
            "fault",
        ],
        "help_examples": {
            "模式": ["自动", "睡眠", "最爱"],
            "童锁": ["开", "关"],
            "提示音": ["开", "关"],
            "屏幕": ["息屏", "微亮", "正常"],
            "风速": ["0", "1", "5", "10", "14"],
        },
        "help_hints": {
            "风速": "可输入 0~14 的整数档位；通常仅在“最爱”模式下更有意义",
            "屏幕": "支持：息屏 / 微亮 / 正常",
            "模式": "支持：自动 / 睡眠 / 最爱",
        },
    },
}


def normalize_category(category: str) -> str:
    category = str(category or "").strip()
    return category if category in VALID_CATEGORIES else CATEGORY_NONE


def normalize_model(model: str) -> str:
    return str(model or "").strip()


def get_model_profile(model: str) -> Dict[str, Any]:
    model = normalize_model(model)
    if not model:
        return {}
    return MODEL_PROFILES.get(model, {})


def get_category_profile(category: str) -> Dict[str, Any]:
    category = normalize_category(category)
    return CATEGORY_PROFILES.get(category, {})


def resolve_profile(model: str = "", category: str = "") -> Dict[str, Any]:
    """
    解析优先级：
    1. model 精确模板
    2. category 类别模板
    3. 空字典（表示无类别模板）
    """
    model_profile = get_model_profile(model)
    if model_profile:
        return model_profile

    category_profile = get_category_profile(category)
    if category_profile:
        return category_profile

    return {}


def resolve_effective_category(model: str = "", category: str = "") -> str:
    """
    返回当前设备最终生效的类别：
    1. 若 model 模板存在，优先取其声明的 category
    2. 若 model 未命中，则使用传入的 category
    3. 最终无法确定时返回 无类别
    """
    model_profile = get_model_profile(model)
    if model_profile:
        model_category = normalize_category(model_profile.get("category", ""))
        if model_category != CATEGORY_NONE:
            return model_category

    return normalize_category(category)


def get_device_prop_map(model: str = "", category: str = "") -> Dict[str, str]:
    profile = resolve_profile(model=model, category=category)
    return {**GLOBAL_PROP_MAP, **profile.get("prop_map", {})}


def get_device_val_map(model: str = "", category: str = "") -> Dict[str, Any]:
    profile = resolve_profile(model=model, category=category)
    return {**GLOBAL_VAL_MAP, **profile.get("value_map", {})}


def get_device_display_map(model: str = "", category: str = "") -> Dict[str, str]:
    profile = resolve_profile(model=model, category=category)
    return {**GLOBAL_DISPLAY_MAP, **profile.get("display_map", {})}


def get_reverse_prop_map(model: str = "", category: str = "") -> Dict[str, str]:
    forward_map = get_device_prop_map(model=model, category=category)
    return {v: k for k, v in forward_map.items()}


def get_device_detail_writable_keys(model: str = "", category: str = "") -> List[str]:
    profile = resolve_profile(model=model, category=category)
    return profile.get("detail_writable", [])


def get_device_detail_readable_keys(model: str = "", category: str = "") -> List[str]:
    profile = resolve_profile(model=model, category=category)
    return profile.get("detail_readable", [])


def get_device_help_examples(model: str = "", category: str = "") -> Dict[str, List[str]]:
    profile = resolve_profile(model=model, category=category)
    return profile.get("help_examples", {})


def get_device_help_hints(model: str = "", category: str = "") -> Dict[str, str]:
    profile = resolve_profile(model=model, category=category)
    return profile.get("help_hints", {})
