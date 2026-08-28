"""Command line interface for mobile automation."""

import argparse
import json
from pathlib import Path
import sys

from .adb import AdbClient, AdbError
from .runner import TaskRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mobile-auto", description="Python 手机自动化工具")
    parser.add_argument("--serial", help="指定设备序列号，多设备连接时必须提供")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("devices", help="列出已连接设备")

    run_parser = subparsers.add_parser("run", help="运行 JSON 自动化任务")
    run_parser.add_argument("task", type=Path, help="任务 JSON 文件")
    run_parser.add_argument("--artifacts", type=Path, default=Path("artifacts"))

    shot_parser = subparsers.add_parser("screenshot", help="截取设备屏幕")
    shot_parser.add_argument("output", type=Path)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        client = AdbClient(serial=args.serial)
        if args.command == "devices":
            devices = client.devices()
            if not devices:
                print("未发现设备")
            for device in devices:
                print("{}\t{}\t{}".format(device.serial, device.state, device.model or "-"))
        elif args.command == "run":
            payload = json.loads(args.task.read_text(encoding="utf-8"))
            steps = payload["steps"] if isinstance(payload, dict) else payload
            TaskRunner(client, args.artifacts).run(steps)
            print("任务执行完成")
        elif args.command == "screenshot":
            print(client.screenshot(args.output))
        return 0
    except (AdbError, OSError, ValueError, KeyError, json.JSONDecodeError, RuntimeError) as exc:
        print("错误：{}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

