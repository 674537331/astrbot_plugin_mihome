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

_CORRUPT_STATE_BACKUP_LIMIT = 5
_FILE_COMPARE_CHUNK_SIZE = 64 * 1024

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

    def _state_backup_paths(self) -> list[Path]:
        prefix = f"{self.state_path.name}.corrupt-"
        return sorted(
            (
                path
                for path in self.data_dir.glob(f"{prefix}*")
                if path.parent == self.data_dir
            ),
            key=lambda path: self._state_backup_order(path, prefix),
        )

    @staticmethod
    def _state_backup_order(path: Path, prefix: str) -> tuple[int, str]:
        try:
            timestamp = int(path.name.removeprefix(prefix))
        except ValueError:
            try:
                timestamp = path.stat().st_mtime_ns
            except OSError:
                timestamp = 0
        return timestamp, path.name

    @staticmethod
    def _files_have_same_content(first: Path, second: Path) -> bool:
        if (
            MiHomeDataManager._is_link_or_reparse_point(first)
            or MiHomeDataManager._is_link_or_reparse_point(second)
        ):
            return False
        try:
            if (
                not first.is_file()
                or not second.is_file()
                or first.stat().st_size != second.stat().st_size
            ):
                return False
            with first.open("rb") as first_file, second.open("rb") as second_file:
                while True:
                    first_chunk = first_file.read(_FILE_COMPARE_CHUNK_SIZE)
                    second_chunk = second_file.read(_FILE_COMPARE_CHUNK_SIZE)
                    if first_chunk != second_chunk:
                        return False
                    if not first_chunk:
                        return True
        except OSError:
            return False

    @staticmethod
    def _remove_state_backup(path: Path) -> bool:
        try:
            path.unlink()
            return True
        except OSError as exc:
            logger.error(
                "[MiHome] 损坏状态备份清理失败: "
                f"{type(exc).__name__}"
            )
            return False

    def _prune_state_backups(self, preserve: Path | None = None) -> None:
        backups = self._state_backup_paths()
        excess = len(backups) - _CORRUPT_STATE_BACKUP_LIMIT
        if excess <= 0:
            return
        for backup_path in backups:
            if excess <= 0:
                break
            if preserve is not None and backup_path == preserve:
                continue
            if self._remove_state_backup(backup_path):
                excess -= 1

    def _backup_corrupt_state(self) -> None:
        self._prune_state_backups()
        backups = self._state_backup_paths()
        matching_backups = [
            path
            for path in backups
            if self._files_have_same_content(self.state_path, path)
        ]
        if matching_backups:
            preserved_backup = matching_backups[-1]
            for duplicate_path in matching_backups[:-1]:
                self._remove_state_backup(duplicate_path)
            if not self._harden_path(
                preserved_backup,
                0o600,
                "损坏状态备份",
            ):
                raise PermissionError("损坏状态备份权限加固失败")
            self._prune_state_backups(preserve=preserved_backup)
            return

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
        self._prune_state_backups(preserve=backup_path)

    def save_state(self, state: Dict[str, Any]) -> bool:
        with self._state_lock:
            temp_path = None
            try:
                if self._state_corrupt and self.state_path.exists():
                    self._backup_corrupt_state()

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
            if (
                not self._state_corrupt
                and all(
                    key in state and state[key] == value
                    for key, value in kwargs.items()
                )
            ):
                return True
            state.update(kwargs)
            return self.save_state(state)

    def compare_and_update_state(
        self,
        expected_state: Dict[str, Any],
        **kwargs,
    ) -> tuple[str, Dict[str, Any]]:
        """仅在状态未变化时强制合并保存，并返回保存后的实际快照。"""

        with self._state_lock:
            current_state = self.load_state()
            if current_state != expected_state:
                return "changed", current_state

            target_state = dict(current_state)
            target_state.update(kwargs)
            saved = self.save_state(target_state)
            observed_state = self.load_state()
            return ("saved" if saved else "failed"), observed_state
