"""B5 定点記録（月次）。LLM を使わない部分（報道量・分布・主な出来事・論調の分布）だけを記録する。
変化の要約とフェーズの判定は、Claude を使うステップ 4 で足す（列は用意済み）。"""
from __future__ import annotations

import json
from collections import Counter

from ..regions import region_of

TOP_EVENTS = 5


def previous_month(month: str) -> str:
    y, m = map(int, month.split("-"))
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def run(conn, month: str) -> dict:
    n = 0
    themes = conn.execute("SELECT id, current_version FROM themes").fetchall()
    conn.execute("BEGIN")
    for t in themes:
        rows = conn.execute(
            "SELECT r.id, r.country, r.language, e.id AS event_id, e.headline, e.importance FROM event_themes et"
            " JOIN event_reports er ON er.event_id=et.event_id JOIN reports r ON r.id=er.report_id JOIN events e ON e.id=et.event_id"
            " WHERE et.theme_id=? AND strftime('%Y-%m', r.fetched_at, '+9 hours')=?", (t["id"], month)).fetchall()
        if not rows:
            continue
        events = {r["event_id"]: r for r in rows}
        top = sorted(events.values(), key=lambda r: r["importance"], reverse=True)[:TOP_EVENTS]
        dist = {
            "country": Counter(r["country"] for r in rows),
            "region": Counter(region_of(r["country"]) for r in rows),
            "language": Counter(r["language"] for r in rows),
        }
        stance = None
        stance_rows = conn.execute(
            "SELECT s.stance, r.country, r.language FROM report_stances s JOIN reports r ON r.id=s.report_id"
            " WHERE s.theme_id=? AND strftime('%Y-%m', r.fetched_at, '+9 hours')=?", (t["id"], month)).fetchall()
        if stance_rows:
            stance = {"all": Counter(r["stance"] for r in stance_rows),
                      "by_country": {c: Counter(r["stance"] for r in stance_rows if r["country"] == c)
                                     for c in {r["country"] for r in stance_rows}},
                      "by_language": {c: Counter(r["stance"] for r in stance_rows if r["language"] == c)
                                      for c in {r["language"] for r in stance_rows}}}
        conn.execute(
            "INSERT OR REPLACE INTO snapshots (theme_id, month, theme_version, report_count, event_count, distribution, top_events,"
            " stance_distribution) VALUES (?,?,?,?,?,?,?,?)",
            (t["id"], month, t["current_version"], len(rows), len(events),
             json.dumps({k: dict(v) for k, v in dist.items()}, ensure_ascii=False),
             json.dumps([{"id": r["event_id"], "headline": r["headline"]} for r in top], ensure_ascii=False),
             json.dumps(stance, ensure_ascii=False) if stance else None))
        n += 1
    conn.execute("COMMIT")
    return {"month": month, "snapshots": n}
