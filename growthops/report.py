"""Portable HTML and CSV reports. No external assets, scripts, or trackers."""

import csv
from html import escape
import json
from pathlib import Path


def percent(value):
    return "待观察 / 无分母" if value is None else f"{value:.1%}"


def money(value):
    return "—" if value is None else f"${value:.4f}"


def table(headers, rows):
    head = "".join(f"<th>{escape(str(cell))}</th>" for cell in headers)
    body = "".join("<tr>" + "".join(f"<td>{escape(str(cell))}</td>" for cell in row) + "</tr>" for row in rows)
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def render_html(report):
    label = escape(report["dataset_label"])
    cutoff = escape(report["as_of"])
    completed = report["funnel"][-1]
    cards = [("观察到的活跃用户", report["costs"]["observed_active_users"], "任一有效事件的去重用户"),
             ("漏斗完成率", percent(completed["conversion_from_visit"]), "完整走过四个步骤 / visit 用户"),
             ("精确 D7 留存", percent(report["retention"]["d7"]["rate"]), "分母只含观察窗口完整的用户"),
             ("每活跃用户成本", money(report["costs"]["per_active_user"]), "输入日志中的模型调用成本，USD")]
    card_html = "".join(f'<article class="card"><p>{escape(str(title))}</p><strong>{escape(str(value))}</strong><small>{escape(note)}</small></article>' for title, value, note in cards)
    funnel_html = ""
    for index, row in enumerate(report["funnel"]):
        rate = row["conversion_from_visit"] or 0
        funnel_html += f'<div class="stage"><div class="stage-label"><span>0{index+1} · {escape(row["event"])}</span><b>{row["users"]} 人</b></div><div class="track"><div class="bar" style="width:{rate*100:.4f}%"></div></div><small>相对首步 {percent(row["conversion_from_visit"])} · 相对前一步 {percent(row["conversion_from_previous"]) if index else "起点"}</small></div>'
    channel_html = table(["首次访问渠道", "visit", "question", "answer_helpful", "return", "完成率"],
                         [[row["channel"], row["visit"], row["question"], row["answer_helpful"], row["return"], percent(row["completion_rate"])] for row in report["channels"]])
    cohorts_html = table(["UTC 首访日", "用户", "D1 留存 / 分母", "D1", "D7 留存 / 分母", "D7", "D7 待观察"],
                         [[row["cohort_date"], row["users"], f'{row["d1_retained"] if row["d1_retained"] is not None else "—"} / {row["d1_eligible"]}', percent(row["d1_rate"]), f'{row["d7_retained"] if row["d7_retained"] is not None else "—"} / {row["d7_eligible"]}', percent(row["d7_rate"]), row["d7_censored"]] for row in report["cohorts"]])
    quality = report["quality"]
    quality_html = table(["输入行", "接收行", "拒绝行", "截止时间后排除"], [[quality.get("input_rows", 0), quality.get("accepted_rows", 0), quality.get("rejected_rows", 0), quality.get("future_rows", 0)]])
    issue_html = table(["CSV 行号", "处理", "原因"], [[row["line"], row["disposition"], row["reason"]] for row in report["issues"]]) if report["issues"] else '<p class="muted">当前输入未发现行级质量问题。</p>'
    definitions = "".join(f"<li>{escape(text)}</li>" for text in report["definitions"].values())
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>GrowthOps Lab · 运营分析报告</title>
<style>
:root{{--ink:#172d35;--muted:#64777c;--teal:#087e82;--pale:#edf6f5;--line:#dfe8e7;--paper:#fbfcf9}}*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.65 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}main{{max-width:1160px;padding:42px 28px 70px;margin:auto}}header{{border-bottom:1px solid var(--line);padding-bottom:28px;margin-bottom:28px}}.eyebrow{{font-size:12px;letter-spacing:.14em;color:var(--teal);font-weight:750}}h1{{font-size:clamp(27px,4vw,44px);line-height:1.2;margin:10px 0 15px;letter-spacing:-.03em}}header p{{max-width:760px;margin:6px 0;color:var(--muted)}}.tag{{display:inline-block;padding:4px 11px;border-radius:5px;background:#fff3d8;color:#795611;font-size:12px;font-weight:650}}.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:24px}}.card{{background:white;border:1px solid var(--line);border-radius:12px;padding:20px}}.card p{{margin:0 0 10px;color:var(--muted);font-size:13px}}.card strong{{display:block;font-size:29px;line-height:1.25;margin-bottom:12px;color:var(--teal)}}small{{font-size:12px;color:var(--muted)}}.card small{{display:block}}.grid{{display:grid;grid-template-columns:1fr 1.25fr;gap:22px}}section{{background:white;border:1px solid var(--line);border-radius:12px;margin-bottom:22px;padding:25px;min-width:0}}h2{{font-size:20px;line-height:1.3;margin:0 0 8px}}.muted{{color:var(--muted);font-size:13px;margin-top:0}}.stage{{margin:20px 0}}.stage-label{{display:flex;justify-content:space-between;gap:8px;font-size:13px}}.track{{height:10px;background:var(--pale);border-radius:5px;margin:7px 0}}.bar{{height:100%;background:var(--teal);border-radius:5px}}.scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:13px;margin-top:18px}}th{{font-weight:650;color:var(--muted);text-align:left;background:#f6f9f8}}th,td{{padding:12px 10px;border-bottom:1px solid var(--line);white-space:nowrap}}td:first-child{{white-space:normal;overflow-wrap:anywhere;min-width:90px}}ul{{padding-left:20px;color:var(--muted);font-size:13px}}li{{margin:8px 0}}footer{{color:var(--muted);font-size:12px}}.cost-summary{{padding:14px;background:var(--pale);border-radius:8px;margin-top:18px;font-size:13px}}@media(max-width:850px){{.cards{{grid-template-columns:repeat(2,1fr)}}.grid{{grid-template-columns:1fr}}main{{padding:24px 16px 45px}}section{{padding:20px}}}}@media(max-width:420px){{.cards{{grid-template-columns:1fr}}}}@media print{{body{{background:white}}main{{max-width:none;padding:0}}section,.card{{break-inside:avoid}}}}
</style></head><body><main><header><div class="eyebrow">GROWTHOPS LAB / PRODUCT OPERATIONS</div><h1>从使用行为，找到改进方向。</h1><p>AI 产品运营分析 · 用户漏斗、渠道表现、精确日留存与模型成本。</p><p><span class="tag">{label}</span></p><p>观察截止：{cutoff} · 日历口径：UTC</p></header>
<div class="cards">{card_html}</div><div class="grid"><section><h2>使用漏斗</h2><p class="muted">同一用户按时间严格递增完成，重复事件不会重复计人。</p>{funnel_html}</section><section><h2>渠道表现</h2><p class="muted">按输入文件内的首次 visit 渠道归因。可用于定位差异，不能单独证明因果。</p>{channel_html}<div class="cost-summary">模型调用 {report["costs"]["model_calls"]} 次 · 总成本 {money(report["costs"]["total"])} · 每次调用 {money(report["costs"]["per_model_call"])}</div></section></div>
<section><h2>精确日留存</h2><p class="muted">D1 / D7 指首访后第 1 / 7 个 UTC 日当天发生 question 或 return。尚未完整观察的日期显示待观察。</p>{cohorts_html}</section>
<section><h2>数据质量</h2><p class="muted">问题行被排除并留痕。缺失记录、未提供的历史和错误埋点仍需人工核实。</p>{quality_html}{issue_html}</section>
<section><h2>阅读与使用边界</h2><ul>{definitions}</ul><p class="muted">合成演示数据只用于说明计算方式。该报告不构成真实产品的增长或效果证明。</p></section>
<footer>GrowthOps Lab v0.1.0 · 离线计算 / 无外部资源 / 可复核 JSON 与 CSV · AI 辅助开发</footer></main></body></html>'''


def safe_csv(value):
    text = "" if value is None else str(value)
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")) else text


def write_outputs(report, output):
    destination = Path(output)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "report.html").write_text(render_html(report), encoding="utf-8")
    (destination / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    for name in ("funnel", "channels", "cohorts", "issues"):
        rows = report[name]
        with (destination / f"{name}.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            if rows:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows({key: safe_csv(value) for key, value in row.items()} for row in rows)
    return destination / "report.html"
