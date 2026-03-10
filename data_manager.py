# -*- coding: utf-8 -*-
import os
import json
from typing import Dict, Any
from astrbot.api import logger

# 严格遵守 AstrBot 规范：获取正确的 data 根目录
try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
    BASE_PATH = get_astrbot_data_path()  # 已经是 .../data/
except ImportError:
    BASE_PATH = os.path.join(os.path.abspath(os.getcwd()), "data")

class MiHomeDataManager:
    def __init__(self, plugin_name: str):
        # 正确拼接：直接连接 plugin_data，杜绝 data/data 套娃
        self.data_dir = os.path.join(str(BASE_PATH), "plugin_data", plugin_name)
        os.makedirs(self.data_dir, exist_ok=True)
        self.auth_path = os.path.join(self.data_dir, "auth.json")
        self.state_path = os.path.join(self.data_dir, "state.json")

    def get_auth_path(self) -> str:
        return self.auth_path

    def auth_exists(self) -> bool:
        return os.path.exists(self.auth_path)

    def clear_auth_file(self) -> bool:
        if self.auth_exists():
            try:
                os.remove(self.auth_path)
                return True
            except Exception as e:
                logger.error(f"[MiHome] 文件移除失败: {e}")
                return False
        return False

    def load_state(self) -> Dict[str, Any]:
        if not os.path.exists(self.state_path):
            return {}
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.debug(f"[MiHome] 状态文件读取忽略: {e}")
            return {}

    def update_state(self, **kwargs):
        state = self.load_state()
        state.update(kwargs)
        try:
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[MiHome] 状态保存失败: {e}")
        self.save_state(state)
