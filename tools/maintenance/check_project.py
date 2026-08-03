#!/usr/bin/env python3
"""米家插件发布前项目检查。

默认只读取仓库文件。单元测试与 Ruff 仅在显式传参时执行。
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SKIP_DIRS = {".git", ".ruff_cache", "__pycache__"}
REQUIRED_FILES = (
    "metadata.yaml",
    "requirements.txt",
    "_conf_schema.json",
    "main.py",
    "mihome_client.py",
    "web_api.py",
    "README.md",
    "CHANGELOG.md",
)
SENSITIVE_FILENAMES = {
    ".env",
    "auth.json",
    "cookies.json",
    "credentials.json",
    "login_result.json",
    "state.json",
    "token.json",
}
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
NODE_ENV = "MIHOME_NODE"
RUFF_ENV = "MIHOME_RUFF"
RUFF_RULES = "E4,E7,E9,F"


@dataclass(frozen=True)
class Result:
    name: str
    state: str
    detail: str


def relative(path: Path) -> str:
    """返回统一分隔符的仓库相对路径。"""

    return path.relative_to(ROOT).as_posix()


def is_skipped(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts)


def source_files(suffix: str) -> list[Path]:
    return sorted(
        path
        for path in ROOT.rglob(f"*{suffix}")
        if path.is_file() and not is_skipped(path)
    )


def configured_tool(name: str, environment_key: str) -> str | None:
    configured = os.environ.get(environment_key, "").strip()
    if configured:
        path = Path(configured).expanduser()
        return str(path) if path.is_file() else None
    return shutil.which(name)


def check_required_files() -> Result:
    missing = [name for name in REQUIRED_FILES if not (ROOT / name).is_file()]
    if missing:
        return Result("项目结构", "FAIL", f"缺少文件：{', '.join(missing)}")
    return Result("项目结构", "PASS", f"{len(REQUIRED_FILES)} 个必需文件齐全")


def check_python_syntax() -> Result:
    failures: list[str] = []
    files = source_files(".py")
    for path in files:
        try:
            source = path.read_text(encoding="utf-8")
            ast.parse(source, filename=relative(path))
        except (OSError, SyntaxError, UnicodeError) as exc:
            failures.append(f"{relative(path)}: {exc}")
    if failures:
        return Result("Python 语法", "FAIL", "；".join(failures))
    return Result("Python 语法", "PASS", f"已解析 {len(files)} 个文件")


def check_json_syntax() -> Result:
    failures: list[str] = []
    files = source_files(".json")
    for path in files:
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            failures.append(f"{relative(path)}: {exc}")
    if failures:
        return Result("JSON 语法", "FAIL", "；".join(failures))
    return Result("JSON 语法", "PASS", f"已解析 {len(files)} 个文件")


def metadata_value(source: str, key: str) -> str | None:
    match = re.search(
        rf"(?m)^{re.escape(key)}:\s*[\"']?([^\"'\r\n]+?)[\"']?\s*$",
        source,
    )
    return match.group(1).strip() if match else None


def pinned_requirement(name: str, source: str) -> str | None:
    match = re.search(
        rf"(?im)^{re.escape(name)}==([0-9A-Za-z.+-]+)\s*$",
        source,
    )
    return match.group(1) if match else None


def check_release_versions() -> Result:
    try:
        metadata = (ROOT / "metadata.yaml").read_text(encoding="utf-8")
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return Result("版本关系", "FAIL", str(exc))

    raw_version = metadata_value(metadata, "version")
    if raw_version is None:
        return Result("版本关系", "FAIL", "metadata.yaml 缺少 version")
    version = raw_version.removeprefix("v")
    if not SEMVER_RE.fullmatch(version):
        return Result("版本关系", "FAIL", f"插件版本不是 SemVer：{raw_version}")

    mijia_version = pinned_requirement("mijiaAPI", requirements)
    if mijia_version is None:
        return Result(
            "版本关系",
            "FAIL",
            "requirements.txt 必须使用 mijiaAPI==版本号",
        )

    changelog_patterns = (
        rf"(?m)^##\s+\[?v?{re.escape(version)}\]?",
        rf"(?m)^#{{1,3}}\s+v?{re.escape(version)}(?:\s|$)",
    )
    if not any(re.search(pattern, changelog) for pattern in changelog_patterns):
        return Result(
            "版本关系",
            "FAIL",
            f"CHANGELOG.md 中未找到 {version} 的版本标题",
        )

    return Result(
        "版本关系",
        "PASS",
        f"插件 {raw_version}，mijiaAPI 固定为 {mijia_version}",
    )


def tracked_and_untracked_files() -> list[Path]:
    git = shutil.which("git")
    if git is None:
        return [
            path
            for path in ROOT.rglob("*")
            if path.is_file() and not is_skipped(path)
        ]
    process = subprocess.run(
        [
            git,
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if process.returncode != 0:
        return []
    return [
        ROOT / entry.decode("utf-8", errors="replace")
        for entry in process.stdout.split(b"\0")
        if entry
    ]


def check_sensitive_artifacts() -> Result:
    files = tracked_and_untracked_files()
    if not files:
        return Result("认证文件", "SKIP", "无法读取 Git 文件列表")
    found = sorted(
        relative(path)
        for path in files
        if path.name.lower() in SENSITIVE_FILENAMES
    )
    if found:
        return Result(
            "认证文件",
            "FAIL",
            f"发现可能包含认证信息的文件：{', '.join(found)}",
        )
    return Result("认证文件", "PASS", f"已检查 {len(files)} 个候选文件")


def check_javascript(require_tool: bool) -> Result:
    files = source_files(".js")
    if not files:
        return Result("JavaScript 语法", "PASS", "项目中没有 JavaScript 文件")
    node = configured_tool("node", NODE_ENV)
    if node is None:
        state = "FAIL" if require_tool else "SKIP"
        return Result("JavaScript 语法", state, "未找到 Node.js")

    failures: list[str] = []
    for path in files:
        process = subprocess.run(
            [node, "--check", str(path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if process.returncode:
            detail = (process.stderr or process.stdout).strip()
            failures.append(f"{relative(path)}: {detail}")
    if failures:
        return Result("JavaScript 语法", "FAIL", "；".join(failures))
    return Result("JavaScript 语法", "PASS", f"已检查 {len(files)} 个文件")


def run_tests() -> Result:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-v",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    output = "\n".join(
        part.strip() for part in (process.stdout, process.stderr) if part.strip()
    )
    summary = next(
        (
            line.strip()
            for line in reversed(output.splitlines())
            if line.startswith(("OK", "FAILED", "Ran "))
        ),
        f"退出码 {process.returncode}",
    )
    if process.returncode:
        tail = "\n".join(output.splitlines()[-20:])
        return Result("单元测试", "FAIL", tail or summary)
    return Result("单元测试", "PASS", summary)


def run_ruff(require_tool: bool) -> Result:
    ruff = configured_tool("ruff", RUFF_ENV)
    if ruff:
        command = [ruff, "check", "--select", RUFF_RULES, "."]
    elif importlib.util.find_spec("ruff") is not None:
        command = [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--select",
            RUFF_RULES,
            ".",
        ]
    else:
        state = "FAIL" if require_tool else "SKIP"
        return Result("Ruff", state, "当前 Python 环境未安装 Ruff")

    process = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    output = (process.stdout or process.stderr).strip()
    if process.returncode:
        return Result("Ruff", "FAIL", output)
    return Result("Ruff", "PASS", output or "检查通过")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tests", action="store_true", help="运行单元测试")
    parser.add_argument("--ruff", action="store_true", help="运行 Ruff")
    parser.add_argument(
        "--all",
        action="store_true",
        help="运行静态检查、单元测试和 Ruff",
    )
    parser.add_argument(
        "--require-optional-tools",
        action="store_true",
        help="Node.js 或 Ruff 缺失时判定失败",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    results = [
        check_required_files(),
        check_python_syntax(),
        check_json_syntax(),
        check_release_versions(),
        check_sensitive_artifacts(),
        check_javascript(args.require_optional_tools),
    ]
    if args.tests or args.all:
        results.append(run_tests())
    if args.ruff or args.all:
        results.append(run_ruff(args.require_optional_tools))

    print(f"仓库：{ROOT}")
    for result in results:
        print(f"[{result.state:<4}] {result.name}：{result.detail}")

    failures = [result for result in results if result.state == "FAIL"]
    print(
        f"\n结果：{len(results) - len(failures)}/{len(results)} 项无失败，"
        f"{len(failures)} 项失败"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
