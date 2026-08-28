"""Execute approved Poppo private-message targets with resume history."""

import argparse
import logging
from pathlib import Path
import sys

from mobile_automation import (
    AdbError,
    DeviceReconnectError,
    PoppoMessenger,
    load_approved_targets,
)
from mobile_automation.messaging import DEFAULT_APP_NAME, DEFAULT_TEMPLATE_CONFIG
from open_qingshu_and_click import (
    DEFAULT_CONFIG_PATH,
    notify_feishu,
    select_device,
)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", help="ADB 设备序列号")
    parser.add_argument(
        "--plan",
        type=Path,
        default=Path("artifacts/qingshu/outreach_plan.csv"),
        help="私信计划 CSV；只处理 status=approved",
    )
    parser.add_argument(
        "--templates",
        type=Path,
        default=DEFAULT_TEMPLATE_CONFIG,
        help="国家路由与多语言文案 JSON",
    )
    parser.add_argument(
        "--app-name",
        default=DEFAULT_APP_NAME,
        help="替换文案中的 {{app_name}}，默认 yago",
    )
    parser.add_argument(
        "--history",
        type=Path,
        default=Path("artifacts/qingshu/message_history.csv"),
        help="发送状态与断点历史 CSV",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=10.0,
        help="全部首发完成后扫描 Message 未读列表的间隔秒数",
    )
    parser.add_argument(
        "--online-only",
        action="store_true",
        help="仅向个人页实时状态为 Online 或 Party 的用户发送",
    )
    parser.add_argument(
        "--feishu-config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="飞书机器人配置文件",
    )
    parser.add_argument(
        "--no-feishu",
        action="store_true",
        help="设备连接最终失败时不发送飞书通知",
    )
    parser.add_argument(
        "--skip-send",
        action="store_true",
        help="跳过首条私信，直接进入 Message 等待回复；仍会发送回复后的后续消息",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="本次首发成功人数上限；跳过和失败不占名额",
    )
    parser.add_argument(
        "--verify-user-id",
        help="仅验证指定用户的搜索、详情和会话导航，不发送消息",
    )
    return parser


def main(argv=None):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    args = build_parser().parse_args(argv)
    targets = (
        []
        if args.verify_user_id
        else load_approved_targets(args.plan, args.templates, args.app_name)
    )
    if args.limit is not None and args.limit < 0:
        raise ValueError("limit 不能为负数")
    try:
        client, device = select_device(args.serial)
    except AdbError as exc:
        print("设备连接失败：{}".format(exc), file=sys.stderr)
        notify_feishu(
            "[Qingshu 私信] 设备连接失败\n"
            "已按 30 秒递增等待重试 10 次，仍未发现可用设备\n"
            "失败原因：{}".format(exc),
            enabled=not args.no_feishu,
            config_path=args.feishu_config,
        )
        return 1
    print("已连接设备：{} {}".format(device.serial, device.model or ""))
    print("本次已审核目标：{} 人".format(len(targets)))

    def notify_reconnected(reconnected_device):
        notify_feishu(
            "[Qingshu 私信] 设备重连成功\n"
            "设备：{}\n型号：{}\n任务已继续运行".format(
                reconnected_device.serial,
                reconnected_device.model or "unknown",
            ),
            enabled=not args.no_feishu,
            config_path=args.feishu_config,
        )

    messenger = PoppoMessenger(
        client,
        poll_interval=args.poll_interval,
        online_only=args.online_only,
        on_device_reconnected=notify_reconnected,
    )
    if args.verify_user_id:
        result = messenger.verify_target(args.verify_user_id)
        print(
            "user_id={} nickname={} status={} error={}".format(
                result.user_id, result.nickname, result.status, result.error
            )
        )
        return 0 if result.status == "verified_no_send" else 1
    try:
        results = messenger.run_targets(
            targets,
            args.history,
            monitor_forever=True,
            skip_initial_send=args.skip_send,
            initial_success_limit=None if args.skip_send else args.limit,
        )
    except DeviceReconnectError as exc:
        print("运行中设备重连失败：{}".format(exc), file=sys.stderr)
        notify_feishu(
            "[Qingshu 私信] 运行中设备离线\n"
            "按 30 秒递增等待重连 10 次仍未恢复，任务已停止\n"
            "失败原因：{}".format(exc),
            enabled=not args.no_feishu,
            config_path=args.feishu_config,
        )
        return 1
    for result in results:
        print(
            "user_id={} status={} reply={} error={}".format(
                result.user_id, result.status, result.reply, result.error
            )
        )
    return 1 if any(item.status == "error" for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
