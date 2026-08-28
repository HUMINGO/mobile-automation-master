"""Filter collected Poppo users and create a reviewable private-message plan."""

import argparse
from pathlib import Path

from mobile_automation import (
    UserFilter,
    build_outreach_plan,
    load_contacted_user_ids,
    write_outreach_plan,
)


def _csv_values(value):
    return tuple(item.strip() for item in value.split(",") if item.strip())


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--users-csv", type=Path, default=Path("artifacts/qingshu/users.csv"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/qingshu/outreach_plan.csv"))
    parser.add_argument("--history", type=Path, help="已有发送结果 CSV；status=sent 的用户会被排除")
    parser.add_argument("--gender", type=_csv_values, default=(), help="允许性别，逗号分隔")
    parser.add_argument("--country", type=_csv_values, default=(), help="允许国家代码，逗号分隔")
    parser.add_argument("--min-age", type=int)
    parser.add_argument("--max-age", type=int)
    parser.add_argument("--min-source-amount", type=float)
    parser.add_argument("--face-authenticated-only", action="store_true")
    parser.add_argument("--limit", type=int)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    criteria = UserFilter(
        genders=args.gender,
        countries=args.country,
        min_age=args.min_age,
        max_age=args.max_age,
        min_source_amount=args.min_source_amount,
        face_authenticated_only=args.face_authenticated_only,
    )
    plan = build_outreach_plan(
        args.users_csv,
        criteria,
        contacted_user_ids=load_contacted_user_ids(args.history),
        limit=args.limit,
    )
    count = write_outreach_plan(args.output, plan)
    print("已生成待人工审核私信计划：{} 人 -> {}".format(count, args.output))
    print("本命令不会打开 App 或发送消息。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
