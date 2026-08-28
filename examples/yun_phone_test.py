"""Connect to the cloud phone, open Poppo, and click the My navigation tab."""

import argparse
from pathlib import Path
import socket
import subprocess
import sys
import time

from mobile_automation import AdbClient, AdbError, UiTree


ADB_SERIAL = "localhost:61046"
POPPO_PACKAGE = "com.baitu.qingshu"
MY_NAV_ID = "com.baitu.qingshu:id/navMe"
SSH_COMMAND = [
    "ssh",
    "-oStrictHostKeyChecking=accept-new",
    "s@162.128.224.130",
    "-p",
    "1824",
    "-L",
    "61046:localhost:1",
    "-Nf",
]


def local_port_is_open(host="localhost", port=61046, timeout=1.0):
    """Return whether the local end of the SSH tunnel is accepting connections."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def ensure_ssh_tunnel(skip_tunnel=False):
    """Reuse an existing tunnel or run the configured SSH forwarding command."""
    if local_port_is_open():
        print("检测到 SSH 隧道已存在：localhost:61046")
        return
    if skip_tunnel:
        raise RuntimeError("localhost:61046 未监听，请先手工创建 SSH 隧道")

    print("未检测到隧道，正在启动 SSH；如有提示，请在终端输入连接密码……")
    try:
        subprocess.run(SSH_COMMAND, check=True)
    except FileNotFoundError as exc:
        raise RuntimeError("找不到 ssh 命令") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("SSH 隧道创建失败") from exc

    for _ in range(10):
        if local_port_is_open():
            return
        time.sleep(0.5)
    raise RuntimeError("SSH 命令已返回，但 localhost:61046 未开始监听")


def connect_cloud_phone(serial=ADB_SERIAL, timeout=15.0):
    """Connect ADB and wait until the selected cloud phone is ready."""
    client = AdbClient(serial=serial)
    result = client.run("connect", serial, include_serial=False, timeout=timeout)
    print("ADB：{}".format(result or "连接命令已完成"))

    deadline = time.monotonic() + timeout
    last_state = "未发现"
    while time.monotonic() < deadline:
        matched = [device for device in client.devices() if device.serial == serial]
        if matched:
            last_state = matched[0].state
            if last_state == "device":
                return client, matched[0]
        time.sleep(0.5)
    raise AdbError("云手机 {} 未就绪，当前状态：{}".format(serial, last_state))


def wait_and_click_my(
    client,
    output_dir=Path("artifacts/yun_phone"),
    attempts=3,
    retry_delay=5.0,
):
    """Capture each UI attempt and click the My tab once it appears."""
    output_dir.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, attempts + 1):
        xml_path = output_dir / "poppo_ui_attempt_{}.xml".format(attempt)
        tree = UiTree.capture(client, save_to=xml_path)
        node = tree.find_by_resource_id(MY_NAV_ID)
        if node is not None:
            x, y = UiTree.click(client, node)
            print("已点击 My 导航：{}，坐标=({}, {})".format(MY_NAV_ID, x, y))
            print("UI 树已保存：{}".format(xml_path.resolve()))
            return x, y
        if attempt < attempts:
            print(
                "第 {}/{} 次未找到 My，{} 秒后重试……".format(
                    attempt, attempts, retry_delay
                )
            )
            time.sleep(retry_delay)
    raise RuntimeError(
        "连续 {} 次未找到 My 导航：{}；UI 树位于 {}".format(
            attempts, MY_NAV_ID, output_dir.resolve()
        )
    )


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", default=ADB_SERIAL, help="云手机 ADB 序列号")
    parser.add_argument("--package", default=POPPO_PACKAGE, help="Poppo Android 包名")
    parser.add_argument(
        "--skip-tunnel",
        action="store_true",
        help="不执行 SSH 命令；要求 localhost:61046 已经监听",
    )
    parser.add_argument("--attempts", type=int, default=3, help="查找 My 的最大次数")
    parser.add_argument("--retry-delay", type=float, default=5.0, help="重试间隔秒数")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/yun_phone"),
        help="UI 树保存目录",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.attempts < 1:
        print("执行失败：--attempts 必须大于 0", file=sys.stderr)
        return 1
    try:
        ensure_ssh_tunnel(skip_tunnel=args.skip_tunnel)
        client, device = connect_cloud_phone(args.serial)
        print("已连接云手机：{} {}".format(device.serial, device.model or ""))
        print("正在强制关闭并重新启动 Poppo：{}".format(args.package))
        client.stop_app(args.package)
        client.start_app(args.package)
        wait_and_click_my(
            client,
            output_dir=args.output_dir,
            attempts=args.attempts,
            retry_delay=args.retry_delay,
        )
        print("云手机测试成功")
        return 0
    except (AdbError, OSError, RuntimeError, ValueError) as exc:
        print("执行失败：{}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
