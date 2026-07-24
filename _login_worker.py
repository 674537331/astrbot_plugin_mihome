# -*- coding: utf-8 -*-
import sys
import logging
import os
import stat
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logging.getLogger("mijiaAPI").setLevel(logging.WARNING)

try:
    from mijiaAPI import mijiaAPI
except (ImportError, OSError) as e:
    if "ARC4" in str(e).upper():
        print(
            "ERROR: pycryptodome 的 ARC4 原生模块加载失败；"
            "请在 AstrBot 使用的 Python 环境中重新安装 pycryptodome==3.23.0。",
            flush=True,
        )
    else:
        print(f"ERROR: 缺少依赖库 - {e}", flush=True)
    sys.exit(1)


def is_link_or_reparse_point(path: Path) -> bool:
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


def main():
    if len(sys.argv) < 2:
        print("ERROR: 未指定 auth.json 路径", flush=True)
        sys.exit(1)

    auth_path = sys.argv[1]
    auth_file = Path(auth_path)
    auth_created_by_worker = (
        not auth_file.exists()
        and not is_link_or_reparse_point(auth_file)
    )
    print("[WORKER] 开始初始化认证环境。", flush=True)

    try:
        if is_link_or_reparse_point(auth_file):
            raise RuntimeError("auth.json 不能是链接或重解析点")
        if is_link_or_reparse_point(auth_file.parent):
            raise RuntimeError("凭证目录不能是链接或重解析点")
        auth_file.parent.mkdir(parents=True, exist_ok=True)
        auth_file.parent.chmod(0o700)
        if (
            os.name != "nt"
            and stat.S_IMODE(auth_file.parent.stat().st_mode) & 0o077
        ):
            raise RuntimeError("凭证目录权限加固未生效")
        api = mijiaAPI(auth_path)
        print("[WORKER] API 实例已创建，正在请求小米服务器...", flush=True)
        api.login()
        if is_link_or_reparse_point(auth_file) or not auth_file.is_file():
            raise RuntimeError("auth.json 文件类型异常")
        auth_file.chmod(0o600)
        if os.name != "nt" and stat.S_IMODE(auth_file.stat().st_mode) & 0o077:
            raise RuntimeError("auth.json 权限加固未生效")
        print("\n[WORKER_SUCCESS] 授权完毕。", flush=True)
    except Exception as e:
        if auth_created_by_worker and (
            auth_file.exists() or is_link_or_reparse_point(auth_file)
        ):
            try:
                auth_file.unlink()
            except Exception:
                pass
        print(f"\n[WORKER_ERROR] 登录流程失败: {type(e).__name__}: {e}", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
