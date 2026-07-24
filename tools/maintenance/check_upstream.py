#!/usr/bin/env python3
"""只读核对项目固定的 mijiaAPI 版本与公开上游状态。"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
PYPI_URL = "https://pypi.org/pypi/mijiaAPI/json"
GITHUB_REPO_URL = "https://api.github.com/repos/Do1e/mijia-api"
GITHUB_COMMITS_URL = f"{GITHUB_REPO_URL}/commits?per_page=1"
GITHUB_TAGS_URL = f"{GITHUB_REPO_URL}/tags?per_page=1"
USER_AGENT = "astrbot-plugin-mihome-maintenance/1.0"


def fetch_json(url: str, timeout: float) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json, application/json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def pinned_version() -> str:
    source = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    match = re.search(r"(?im)^mijiaAPI==([0-9A-Za-z.+-]+)\s*$", source)
    if match is None:
        raise ValueError("requirements.txt 未使用 mijiaAPI==版本号")
    return match.group(1)


def collect(timeout: float) -> dict[str, Any]:
    pinned = pinned_version()
    pypi = fetch_json(PYPI_URL, timeout)
    repository = fetch_json(GITHUB_REPO_URL, timeout)
    commits = fetch_json(GITHUB_COMMITS_URL, timeout)
    tags = fetch_json(GITHUB_TAGS_URL, timeout)

    latest_commit = commits[0] if isinstance(commits, list) and commits else {}
    latest_tag = tags[0] if isinstance(tags, list) and tags else {}
    commit_data = latest_commit.get("commit", {})
    author_data = commit_data.get("author", {})

    latest_pypi = str(pypi.get("info", {}).get("version", ""))
    return {
        "project_pinned_version": pinned,
        "pypi_latest_version": latest_pypi,
        "pin_matches_pypi": pinned == latest_pypi,
        "github_default_branch": repository.get("default_branch"),
        "github_pushed_at": repository.get("pushed_at"),
        "github_latest_commit": latest_commit.get("sha"),
        "github_latest_commit_date": author_data.get("date"),
        "github_latest_commit_message": str(commit_data.get("message", "")).split(
            "\n",
            1,
        )[0],
        "github_latest_tag": latest_tag.get("name"),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="每个公开请求的超时秒数（默认：15）",
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.timeout <= 0 or args.timeout > 60:
        print("错误：timeout 必须大于 0 且不超过 60 秒", file=sys.stderr)
        return 2
    try:
        result = collect(args.timeout)
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        json.JSONDecodeError,
        urllib.error.URLError,
    ) as exc:
        print(f"上游核对失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"项目固定版本：{result['project_pinned_version']}")
        print(f"PyPI 最新版本：{result['pypi_latest_version']}")
        print(
            "版本状态："
            + ("已对齐" if result["pin_matches_pypi"] else "需要评估升级")
        )
        print(f"GitHub 默认分支：{result['github_default_branch']}")
        print(
            "GitHub 最新提交："
            f"{result['github_latest_commit']} "
            f"({result['github_latest_commit_date']})"
        )
        print(f"提交摘要：{result['github_latest_commit_message']}")
        print(f"GitHub 最新标签：{result['github_latest_tag']}")

    return 0 if result["pin_matches_pypi"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
