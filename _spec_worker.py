# -*- coding: utf-8 -*-
import json
import re
import sys
from pathlib import Path

from mijiaAPI import get_device_info


MODEL_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}")


def main() -> None:
    if len(sys.argv) != 3:
        print("ERROR: 参数数量错误", flush=True)
        raise SystemExit(2)

    model = str(sys.argv[1] or "").strip()
    cache_dir = Path(sys.argv[2])
    if not MODEL_PATTERN.fullmatch(model):
        print("ERROR: 设备型号格式异常", flush=True)
        raise SystemExit(2)
    if cache_dir.is_symlink() or not cache_dir.is_dir():
        print("ERROR: 临时缓存目录类型异常", flush=True)
        raise SystemExit(2)

    try:
        spec = get_device_info(model, cache_path=cache_dir)
        if (
            not isinstance(spec, dict)
            or not isinstance(spec.get("properties"), list)
            or not isinstance(spec.get("actions"), list)
        ):
            raise ValueError("设备规格结构异常")

        cache_path = cache_dir / f"{model}.json"
        if cache_path.is_symlink() or not cache_path.is_file():
            raise ValueError("设备规格缓存文件类型异常")
        with cache_path.open("r", encoding="utf-8") as file:
            persisted = json.load(file)
        if persisted != spec:
            raise ValueError("设备规格缓存校验失败")
        cache_path.chmod(0o600)
    except Exception as exc:
        print(
            f"ERROR: 设备规格获取失败 ({type(exc).__name__})",
            flush=True,
        )
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
