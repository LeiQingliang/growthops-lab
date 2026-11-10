"""Run `python -m growthops demo` without installing third-party packages."""

import argparse
import csv
import json
from pathlib import Path
import sys
from .core import analyze, load_events, parse_timestamp
from .demo import DEMO_AS_OF, make_demo
from .report import write_outputs


def main(argv=None):
    parser = argparse.ArgumentParser(description="GrowthOps Lab: 离线 AI 产品运营事件分析")
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="生成合成演示数据与报告")
    demo.add_argument("--output", default="artifacts/demo", help="输出目录")
    real = sub.add_parser("analyze", help="分析已导出的 CSV 事件")
    real.add_argument("--input", required=True, help="输入 CSV")
    real.add_argument("--as-of", required=True, help="带时区的观察截止时间，例如 2026-10-16T00:00:00Z")
    real.add_argument("--output", default="artifacts/analysis", help="输出目录")
    real.add_argument("--mapping", help="JSON 文件：标准字段名到 CSV 原始列名的映射")
    real.add_argument("--label", default="用户导入数据（来源由导入者核实）", help="显示在报告上的数据标签")
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            source = make_demo(Path(args.output) / "synthetic-events.csv")
            cutoff, label, mapping = parse_timestamp(DEMO_AS_OF), "合成演示数据 · 非真实业务业绩", None
        else:
            source, cutoff, label, mapping = Path(args.input), parse_timestamp(args.as_of), args.label, None
            if args.mapping:
                mapping = json.loads(Path(args.mapping).read_text(encoding="utf-8-sig"))
                if not isinstance(mapping, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in mapping.items()):
                    raise ValueError("mapping must be a JSON object with string keys and values")
        events, quality, issues = load_events(source, cutoff, mapping)
        result = analyze(events, cutoff, quality, issues, label)
        report = write_outputs(result, args.output)
        print(f"Report: {report.resolve()}")
        print(f"Accepted {quality['accepted_rows']} / {quality['input_rows']} rows; rejected {quality['rejected_rows']}; after cutoff {quality['future_rows']}.")
        return 0
    except (OSError, ValueError, OverflowError, csv.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
