# -*- coding: utf-8 -*-
"""在可终止子进程中拉取并校验公开设备规格，输出与 mijiaAPI 兼容的 schema。"""

import json
import re
import sys
from pathlib import Path

import requests


MODEL_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}")
SPEC_URL_TEMPLATE = "https://home.miot-spec.com/spec/{model}"
APP_JSON_PATTERN = re.compile(
    r'<script data-page="app" type="application/json">(.*?)</script>',
    re.DOTALL,
)
SPEC_FETCH_TIMEOUT = 20.0

# 与 mijiaAPI DevProp 支持的类型保持一致；其余格式直接跳过，
# 避免“未知类型”让整台设备的只读能力被废弃。
_SUPPORTED_TYPES = {"bool", "int", "uint", "float", "string"}


def _lookup_i18n(i18n: dict, key: str) -> str:
    """按 zh_cn → en → 空串的顺序取本地化文案。

    上游 mijiaAPI 对 ``props.i18n.zh_cn`` 使用硬下标，而部分型号
    （如 yeelink.light.mono5）只有 en 语言表，直接导致
    ``KeyError: 'zh_cn'``。这里做语言回退以兼容这些型号。
    """

    if not isinstance(i18n, dict):
        return ""
    for lang in ("zh_cn", "en"):
        table = i18n.get(lang)
        if not isinstance(table, dict):
            continue
        text = str(table.get(key, "") or "").strip()
        if text:
            return text
    return ""


def fetch_device_spec(model: str) -> dict:
    """拉取 miot-spec 页面并解析为插件消费的规格结构。"""

    url = SPEC_URL_TEMPLATE.format(model=model)
    response = requests.get(
        url,
        headers={"User-Agent": "mijiaAPI/4.1.3"},
        timeout=SPEC_FETCH_TIMEOUT,
    )
    if response.status_code != 200:
        raise ValueError(f"设备规格页面返回 {response.status_code}")
    match = APP_JSON_PATTERN.search(response.text)
    if match is None:
        raise ValueError("设备规格页面缺少应用数据")
    content = json.loads(match.group(1))

    props = content.get("props") or {}
    product = props.get("product") or {}
    if not isinstance(product, dict) or not product.get("model"):
        raise ValueError("设备规格结构异常")
    i18n = props.get("i18n") or {}
    services = (props.get("tree") or {}).get("services") or []
    if not isinstance(services, list):
        raise ValueError("设备规格结构异常")

    result = {
        "name": product.get("name") or model,
        "model": product.get("model") or model,
        "properties": [],
        "actions": [],
    }
    properties_name = []
    actions_name = []

    for svc in services:
        if not isinstance(svc, dict):
            continue
        siid = svc.get("iid")
        if not isinstance(siid, int):
            continue
        svc_type = str(svc.get("type") or "")

        for prop in svc.get("properties") or []:
            if not isinstance(prop, dict):
                continue
            piid = prop.get("iid")
            if not isinstance(piid, int):
                continue
            fmt = str(prop.get("format") or "")
            if fmt.startswith("int"):
                prop_type = "int"
            elif fmt.startswith("uint"):
                prop_type = "uint"
            else:
                prop_type = fmt
            if prop_type not in _SUPPORTED_TYPES:
                continue
            access = prop.get("access") or []
            access_str = "".join(
                [
                    "r" if "read" in access else "",
                    "w" if "write" in access else "",
                ]
            )
            zh_cn = _lookup_i18n(
                i18n,
                f"service:{siid:03d}:property:{piid:03d}",
            )
            item = {
                "name": str(prop.get("type") or ""),
                "description": f"{prop.get('description') or ''} / {zh_cn}".rstrip(
                    " / "
                ),
                "type": prop_type,
                "rw": access_str,
                "range": prop.get("valueRange", None),
                "value-list": None,
                "method": {"siid": siid, "piid": piid},
            }
            if prop.get("valueList"):
                item["value-list"] = []
                for vl_item in prop["valueList"]:
                    if not isinstance(vl_item, dict):
                        continue
                    vl_zh = _lookup_i18n(
                        i18n,
                        str(vl_item.get("i18nKey") or ""),
                    )
                    vl_entry = {
                        "value": vl_item.get("value"),
                        "description": str(vl_item.get("description") or ""),
                    }
                    if vl_zh:
                        vl_entry["desc_zh_cn"] = vl_zh
                    item["value-list"].append(vl_entry)
            if item["name"] in properties_name:
                item["name"] = f"{svc_type}-{item['name']}"
            properties_name.append(item["name"])
            result["properties"].append(item)

        for act in svc.get("actions") or []:
            if not isinstance(act, dict):
                continue
            aiid = act.get("iid")
            if not isinstance(aiid, int):
                continue
            zh_cn = _lookup_i18n(
                i18n,
                f"service:{siid:03d}:action:{aiid:03d}",
            )
            act_item = {
                "name": str(act.get("type") or ""),
                "description": f"{act.get('description') or ''} / {zh_cn}".rstrip(
                    " / "
                ),
                "method": {"siid": siid, "aiid": aiid},
            }
            if act_item["name"] in actions_name:
                act_item["name"] = f"{svc_type}-{act_item['name']}"
            actions_name.append(act_item["name"])
            result["actions"].append(act_item)

    return result


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
        spec = fetch_device_spec(model)
        if (
            not isinstance(spec, dict)
            or not isinstance(spec.get("properties"), list)
            or not isinstance(spec.get("actions"), list)
        ):
            raise ValueError("设备规格结构异常")

        cache_path = cache_dir / f"{model}.json"
        if cache_path.is_symlink():
            raise ValueError("设备规格缓存文件类型异常")
        cache_path.write_text(
            json.dumps(spec, ensure_ascii=False),
            encoding="utf-8",
        )
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
