"""Execute one top-level ``test_*`` function from a test-script module."""

from __future__ import annotations

import argparse
import importlib
import inspect
from typing import Callable, Optional


def load_test_case(module_name: str, function_name: str) -> Callable[[], None]:
    """Import and validate one no-argument top-level test function."""
    module = importlib.import_module(module_name)
    test_case = getattr(module, function_name, None)
    if not callable(test_case) or not function_name.startswith("test_"):
        raise ValueError("未找到测试方法：{}.{}".format(module_name, function_name))
    if getattr(test_case, "__module__", None) != module_name:
        raise ValueError("测试方法必须定义在目标脚本中：{}.{}".format(module_name, function_name))
    required = [
        parameter.name
        for parameter in inspect.signature(test_case).parameters.values()
        if parameter.default is inspect.Parameter.empty
        and parameter.kind in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        }
    ]
    if required:
        raise ValueError(
            "测试方法不能包含必填参数：{}.{}（{}）".format(
                module_name, function_name, ", ".join(required)
            )
        )
    return test_case


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="执行一个 Android UI 自动化测试方法")
    parser.add_argument("module", help="例如 test_script.test_settings")
    parser.add_argument("function", help="例如 test_setting_page")
    args = parser.parse_args(argv)

    test_case = load_test_case(args.module, args.function)
    print("开始执行测试用例：{}::{}".format(args.module, args.function))
    test_case()
    print("测试用例通过：{}::{}".format(args.module, args.function))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
