"""Run project-local UI test scripts in isolated Python processes."""

from __future__ import annotations

import argparse
import ast
from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Iterable, List, Optional

from mobile_automation.reporting import (
    REPORT_CASE_ENV,
    REPORT_DIR_ENV,
    load_case_steps,
)
from utils.html_report import generate_html_report


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCRIPT_DIR = PROJECT_ROOT / "test_script"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "artifacts" / "test_runs"


@dataclass
class TestScriptResult:
    name: str
    status: str
    elapsed_seconds: float
    log_path: str
    return_code: Optional[int] = None
    error: str = ""
    steps: list = field(default_factory=list)


def discover_test_scripts(script_dir: Path, pattern: str = "test_*.py") -> List[Path]:
    """Return runnable test scripts in deterministic filename order."""
    if not script_dir.is_dir():
        raise ValueError("测试脚本目录不存在：{}".format(script_dir))
    return sorted(
        path for path in script_dir.glob(pattern)
        if path.is_file() and path.name != "__init__.py"
    )


def _module_name(script: Path, script_dir: Path) -> str:
    relative = script.relative_to(script_dir).with_suffix("")
    return "test_script." + ".".join(relative.parts)


def discover_test_functions(script: Path) -> List[str]:
    """Return top-level no-framework test function names in source order."""
    try:
        source = script.read_text(encoding="utf-8")
        module = ast.parse(source, filename=str(script))
    except (OSError, SyntaxError) as exc:
        raise ValueError("无法解析测试脚本 {}：{}".format(script, exc)) from exc
    return [
        node.name
        for node in module.body
        if isinstance(node, ast.FunctionDef)
        and node.name.startswith("test_")
    ]


def run_test_scripts(
    scripts: Iterable[Path],
    *,
    script_dir: Path = DEFAULT_SCRIPT_DIR,
    output_dir: Path,
    timeout_seconds: float = 120.0,
    continue_on_error: bool = True,
    dry_run: bool = False,
) -> List[TestScriptResult]:
    """Collect and run every top-level ``test_*`` function in isolation."""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds 必须大于 0")
    output_dir.mkdir(parents=True, exist_ok=True)
    results: List[TestScriptResult] = []
    for script in scripts:
        try:
            test_functions = discover_test_functions(script)
        except ValueError as exc:
            results.append(TestScriptResult(script.name, "failed", 0.0, "", error=str(exc)))
            if not continue_on_error:
                break
            continue
        if not test_functions:
            results.append(TestScriptResult(
                script.name, "failed", 0.0, "",
                error="脚本中未发现顶层 test_* 测试方法",
            ))
            if not continue_on_error:
                break
            continue

        module_name = _module_name(script, script_dir)
        for function_name in test_functions:
            started = datetime.now()
            case_id = "{}__{}".format(script.stem, function_name)
            display_name = "{}::{}".format(script.name, function_name)
            log_path = output_dir / "{}.log".format(case_id)
            command = [
                sys.executable, "-m", "utils.execute_test_case", module_name, function_name,
            ]
            environment = os.environ.copy()
            environment[REPORT_DIR_ENV] = str(output_dir.resolve())
            environment[REPORT_CASE_ENV] = case_id
            print("开始执行：{}".format(display_name))
            if dry_run:
                message = "预览命令：{}".format(" ".join(command))
                log_path.write_text(message + "\n", encoding="utf-8")
                results.append(TestScriptResult(
                    display_name, "dry_run", 0.0, str(log_path), error=message,
                ))
                continue
            try:
                completed = subprocess.run(
                    command,
                    cwd=PROJECT_ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout_seconds,
                    check=False,
                    env=environment,
                )
                output = completed.stdout or ""
                status = "passed" if completed.returncode == 0 else "failed"
                result = TestScriptResult(
                    display_name, status, (datetime.now() - started).total_seconds(),
                    str(log_path), return_code=completed.returncode,
                )
            except subprocess.TimeoutExpired as exc:
                output = exc.stdout or ""
                if isinstance(output, bytes):
                    output = output.decode("utf-8", errors="replace")
                result = TestScriptResult(
                    display_name, "timeout", (datetime.now() - started).total_seconds(),
                    str(log_path), error="超过 {} 秒仍未结束".format(timeout_seconds),
                )
            log_path.write_text(output, encoding="utf-8")
            result.steps = load_case_steps(output_dir, case_id)
            results.append(result)
            print("{}：{}".format(display_name, result.status))
            if result.status != "passed" and not continue_on_error:
                return results
    return results


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="批量运行 test_script 下的 Android UI 测试用例")
    parser.add_argument("--pattern", default="test_*.py", help="测试文件匹配规则，默认 test_*.py")
    parser.add_argument("--timeout", type=float, default=120, help="单个用例超时秒数，默认 120")
    execution_mode = parser.add_mutually_exclusive_group()
    execution_mode.add_argument(
        "--continue-on-error",
        dest="continue_on_error",
        action="store_true",
        default=True,
        help="失败后继续执行后续用例（默认）",
    )
    execution_mode.add_argument(
        "--fail-fast",
        dest="continue_on_error",
        action="store_false",
        help="首个用例失败或超时时立即停止",
    )
    parser.add_argument("--dry-run", action="store_true", help="仅列出将执行的命令，不操作设备")
    parser.add_argument("--output-dir", type=Path, help="日志目录，默认 artifacts/test_runs/时间戳")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = _parser().parse_args(argv)
    scripts = discover_test_scripts(DEFAULT_SCRIPT_DIR, args.pattern)
    if not scripts:
        print("未找到匹配的测试用例：{}".format(args.pattern))
        return 2
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = args.output_dir or DEFAULT_OUTPUT_ROOT / timestamp
    results = run_test_scripts(
        scripts,
        output_dir=output_dir,
        timeout_seconds=args.timeout,
        continue_on_error=args.continue_on_error,
        dry_run=args.dry_run,
    )
    report_path = output_dir / "report.json"
    report_path.write_text(
        json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    html_report_path = generate_html_report(output_dir, results)
    failed = [result for result in results if result.status not in {"passed", "dry_run"}]
    print("执行完成：{}；JSON 报告：{}；可视化报告：{}".format(
        "成功" if not failed else "存在失败", report_path, html_report_path,
    ))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
