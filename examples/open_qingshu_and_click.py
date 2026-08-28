"""Open Poppo live and resiliently collect profiles from the Square WebView."""

import argparse
from pathlib import Path
import sys
import time

from mobile_automation import (
    AdbClient,
    AdbError,
    DEFAULT_CONFIG_PATH,
    FeishuError,
    collect_square_users,
    send_feishu_text,
)


def notify_feishu(message, enabled=True, config_path=DEFAULT_CONFIG_PATH):
    """Send a best-effort task notification without interrupting automation."""
    if not enabled:
        return False
    try:
        send_feishu_text(message, config_path=config_path)
        print("飞书通知已发送")
        return True
    except FeishuError as exc:
        print("飞书通知发送失败（自动化继续）：{}".format(exc), file=sys.stderr)
        return False


def select_device(serial=None, retry_count=10, retry_interval=30, sleep=time.sleep):
    """Select an authorized device, retrying unavailable connections."""
    if retry_count < 0:
        raise ValueError("retry_count 不能为负数")
    if retry_interval < 0:
        raise ValueError("retry_interval 不能为负数")

    probe = AdbClient()
    last_error = None
    device = None
    ready = []
    for attempt in range(retry_count + 1):
        try:
            devices = probe.devices()
        except AdbError as exc:
            last_error = exc
        else:
            if serial:
                matched = [item for item in devices if item.serial == serial]
                if not matched:
                    last_error = AdbError("未找到指定设备：{}".format(serial))
                elif matched[0].state != "device":
                    last_error = AdbError(
                        "设备 {} 当前状态为 {}，请在手机上允许 USB 调试授权".format(
                            matched[0].serial, matched[0].state
                        )
                    )
                else:
                    device = matched[0]
                    break
            else:
                ready = [item for item in devices if item.state == "device"]
                if ready:
                    break
                states = ", ".join(
                    "{} ({})".format(item.serial, item.state) for item in devices
                )
                last_error = AdbError(
                    "没有已授权的可用设备。当前设备：{}".format(states or "无")
                )

        if attempt >= retry_count:
            break
        wait_seconds = retry_interval * (attempt + 1)
        print(
            "未发现可用设备：{}；{} 秒后进行第 {}/{} 次重试".format(
                last_error, wait_seconds, attempt + 1, retry_count
            ),
            file=sys.stderr,
        )
        sleep(wait_seconds)

    if device is None and not ready:
        raise AdbError(
            "{}；等待后重试 {} 次仍未发现可用设备".format(last_error, retry_count)
        )

    if serial:
        return AdbClient(serial=device.serial), device
    if len(ready) > 1:
        print("检测到多台可用设备，请选择：")
        for index, candidate in enumerate(ready, start=1):
            name = candidate.model or candidate.product or candidate.serial
            print(
                "  {}. {} (serial={})".format(index, name, candidate.serial)
            )
        while True:
            try:
                selected = input(
                    "请输入设备编号 [1-{}]：".format(len(ready))
                ).strip()
            except EOFError as exc:
                raise AdbError(
                    "无法读取设备选择，请使用 --serial 指定设备"
                ) from exc
            try:
                selected_index = int(selected)
            except ValueError:
                print("输入无效，请输入 1-{} 之间的数字".format(len(ready)))
                continue
            if not 1 <= selected_index <= len(ready):
                print("输入无效，请输入 1-{} 之间的数字".format(len(ready)))
                continue
            device = ready[selected_index - 1]
            break
    else:
        device = ready[0]
    return AdbClient(serial=device.serial), device


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", help="ADB 设备序列号")
    parser.add_argument(
        "--iterations", type=int, default=10000, help="总目标循环次数，范围 0-10000"
    )
    parser.add_argument(
        "--csv-output",
        type=Path,
        default=Path("artifacts/qingshu/users.csv"),
        help="用户信息 CSV 追加文件",
    )
    parser.add_argument(
        "--state-file", type=Path, help="断点状态文件；默认与 CSV 同目录同名"
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/qingshu"), help="诊断文件目录"
    )
    parser.add_argument("--min-delay", type=float, default=1.0, help="每轮最短等待秒数")
    parser.add_argument("--max-delay", type=float, default=5.0, help="每轮最长等待秒数")
    parser.add_argument(
        "--fresh", action="store_true", help="忽略断点，从 iteration 0 开始新任务"
    )
    parser.add_argument(
        "--feishu-config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="飞书机器人配置文件",
    )
    parser.add_argument(
        "--no-feishu", action="store_true", help="本次任务不发送飞书通知"
    )
    parser.add_argument(
        "--skip-gender-detection",
        action="store_true",
        help="跳过截图与性别颜色识别，直接写入 unknown；适合远程设备",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    feishu_enabled = not args.no_feishu
    try:
        client, device = select_device(args.serial)
        print("已连接设备：{} {}".format(device.serial, device.model or ""))

        def notify_reconnected(reconnected_device):
            notify_feishu(
                "[Qingshu 自动化] 设备重连成功\n"
                "设备：{}\n型号：{}\n采集任务已继续运行".format(
                    reconnected_device.serial,
                    reconnected_device.model or "unknown",
                ),
                enabled=feishu_enabled,
                config_path=args.feishu_config,
            )

        state = collect_square_users(
            client,
            iterations=args.iterations,
            csv_path=args.csv_output,
            min_delay=args.min_delay,
            max_delay=args.max_delay,
            output_dir=args.output_dir,
            state_path=args.state_file,
            fresh=args.fresh,
            skip_gender_detection=args.skip_gender_detection,
            on_device_reconnected=notify_reconnected,
        )
        print(
            "任务结束：next_iteration={}，成功={}，错误={}，连续失败={}".format(
                state.next_iteration,
                state.success_count,
                state.error_count,
                state.consecutive_failed_iterations,
            )
        )
        safely_stopped = state.consecutive_failed_iterations >= 3
        if safely_stopped:
            message = (
                "[Qingshu 自动化] 任务失败\n"
                "失败原因：连续 3 轮均耗尽恢复次数，任务已安全停止\n"
                "下一轮：{}\n成功：{}\n错误：{}\n连续失败：{}"
            ).format(
                state.next_iteration,
                state.success_count,
                state.error_count,
                state.consecutive_failed_iterations,
            )
        else:
            message = (
                "[Qingshu 自动化] 任务完成\n"
                "完成轮次：{} / {}\n成功：{}\n错误：{}"
            ).format(
                state.next_iteration,
                args.iterations,
                state.success_count,
                state.error_count,
            )
        notify_feishu(
            message,
            enabled=feishu_enabled,
            config_path=args.feishu_config,
        )
        return 2 if safely_stopped else 0
    except Exception as exc:
        print("执行失败：{}".format(exc), file=sys.stderr)
        notify_feishu(
            "[Qingshu 自动化] 任务失败\n失败原因：{}".format(exc),
            enabled=feishu_enabled,
            config_path=args.feishu_config,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
