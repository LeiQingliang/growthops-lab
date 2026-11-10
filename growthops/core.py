"""CSV validation and explicitly defined, deterministic user-level metrics."""

from collections import Counter, defaultdict
import csv
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

UTC = timezone.utc
FIELDS = ("event_id", "user_id", "timestamp", "event", "channel", "cost_usd")
EVENTS = frozenset(("visit", "question", "answer_helpful", "return", "model_call"))
STAGES = ("visit", "question", "answer_helpful", "return")
RETENTION_EVENTS = frozenset(("question", "return"))


def parse_timestamp(value):
    """Require a timezone; normalize offsets before grouping calendar days."""
    stamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("timestamp requires an explicit timezone")
    return stamp.astimezone(UTC)


def ratio(numerator, denominator):
    return round(numerator / denominator, 6) if denominator else None


def load_events(path, as_of, mapping=None):
    """First valid occurrence wins for duplicate event_id; reject bad rows."""
    if as_of.tzinfo is None:
        raise ValueError("as_of requires an explicit timezone")
    as_of = as_of.astimezone(UTC)
    mapping = mapping or {}
    if set(mapping) - set(FIELDS):
        raise ValueError("mapping contains unknown canonical fields")
    events, issues, seen = [], [], set()
    quality = Counter(input_rows=0, accepted_rows=0, rejected_rows=0, future_rows=0)
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        headers = reader.fieldnames or []
        if len(set(headers)) != len(headers):
            raise ValueError("duplicate CSV header names")
        missing = [mapping.get(key, key) for key in FIELDS if mapping.get(key, key) not in headers]
        if missing:
            raise ValueError("missing CSV columns: " + ", ".join(missing))
        for line, row in enumerate(reader, 2):
            quality["input_rows"] += 1
            reason = None
            try:
                if None in row or any(row.get(mapping.get(k, k)) is None for k in FIELDS):
                    raise ValueError("malformed CSV row")
                item = {key: row[mapping.get(key, key)].strip() for key in FIELDS}
                if not item["event_id"]:
                    raise ValueError("empty event_id")
                if not item["user_id"]:
                    raise ValueError("empty user_id")
                if item["event"] not in EVENTS:
                    raise ValueError("unknown event")
                try:
                    stamp = parse_timestamp(item["timestamp"])
                except (ValueError, OverflowError):
                    raise ValueError("invalid or timezone-free timestamp") from None
                try:
                    cost = Decimal(item["cost_usd"] or "0")
                    if not cost.is_finite() or cost < 0 or cost > Decimal("1000000000"):
                        raise InvalidOperation
                except InvalidOperation:
                    raise ValueError("invalid cost_usd (finite, nonnegative, <= 1 billion required)") from None
                if item["event"] != "model_call" and cost != 0:
                    raise ValueError("cost_usd is only allowed on model_call")
                if item["event_id"] in seen:
                    raise ValueError("duplicate event_id")
                # Future data is excluded before deduplication and all metric calculations.
                if stamp > as_of:
                    quality["future_rows"] += 1
                    issues.append({"line": line, "reason": "after observation cutoff", "disposition": "excluded"})
                    continue
                seen.add(item["event_id"])
                item.update(timestamp=stamp, cost_usd=cost, channel=item["channel"] or "unknown")
                events.append(item)
                quality["accepted_rows"] += 1
            except (ValueError, TypeError) as exc:
                reason = str(exc)
            if reason:
                quality["rejected_rows"] += 1
                quality[reason] += 1
                issues.append({"line": line, "reason": reason, "disposition": "rejected"})
    events.sort(key=lambda item: (item["timestamp"], item["event_id"]))
    return events, dict(quality), issues


def analyze(events, as_of, quality=None, issues=None, dataset_label="用户导入数据"):
    """All stages are strict timestamp order; retention uses complete UTC days."""
    as_of = as_of.astimezone(UTC)
    groups = defaultdict(list)
    for event in events:
        if event["timestamp"] <= as_of:
            groups[event["user_id"]].append(event)
    for rows in groups.values():
        rows.sort(key=lambda item: (item["timestamp"], item["event_id"]))
    total = [0] * len(STAGES)
    channels = defaultdict(lambda: [0] * len(STAGES))
    cohort_users = defaultdict(list)
    for user_id, rows in groups.items():
        visits = [row for row in rows if row["event"] == "visit"]
        if not visits:
            continue
        first = visits[0]
        channel = first["channel"]
        cohort_users[first["timestamp"].date()].append((user_id, rows))
        previous, stage = None, 0
        for row in rows:
            if stage >= len(STAGES):
                break
            if row["event"] == STAGES[stage] and (previous is None or row["timestamp"] > previous):
                total[stage] += 1
                channels[channel][stage] += 1
                previous, stage = row["timestamp"], stage + 1
    funnel = [
        {"event": stage, "users": total[index], "conversion_from_visit": ratio(total[index], total[0]),
         "conversion_from_previous": ratio(total[index], total[index - 1]) if index else None}
        for index, stage in enumerate(STAGES)
    ]
    channel_rows = [
        {"channel": channel, **dict(zip(STAGES, counts)), "completion_rate": ratio(counts[-1], counts[0])}
        for channel, counts in sorted(channels.items())
    ]
    cohorts, retention_totals = [], {day: [0, 0, 0] for day in (1, 7)}
    for date, users in sorted(cohort_users.items()):
        row = {"cohort_date": date.isoformat(), "users": len(users)}
        for day in (1, 7):
            target = date + timedelta(days=day)
            mature = datetime.combine(target + timedelta(days=1), time.min, UTC) <= as_of
            retained = sum(any(event["timestamp"].date() == target and event["event"] in RETENTION_EVENTS
                               for event in events_for_user) for _, events_for_user in users) if mature else None
            eligible = len(users) if mature else 0
            censored = 0 if mature else len(users)
            row.update({f"d{day}_eligible": eligible, f"d{day}_retained": retained,
                        f"d{day}_censored": censored, f"d{day}_rate": ratio(retained or 0, eligible)})
            retention_totals[day][0] += retained or 0
            retention_totals[day][1] += eligible
            retention_totals[day][2] += censored
        cohorts.append(row)
    cost = sum((row["cost_usd"] for rows in groups.values() for row in rows if row["event"] == "model_call"), Decimal(0))
    calls = sum(row["event"] == "model_call" for rows in groups.values() for row in rows)
    active = len(groups)
    return {
        "schema_version": "1.0", "dataset_label": dataset_label,
        "as_of": as_of.isoformat(), "timezone": "UTC", "funnel": funnel, "channels": channel_rows,
        "cohorts": cohorts,
        "retention": {f"d{day}": {"retained": value[0], "eligible": value[1], "censored": value[2],
                                      "rate": ratio(value[0], value[1])} for day, value in retention_totals.items()},
        "costs": {"currency": "USD", "total": float(cost), "model_calls": calls,
                  "observed_active_users": active,
                  "per_active_user": float(cost / active) if active else None,
                  "per_model_call": float(cost / calls) if calls else None},
        "quality": quality or {}, "issues": issues or [],
        "definitions": {
            "funnel": "用户级累计漏斗，按首次 visit 渠道归因，步骤时间必须严格递增；无会话或时间窗限制。",
            "retention": "首次 visit 的 UTC 日期为 cohort；精确 D1/D7 当日 question 或 return；仅完整观察日进入分母。",
            "cost": "仅合计 model_call.cost_usd；active 为观察期内任一有效事件的去重用户，不代表业务活跃标准。",
            "scope": "仅分析输入文件截至 as_of 的记录；首次 visit 指文件内首次记录，历史截断会影响 cohort。",
        },
    }
