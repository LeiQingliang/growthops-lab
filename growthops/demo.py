"""Deterministic synthetic data; no real users or business outcomes."""

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
from .core import FIELDS

DEMO_AS_OF = "2026-10-16T00:00:00Z"


def make_demo(path):
    rows = []
    start = datetime(2026, 10, 1, 9, tzinfo=timezone.utc)

    def add(user, stamp, event, channel, cost=""):
        rows.append(dict(zip(FIELDS, (f"demo-{len(rows)+1:04d}", user, stamp.isoformat(), event, channel, cost))))

    for index in range(48):
        user = f"synthetic-user-{index+1:03d}"
        stamp = start + timedelta(days=index // 4)
        channel = ("community", "search", "newsletter")[index % 3]
        add(user, stamp, "visit", channel)
        if index % 5 != 0:
            add(user, stamp + timedelta(minutes=2), "question", channel)
            add(user, stamp + timedelta(minutes=3), "model_call", channel, "0.012" if index % 2 else "0.008")
            if index % 4 != 0:
                add(user, stamp + timedelta(minutes=4), "answer_helpful", channel)
            if index % 3 != 0:
                add(user, stamp + timedelta(days=1), "return", channel)
            if index % 4 == 1:
                add(user, stamp + timedelta(days=7), "question", channel)
    # Deliberate quality cases make exclusions and audit logs visible.
    rows.append(dict(rows[0]))
    rows.append(dict(zip(FIELDS, ("bad-empty-user", "", start.isoformat(), "visit", "community", ""))))
    rows.append(dict(zip(FIELDS, ("bad-cost", "synthetic-user-x", start.isoformat(), "model_call", "community", "-1"))))
    rows.append(dict(zip(FIELDS, ("bad-time", "synthetic-user-y", "not-a-time", "visit", "community", ""))))
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return destination
