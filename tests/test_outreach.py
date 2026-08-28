import csv

import pytest

from mobile_automation.outreach import (
    UserFilter,
    build_outreach_plan,
    load_contacted_user_ids,
    match_user,
    parse_number,
    write_outreach_plan,
)


def _write_users(path, rows):
    fields = [
        "iteration", "user_id", "profile_name", "gender", "age", "country",
        "source_amount", "face_authentication", "online_status", "interest_tags", "status",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_parse_number_supports_compact_values():
    assert parse_number("1,250") == 1250
    assert parse_number("2.4K") == 2400
    assert parse_number("1.1M") == 1100000
    assert parse_number("") is None


def test_filter_rejects_unknown_age_when_age_rule_is_active():
    reasons = match_user(
        {"user_id": "1", "status": "ok", "gender": "female", "age": ""},
        UserFilter(genders=("female",), min_age=20),
    )
    assert reasons == ["unknown_age", "min_age"]


def test_filter_never_allows_minors():
    with pytest.raises(ValueError, match="18"):
        UserFilter(min_age=17)

    assert "underage" in match_user(
        {"user_id": "1", "status": "ok", "age": "17"}, UserFilter()
    )
    assert "unknown_age" in match_user(
        {"user_id": "1", "status": "ok", "age": ""}, UserFilter()
    )


def test_filter_ignores_collected_online_status():
    reasons = match_user(
        {
            "user_id": "1",
            "status": "ok",
            "age": "25",
            "online_status": "Offline",
        },
        UserFilter(),
    )
    assert reasons == []


def test_plan_deduplicates_uses_newest_and_excludes_contacted(tmp_path):
    users = tmp_path / "users.csv"
    _write_users(
        users,
        [
            {"iteration": "1", "user_id": "10", "profile_name": "Old", "gender": "female", "age": "25", "country": "US", "source_amount": "1,000", "status": "ok"},
            {"iteration": "2", "user_id": "20", "profile_name": "Sent", "gender": "female", "age": "30", "country": "US", "source_amount": "3,000", "status": "ok"},
            {"iteration": "3", "user_id": "10", "profile_name": "New", "gender": "female", "age": "26", "country": "US", "source_amount": "2,000", "status": "ok"},
            {"iteration": "4", "user_id": "30", "profile_name": "Male", "gender": "male", "age": "28", "country": "US", "source_amount": "4,000", "status": "ok"},
        ],
    )
    plan = build_outreach_plan(
        users,
        UserFilter(genders=("female",), min_age=21, min_source_amount=1500),
        contacted_user_ids={"20"},
    )
    assert [item["user_id"] for item in plan] == ["10"]
    assert list(plan[0]) == [
        "user_id",
        "profile_name",
        "gender",
        "age",
        "country",
        "status",
        "reason",
    ]
    assert plan[0]["status"] == "pending_review"


def test_history_only_excludes_sent_and_plan_write_is_atomic(tmp_path):
    history = tmp_path / "history.csv"
    history.write_text("user_id,status\n1,sent\n2,failed\n", encoding="utf-8")
    assert load_contacted_user_ids(history) == {"1"}

    output = tmp_path / "plan.csv"
    assert write_outreach_plan(output, [{"user_id": "2", "status": "pending_review"}]) == 1
    assert not (tmp_path / "plan.csv.tmp").exists()
    assert "pending_review" in output.read_text(encoding="utf-8")
