# -*- coding: utf-8 -*-
import os
import json
from typing import Any, Dict

from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

DEFAULT_STATE = {
    "last_login_at": "",
    "last_login_error": "",
    "last_control_error": "",
    "last_control_device": ""
}

class MiHomeDataManager:
    def __init__(self, plugin_name: str = "astrbot_plugin_mihome"):
        base_data_path = str(get_astrbot_data_path())
        self.plugin_data_path = os.path.join(base_data_path, "plugin_data", plugin_name)
        os.makedirs(self.plugin_data_path, exist_ok=True)

        self.auth_store_path = os.path.join(self.plugin_data_path, "auth.json")
        self.state_store_path = os.path.join(self.plugin_data_path, "mihome_state.json")

    def get_auth_path(self) -> str:
        return self.auth_store_path

    def auth_exists(self) -> bool:
        return os.path.exists(self.auth_store_path)

    def clear_auth_file(self) -> bool:
        try:
            if os.path.exists(self.auth_store_path):
                os.remove(self.auth_store_path)
            return True
        except Exception as e:
            logger.error(f"[MiHome] 删除 auth.json 失败: {e}")
            return False

    def load_state(self) -> Dict[str, Any]:
        if not os.path.exists(self.state_store_path):
            return DEFAULT_STATE.copy()
        try:
            with open(self.state_store_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    return DEFAULT_STATE.copy()
                
                merged_state = DEFAULT_STATE.copy()
                merged_state.update(data)
                return merged_state
        except Exception as e:
            logger.error(f"[MiHome] 读取状态文件失败: {e}")
            return DEFAULT_STATE.copy()

    def save_state(self, state: Dict[str, Any]) -> None:
        try:
            with open(self.state_store_path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[MiHome] 保存状态文件失败: {e}")

    def update_state(self, **kwargs) -> None:
        state = self.load_state()
        state.update(kwargs)
        self.save_state(state)
