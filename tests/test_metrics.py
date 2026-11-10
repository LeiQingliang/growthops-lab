import csv
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest

from growthops.core import FIELDS, analyze, load_events, parse_timestamp
from growthops.demo import DEMO_AS_OF, make_demo
from growthops.report import render_html, safe_csv, write_outputs
from growthops.__main__ import main


def event(event_id, user, timestamp, name, channel="search", cost="0"):
    return {"event_id": event_id, "user_id": user, "timestamp": parse_timestamp(timestamp),
            "event": name, "channel": channel, "cost_usd": Decimal(cost)}


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.cutoff = parse_timestamp("2026-10-10T00:00:00Z")

    def test_funnel_rejects_out_of_order_and_same_timestamp(self):
        rows = [event("1", "a", "2026-10-01T09:00:00Z", "question"),
                event("2", "a", "2026-10-01T10:00:00Z", "visit"),
                event("3", "a", "2026-10-01T10:00:00Z", "question"),
                event("4", "a", "2026-10-01T11:00:00Z", "answer_helpful"),
                event("5", "a", "2026-10-02T10:00:00Z", "return")]
        result = analyze(list(reversed(rows)), self.cutoff)
        self.assertEqual([row["users"] for row in result["funnel"]], [1, 0, 0, 0])

    def test_funnel_first_channel_and_distinct_users(self):
        rows = [event("1", "a", "2026-10-01T09:00:00Z", "visit", "community"),
                event("2", "a", "2026-10-01T10:00:00Z", "visit", "search"),
                event("3", "a", "2026-10-01T11:00:00Z", "question"),
                event("4", "a", "2026-10-01T12:00:00Z", "answer_helpful"),
                event("5", "a", "2026-10-02T10:00:00Z", "return"),
                event("6", "a", "2026-10-03T10:00:00Z", "return")]
        result = analyze(rows, self.cutoff)
        self.assertEqual([row["users"] for row in result["funnel"]], [1, 1, 1, 1])
        self.assertEqual(result["channels"][0]["channel"], "community")
        self.assertEqual(result["channels"][0]["completion_rate"], 1)

    def test_exact_retention_and_censoring(self):
        rows = [event("1", "old-a", "2026-10-01T10:00:00Z", "visit"),
                event("2", "old-b", "2026-10-01T10:00:00Z", "visit"),
                event("3", "old-a", "2026-10-08T13:00:00Z", "question"),
                event("4", "old-b", "2026-10-09T13:00:00Z", "question"),
                event("5", "new", "2026-10-05T10:00:00Z", "visit")]
        result = analyze(rows, self.cutoff)
        self.assertEqual(result["retention"]["d7"], {"retained": 1, "eligible": 2, "censored": 1, "rate": .5})
        self.assertIsNone(result["cohorts"][1]["d7_rate"])

    def test_target_day_must_be_fully_observed(self):
        rows = [event("1", "a", "2026-10-01T10:00:00Z", "visit"),
                event("2", "a", "2026-10-02T10:00:00Z", "return")]
        partial = analyze(rows, parse_timestamp("2026-10-02T23:59:59Z"))
        full = analyze(rows, parse_timestamp("2026-10-03T00:00:00Z"))
        self.assertIsNone(partial["retention"]["d1"]["rate"])
        self.assertEqual(full["retention"]["d1"]["rate"], 1)

    def test_timezone_normalizes_to_utc_calendar_day(self):
        rows = [event("1", "a", "2026-10-02T01:00:00+08:00", "visit"),
                event("2", "a", "2026-10-03T01:00:00+08:00", "question")]
        result = analyze(rows, self.cutoff)
        self.assertEqual(result["cohorts"][0]["cohort_date"], "2026-10-01")
        self.assertEqual(result["retention"]["d1"]["rate"], 1)
        with self.assertRaises(ValueError):
            parse_timestamp("2026-10-01T01:00:00")

    def test_cost_denominators_and_no_first_visit(self):
        rows = [event("1", "a", "2026-10-01T09:00:00Z", "visit"),
                event("2", "b", "2026-10-01T09:00:00Z", "model_call", cost="0.1"),
                event("3", "b", "2026-10-01T10:00:00Z", "model_call", cost="0.2")]
        result = analyze(rows, self.cutoff)
        self.assertEqual(result["costs"]["total"], .3)
        self.assertEqual(result["costs"]["per_active_user"], .15)
        self.assertEqual(result["costs"]["per_model_call"], .15)
        self.assertEqual(result["funnel"][0]["users"], 1)
        self.assertEqual(result["cohorts"][0]["users"], 1)

    def test_empty_has_null_denominators(self):
        result = analyze([], self.cutoff)
        self.assertIsNone(result["funnel"][-1]["conversion_from_visit"])
        self.assertIsNone(result["costs"]["per_active_user"])
        self.assertIsNone(result["retention"]["d7"]["rate"])
        self.assertIn("待观察", render_html(result))

    def test_html_escapes_channel_and_label(self):
        rows = [event("1", "a", "2026-10-01T09:00:00Z", "visit", '<img src=x onerror="alert(1)">')]
        html = render_html(analyze(rows, self.cutoff, dataset_label="<script>alert(1)</script>"))
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&lt;img", html)
        self.assertEqual(safe_csv("=SUM(1,1)"), "'=SUM(1,1)")
        self.assertEqual(safe_csv("  =SUM(1,1)"), "'  =SUM(1,1)")
        self.assertEqual(safe_csv("\n@SUM(1,1)"), "'\n@SUM(1,1)")


class InputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "events.csv"
        self.cutoff = parse_timestamp("2026-10-10T00:00:00Z")

    def tearDown(self):
        self.temp.cleanup()

    def write(self, rows, headers=FIELDS):
        with self.path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(headers)
            writer.writerows(rows)

    def test_validation_exclusions_and_first_valid_duplicate(self):
        self.write([
            ["same", "a", "2026-10-01T09:00:00Z", "visit", "search", ""],
            ["same", "a", "2026-10-01T09:00:00Z", "visit", "search", ""],
            ["e2", "", "2026-10-01T09:00:00Z", "visit", "search", ""],
            ["e3", "a", "2026-10-01T09:00:00", "visit", "search", ""],
            ["e4", "a", "bad", "visit", "search", ""],
            ["e5", "a", "2026-10-01T09:00:00Z", "model_call", "search", "-1"],
            ["e6", "a", "2026-10-01T09:00:00Z", "model_call", "search", "NaN"],
            ["e7", "a", "2026-10-11T09:00:00Z", "visit", "search", ""],
            ["e8", "a", "2026-10-01T09:00:00Z", "visit", "search", "3"],
        ])
        events, quality, issues = load_events(self.path, self.cutoff)
        self.assertEqual(len(events), 1)
        self.assertEqual(quality["rejected_rows"], 7)
        self.assertEqual(quality["future_rows"], 1)
        self.assertEqual(len(issues), 8)

    def test_missing_and_duplicate_headers_fail(self):
        self.write([], headers=("user_id",))
        with self.assertRaisesRegex(ValueError, "missing"):
            load_events(self.path, self.cutoff)
        self.write([], headers=FIELDS + ("user_id",))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            load_events(self.path, self.cutoff)

    def test_posthog_style_column_mapping(self):
        self.write([["uuid-1", "anon-1", "2026-10-01T09:00:00Z", "visit", "search", ""]],
                   headers=("uuid", "distinct_id", "timestamp", "event", "channel", "cost_usd"))
        events, quality, _ = load_events(self.path, self.cutoff, {"event_id": "uuid", "user_id": "distinct_id"})
        self.assertEqual(events[0]["user_id"], "anon-1")
        self.assertEqual(quality["accepted_rows"], 1)

    def test_demo_is_reproducible_and_cli_outputs_parse(self):
        make_demo(self.path)
        original = self.path.read_bytes()
        make_demo(self.path)
        self.assertEqual(self.path.read_bytes(), original)
        output = Path(self.temp.name) / "report"
        self.assertEqual(main(["demo", "--output", str(output)]), 0)
        report = json.loads((output / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["costs"]["observed_active_users"], 48)
        # Complete fixture users are indices 1,2,7,11,13,14,17,19,22,23,
        # 26,29,31,34,37,38,41,43,46,47 (question + helpful + next-day return).
        self.assertEqual([row["users"] for row in report["funnel"]], [48, 38, 29, 20])
        self.assertEqual(report["quality"]["rejected_rows"], 4)
        self.assertTrue((output / "cohorts.csv").exists())
        self.assertEqual(report["retention"]["d7"]["eligible"], 32)

    def test_invalid_mapping_returns_actionable_error(self):
        mapping = Path(self.temp.name) / "mapping.json"
        mapping.write_text("[]", encoding="utf-8")
        make_demo(self.path)
        self.assertEqual(main(["analyze", "--input", str(self.path), "--as-of", DEMO_AS_OF,
                               "--mapping", str(mapping), "--output", str(Path(self.temp.name) / "out")]), 2)


if __name__ == "__main__":
    unittest.main()
