"""Build a deduplicated, reviewable outreach plan from collected profiles."""

import csv
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Dict, Iterable, List, Optional, Sequence, Set


PLAN_FIELDS = [
    "user_id",
    "profile_name",
    "gender",
    "age",
    "country",
    "status",
    "reason",
]


@dataclass(frozen=True)
class UserFilter:
    genders: Sequence[str] = ()
    countries: Sequence[str] = ()
    min_age: Optional[int] = None
    max_age: Optional[int] = None
    min_source_amount: Optional[float] = None
    face_authenticated_only: bool = False

    def __post_init__(self) -> None:
        if self.min_age is not None and self.min_age < 18:
            raise ValueError("min_age 不能小于 18")
        if self.max_age is not None and self.max_age < 18:
            raise ValueError("max_age 不能小于 18")
        if (
            self.min_age is not None
            and self.max_age is not None
            and self.max_age < self.min_age
        ):
            raise ValueError("max_age 不能小于 min_age")
        if self.min_source_amount is not None and self.min_source_amount < 0:
            raise ValueError("min_source_amount 不能为负数")


def parse_number(value: object) -> Optional[float]:
    """Parse values such as ``1,200``, ``2.4K`` and ``1.1M``."""
    text = str(value or "").strip().replace(",", "")
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*([KkMm]?)", text)
    if not match:
        return None
    multiplier = {"": 1.0, "k": 1_000.0, "m": 1_000_000.0}[
        match.group(2).lower()
    ]
    return float(match.group(1)) * multiplier


def match_user(row: Dict[str, str], criteria: UserFilter) -> List[str]:
    """Return rejection reasons. An empty list means the row is eligible."""
    reasons = []
    user_id = row.get("user_id", "").strip()
    if not user_id:
        reasons.append("missing_user_id")
    if row.get("status", "").strip().lower() != "ok":
        reasons.append("collection_not_ok")

    gender = row.get("gender", "").strip().lower()
    allowed_genders = {item.strip().lower() for item in criteria.genders if item.strip()}
    if allowed_genders and gender not in allowed_genders:
        reasons.append("gender")

    country = row.get("country", "").strip().upper()
    allowed_countries = {
        item.strip().upper() for item in criteria.countries if item.strip()
    }
    if allowed_countries and country not in allowed_countries:
        reasons.append("country")

    age = parse_number(row.get("age", ""))
    if age is None:
        reasons.append("unknown_age")
    elif age < 18:
        reasons.append("underage")
    if criteria.min_age is not None and (age is None or age < criteria.min_age):
        reasons.append("min_age")
    if criteria.max_age is not None and (age is None or age > criteria.max_age):
        reasons.append("max_age")

    amount = parse_number(row.get("source_amount", ""))
    if criteria.min_source_amount is not None and (
        amount is None or amount < criteria.min_source_amount
    ):
        reasons.append("min_source_amount")
    if criteria.face_authenticated_only and "authentication" not in row.get(
        "face_authentication", ""
    ).lower():
        reasons.append("face_authentication")
    return reasons


def load_contacted_user_ids(path: Optional[Path]) -> Set[str]:
    if path is None or not path.exists() or path.stat().st_size == 0:
        return set()
    with path.open(encoding="utf-8", newline="") as handle:
        return {
            row.get("user_id", "").strip()
            for row in csv.DictReader(handle)
            if row.get("status", "").strip().lower() == "sent"
            and row.get("user_id", "").strip()
        }


def build_outreach_plan(
    users_csv: Path,
    criteria: UserFilter,
    contacted_user_ids: Iterable[str] = (),
    limit: Optional[int] = None,
) -> List[Dict[str, str]]:
    """Select the newest row per user and return a dry-run contact plan."""
    if limit is not None and limit < 0:
        raise ValueError("limit 不能为负数")
    with users_csv.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    already_contacted = {str(item).strip() for item in contacted_user_ids}
    newest_rows = []
    seen_user_ids = set()
    for row in reversed(rows):
        user_id = row.get("user_id", "").strip()
        if user_id and user_id not in seen_user_ids:
            newest_rows.append(row)
            seen_user_ids.add(user_id)

    plan = []
    for row in newest_rows:
        user_id = row.get("user_id", "").strip()
        if user_id in already_contacted or match_user(row, criteria):
            continue
        values = {key: str(value or "") for key, value in row.items()}
        item = {field: values.get(field, "") for field in PLAN_FIELDS}
        item.update(
            {
                "user_id": user_id,
                "status": "pending_review",
                "reason": "",
            }
        )
        plan.append(item)
        if limit is not None and len(plan) >= limit:
            break
    return plan


def write_outreach_plan(path: Path, rows: Iterable[Dict[str, str]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = list(rows)
    temporary = path.with_name("{}.tmp".format(path.name))
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=PLAN_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in PLAN_FIELDS})
    temporary.replace(path)
    return len(rows)
