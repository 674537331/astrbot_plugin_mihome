# -*- coding: utf-8 -*-
import base64
import sys
import logging
import os
import stat
from pathlib import Path
from urllib import parse

logging.basicConfig(level=logging.INFO)
logging.getLogger("mijiaAPI").setLevel(logging.WARNING)

WORKER_QR_PAYLOAD_START = "[WORKER_QR_LOGIN_URL]"
WORKER_QR_PAYLOAD_END = "[/WORKER_QR_LOGIN_URL]"
MAX_LOGIN_URL_LENGTH = 8192

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


def _validated_login_url(value: object) -> str:
    """只接受小米账号域名下的 HTTPS 登录二维码载荷。"""

    login_url = str(value or "").strip()
    if (
        not login_url
        or len(login_url) > MAX_LOGIN_URL_LENGTH
        or any(char in login_url for char in "\r\n\x00")
    ):
        raise RuntimeError("上游返回的登录二维码地址无效")
    try:
        parsed = parse.urlparse(login_url)
        hostname = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError("上游返回的登录二维码地址无效") from exc
    if (
        parsed.scheme.lower() != "https"
        or not (
            hostname == "account.xiaomi.com"
            or hostname.endswith(".account.xiaomi.com")
        )
        or port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != "/longPolling/login"
        or not parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("上游返回的登录二维码地址无效")
    return login_url


def _emit_login_qr_payload(login_url: object) -> None:
    """以有边界的进程内协议传递登录载荷，避免与二维码图片地址混淆。"""

    validated = _validated_login_url(login_url)
    encoded = base64.urlsafe_b64encode(validated.encode("utf-8")).decode("ascii")
    print(
        f"{WORKER_QR_PAYLOAD_START}{encoded}{WORKER_QR_PAYLOAD_END}",
        flush=True,
    )


def _login_with_direct_qr(api):
    """复用同一份登录数据展示二维码并等待扫码结果。"""

    get_login_data = getattr(api, "_get_qr_login_data", None)
    complete_login = getattr(api, "_complete_qr_login", None)
    if not callable(get_login_data) or not callable(complete_login):
        raise RuntimeError("当前 mijiaAPI 缺少二维码登录接口，请重新安装依赖")

    login_data = get_login_data()
    if not isinstance(login_data, dict):
        raise RuntimeError("上游返回的二维码登录数据无效")
    if login_data.get("refreshed"):
        return getattr(api, "auth_data", {})

    _emit_login_qr_payload(login_data.get("loginUrl"))
    return complete_login(login_data)


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
        _login_with_direct_qr(api)
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
