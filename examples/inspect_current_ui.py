"""Capture the current Android UI and print likely center controls."""

import argparse
from datetime import datetime
import json
from pathlib import Path

from mobile_automation import AdbClient, UiTree


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", help="ADB 设备序列号")
    parser.add_argument("--limit", type=int, default=10, help="最多输出多少个候选节点")
    parser.add_argument("--all", action="store_true", help="中央候选中包含不可点击节点")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/current_ui"))
    args = parser.parse_args()

    client = AdbClient(serial=args.serial)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    xml_path = args.output_dir / "ui_{}.xml".format(timestamp)
    screenshot_path = args.output_dir / "screen_{}.png".format(timestamp)

    tree = UiTree.capture(client, xml_path)
    client.screenshot(screenshot_path)
    candidates = tree.center_candidates(clickable_only=not args.all, limit=args.limit)

    print("UI 节点数：{}".format(len(tree.nodes)))
    print("屏幕尺寸：{}x{}".format(*tree.screen_size))
    print("UI 文件：{}".format(xml_path.resolve()))
    print("截图文件：{}".format(screenshot_path.resolve()))
    print("中央候选节点：")
    for index, node in enumerate(candidates, start=1):
        print("{}. {}".format(index, json.dumps(node.describe(), ensure_ascii=False)))


if __name__ == "__main__":
    main()
