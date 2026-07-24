# -*- coding: utf-8 -*-
import json
import os
import shutil
import stat
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, Any

from astrbot.api import logger

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
    BASE_PATH = Path(get_astrbot_data_path())
except ImportError:
    BASE_PATH = Path.cwd() / "data"


class MiHomeDataManager:
    def __init__(self, plugin_name: str):
        self.data_dir = BASE_PATH / "plugin_data" / plugin_name
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._harden_path(self.data_dir, 0o700, "插件数据目录")
        self.auth_path = self.data_dir / "auth.json"
        self.state_path = self.data_dir / "state.json"
        self._state_lock = threading.RLock()
        self._state_corrupt = False
        self.auth_storage_is_secure()

    @staticmethod
    def _is_link_or_reparse_point(path: Path) -> bool:
        if path.is_symlink():
            return True
        if os.name != "nt":
            return False
        try:
            attributes = getattr(path.lstat(), "st_file_attributes", 0)
        except OSError:
            return False
        if not isinstance(attributes, int):
            return False
        reparse_flag = getattr(
            stat,
            "FILE_ATTRIBUTE_REPARSE_POINT",
            0x0400,
        )
        return bool(attributes & reparse_flag)

    @staticmethod
    def _harden_path(path: Path, mode: int, label: str) -> bool:
        if MiHomeDataManager._is_link_or_reparse_point(path):
            logger.error(f"[MiHome] {label}不能是链接或重解析点")
            return False
        try:
            path.chmod(mode)
            if os.name != "nt":
                actual_mode = stat.S_IMODE(path.stat().st_mode)
                if actual_mode & 0o077:
                    logger.error(f"[MiHome] {label}权限加固未生效")
                    return False
            return True
        except Exception as e:
            logger.error(f"[MiHome] {label}权限加固失败: {e}")
            return False

    def get_auth_path(self) -> str:
        return str(self.auth_path)

    def auth_exists(self) -> bool:
        return (
            self.auth_path.exists()
            or self._is_link_or_reparse_point(self.auth_path)
        )

    def harden_auth_file(self) -> bool:
        if (
            self._is_link_or_reparse_point(self.auth_path)
            or not self.auth_path.exists()
            or not self.auth_path.is_file()
        ):
            logger.error("[MiHome] 登录凭证文件缺失或类型异常")
            return False
        return self._harden_path(self.auth_path, 0o600, "登录凭证文件")

    def auth_storage_is_secure(self) -> bool:
        if not self._harden_path(self.data_dir, 0o700, "插件数据目录"):
            return False
        if self._is_link_or_reparse_point(self.auth_path):
            logger.error("[MiHome] 登录凭证路径不能是链接或重解析点")
            return False
        if self.auth_path.exists():
            return self.harden_auth_file()
        return True

    def clear_auth_file(self) -> bool:
        if not self.auth_exists():
            return True
        try:
            self.auth_path.unlink()
            return True
        except Exception as e:
            logger.error(f"[MiHome] 文件移除失败: {e}")
            return False

    def clear_state_backups(self) -> bool:
        """显式登出时清理可能包含旧账号设备信息的备份与临时文件。"""

        with self._state_lock:
            ok = True
            patterns = (
                f"{self.state_path.name}.corrupt-*",
                ".state-*.tmp",
            )
            for pattern in patterns:
                for backup_path in self.data_dir.glob(pattern):
                    if backup_path.parent != self.data_dir:
                        ok = False
                        continue
                    try:
                        backup_path.unlink()
                    except Exception as exc:
                        logger.error(
                            "[MiHome] 旧账号状态文件清理失败: "
                            f"{type(exc).__name__}"
                        )
                        ok = False
            return ok

    def load_state(self) -> Dict[str, Any]:
        with self._state_lock:
            if not self.state_path.exists():
                self._state_corrupt = False
                return {}
            try:
                with self.state_path.open("r", encoding="utf-8") as f:
                    state = json.load(f)
                if not isinstance(state, dict):
                    raise ValueError("状态文件根节点必须是对象")
                self._state_corrupt = False
                return state
            except Exception as e:
                logger.debug(f"[MiHome] 状态文件读取忽略: {e}")
                self._state_corrupt = True
                return {}

    def save_state(self, state: Dict[str, Any]) -> bool:
        with self._state_lock:
            temp_path = None
            try:
                if self._state_corrupt and self.state_path.exists():
                    backup_path = self.state_path.with_name(
                        f"{self.state_path.name}.corrupt-{time.time_ns()}"
                    )
                    shutil.copy2(self.state_path, backup_path)
                    if not self._harden_path(
                        backup_path,
                        0o600,
                        "损坏状态备份",
                    ):
                        raise PermissionError("损坏状态备份权限加固失败")

                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=self.data_dir,
                    prefix=".state-",
                    suffix=".tmp",
                    delete=False,
                ) as f:
                    temp_path = Path(f.name)
                    json.dump(state, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                if not self._harden_path(
                    temp_path,
                    0o600,
                    "临时状态文件",
                ):
                    raise PermissionError("临时状态文件权限加固失败")
                os.replace(temp_path, self.state_path)
                temp_path = None
                if not self._harden_path(self.state_path, 0o600, "状态文件"):
                    return False
                self._state_corrupt = False
                return True
            except Exception as e:
                logger.error(f"[MiHome] 状态保存失败: {e}")
                return False
            finally:
                if temp_path is not None:
                    try:
                        temp_path.unlink(missing_ok=True)
                    except Exception:
                        pass

    def update_state(self, **kwargs) -> bool:
        with self._state_lock:
            state = self.load_state()
            state.update(kwargs)
            return self.save_state(state)
