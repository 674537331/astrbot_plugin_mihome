#!/usr/bin/env python3
"""生成离线、只读的源码体积与 Python 静态性能基线。"""

from __future__ import annotations

import argparse
import ast
import json
import os
import statistics
import subprocess
import sys
import time
import tracemalloc
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SOURCE_SUFFIXES = {".py", ".js", ".css", ".html"}
SKIP_DIRS = {".git", ".ruff_cache", "__pycache__"}
BLOCKING_CALLS = {
    "requests.delete",
    "requests.get",
    "requests.patch",
    "requests.post",
    "requests.put",
    "requests.request",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.run",
    "time.sleep",
    "urllib.request.urlopen",
}


@dataclass(frozen=True)
class FileMetric:
    path: str
    bytes: int
    lines: int
    nonempty_lines: int


class AsyncBlockingVisitor(ast.NodeVisitor):
    """查找 async 函数中直接出现的常见同步阻塞调用。"""

    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.async_depth = 0
        self.findings: list[dict[str, Any]] = []

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.async_depth += 1
        self.generic_visit(node)
        self.async_depth -= 1

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if self.async_depth:
            return
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if self.async_depth:
            name = qualified_name(node.func)
            if name in BLOCKING_CALLS:
                self.findings.append(
                    {
                        "path": self.filename,
                        "line": node.lineno,
                        "call": name,
                    }
                )
        self.generic_visit(node)


def qualified_name(node: ast.AST) -> str:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def include_path(path: Path, include_tests: bool) -> bool:
    relative = path.relative_to(ROOT)
    if any(part in SKIP_DIRS for part in relative.parts):
        return False
    if not include_tests and relative.parts and relative.parts[0] == "tests":
        return False
    return path.is_file() and path.suffix.lower() in SOURCE_SUFFIXES


def collect_sources(include_tests: bool) -> tuple[list[FileMetric], dict[Path, str]]:
    metrics: list[FileMetric] = []
    python_sources: dict[Path, str] = {}
    for path in sorted(ROOT.rglob("*")):
        if not include_path(path, include_tests):
            continue
        data = path.read_bytes()
        text = data.decode("utf-8")
        lines = text.splitlines()
        metrics.append(
            FileMetric(
                path=path.relative_to(ROOT).as_posix(),
                bytes=len(data),
                lines=len(lines),
                nonempty_lines=sum(bool(line.strip()) for line in lines),
            )
        )
        if path.suffix.lower() == ".py":
            python_sources[path] = text
    return metrics, python_sources


def benchmark_ast(
    sources: dict[Path, str],
    rounds: int,
) -> dict[str, float | int]:
    timings: list[float] = []
    tracemalloc.start()
    try:
        for _ in range(rounds):
            start = time.perf_counter()
            for path, source in sources.items():
                ast.parse(source, filename=path.relative_to(ROOT).as_posix())
            timings.append((time.perf_counter() - start) * 1000)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return {
        "rounds": rounds,
        "median_ms": round(statistics.median(timings), 3),
        "min_ms": round(min(timings), 3),
        "max_ms": round(max(timings), 3),
        "peak_kib": round(peak / 1024, 1),
    }


def find_blocking_calls(sources: dict[Path, str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for path, source in sources.items():
        relative = path.relative_to(ROOT).as_posix()
        tree = ast.parse(source, filename=relative)
        visitor = AsyncBlockingVisitor(relative)
        visitor.visit(tree)
        findings.extend(visitor.findings)
    return findings


def measure_import(module: str, timeout: float) -> dict[str, Any]:
    snippet = "\n".join(
        (
            "import importlib, json, sys, time, tracemalloc",
            "tracemalloc.start()",
            "started = time.perf_counter()",
            "importlib.import_module(sys.argv[1])",
            "elapsed = (time.perf_counter() - started) * 1000",
            "current, peak = tracemalloc.get_traced_memory()",
            "print(json.dumps({'elapsed_ms': round(elapsed, 3), "
            "'current_kib': round(current / 1024, 1), "
            "'peak_kib': round(peak / 1024, 1)}))",
        )
    )
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    process = subprocess.run(
        [sys.executable, "-c", snippet, module],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if process.returncode:
        return {
            "module": module,
            "ok": False,
            "error": (process.stderr or process.stdout).strip(),
        }
    try:
        measurement = json.loads(process.stdout)
    except json.JSONDecodeError:
        return {
            "module": module,
            "ok": False,
            "error": f"无法解析子进程输出：{process.stdout.strip()}",
        }
    return {"module": module, "ok": True, **measurement}


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    metrics, python_sources = collect_sources(args.include_tests)
    blocking_calls = find_blocking_calls(python_sources)
    totals = {
        "files": len(metrics),
        "bytes": sum(item.bytes for item in metrics),
        "lines": sum(item.lines for item in metrics),
        "nonempty_lines": sum(item.nonempty_lines for item in metrics),
        "python_files": len(python_sources),
    }
    imports: list[dict[str, Any]] = []
    for module in args.import_module:
        try:
            imports.append(measure_import(module, args.import_timeout))
        except subprocess.TimeoutExpired:
            imports.append(
                {
                    "module": module,
                    "ok": False,
                    "error": f"导入超过 {args.import_timeout:g} 秒",
                }
            )

    return {
        "root": str(ROOT),
        "include_tests": args.include_tests,
        "totals": totals,
        "largest_files": [
            asdict(item)
            for item in sorted(metrics, key=lambda item: item.bytes, reverse=True)[
                : args.top
            ]
        ],
        "ast_parse": benchmark_ast(python_sources, args.rounds),
        "async_blocking_calls": blocking_calls,
        "imports": imports,
    }


def print_report(report: dict[str, Any]) -> None:
    totals = report["totals"]
    print(f"仓库：{report['root']}")
    print(
        "源码总量："
        f"{totals['files']} 个文件，{totals['bytes'] / 1024:.1f} KiB，"
        f"{totals['lines']} 行（有效 {totals['nonempty_lines']} 行）"
    )
    print(f"Python 文件：{totals['python_files']} 个")
    print("\n最大源码文件：")
    for item in report["largest_files"]:
        print(
            f"  {item['bytes'] / 1024:8.1f} KiB  "
            f"{item['nonempty_lines']:5d} 有效行  {item['path']}"
        )

    benchmark = report["ast_parse"]
    print(
        "\nPython 静态解析基线："
        f"{benchmark['rounds']} 轮，中位 {benchmark['median_ms']:.3f} ms，"
        f"范围 {benchmark['min_ms']:.3f}–{benchmark['max_ms']:.3f} ms，"
        f"峰值 {benchmark['peak_kib']:.1f} KiB"
    )

    findings = report["async_blocking_calls"]
    print(f"\n异步函数直接阻塞调用：{len(findings)} 处")
    for finding in findings:
        print(
            f"  {finding['path']}:{finding['line']}  {finding['call']}"
        )
    if findings:
        print("  请逐项确认调用是否已通过线程、子进程或其他方式隔离。")

    if report["imports"]:
        print("\n模块冷启动导入：")
        for item in report["imports"]:
            if item["ok"]:
                print(
                    f"  {item['module']}：{item['elapsed_ms']:.3f} ms，"
                    f"峰值 {item['peak_kib']:.1f} KiB"
                )
            else:
                print(f"  {item['module']}：失败：{item['error']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--include-tests",
        action="store_true",
        help="将 tests 目录计入体积和静态分析",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=10,
        help="Python AST 解析轮数（默认：10）",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=8,
        help="显示最大的源码文件数量（默认：8）",
    )
    parser.add_argument(
        "--import-module",
        action="append",
        default=[],
        metavar="MODULE",
        help="在隔离子进程中测量指定模块导入，可重复使用",
    )
    parser.add_argument(
        "--import-timeout",
        type=float,
        default=20.0,
        help="单个模块导入超时秒数（默认：20）",
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.rounds < 1 or args.rounds > 100:
        print("错误：rounds 必须在 1 到 100 之间", file=sys.stderr)
        return 2
    if args.top < 1 or args.top > 100:
        print("错误：top 必须在 1 到 100 之间", file=sys.stderr)
        return 2
    if args.import_timeout <= 0 or args.import_timeout > 120:
        print("错误：import-timeout 必须大于 0 且不超过 120 秒", file=sys.stderr)
        return 2

    try:
        report = build_report(args)
    except (OSError, UnicodeError, SyntaxError) as exc:
        print(f"性能基线生成失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report)

    failed_imports = [
        item for item in report["imports"] if not item.get("ok", False)
    ]
    return 1 if failed_imports else 0


if __name__ == "__main__":
    raise SystemExit(main())
