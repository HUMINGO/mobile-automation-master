"""Send a standalone Feishu text notification using the local Codex config."""

import argparse
from pathlib import Path
import sys

from mobile_automation import DEFAULT_CONFIG_PATH, FeishuError, send_feishu_text


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("message", nargs="?", default="移动自动化：飞书通知测试成功")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="飞书机器人 JSON 配置文件",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        send_feishu_text(args.message, config_path=args.config)
        print("飞书通知发送成功")
        return 0
    except FeishuError as exc:
        print("飞书通知发送失败：{}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
