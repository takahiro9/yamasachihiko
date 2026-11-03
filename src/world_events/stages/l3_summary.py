"""L3 出来事の見出し・要約と重要度（S6）。"""
from __future__ import annotations

import json
import math
from collections import defaultdict

from .. import config
from ..llm import prompts
from ..llm.client import LLM, LLMError
from ..llm.schemas import L3Out
from ..llm.verify import l3_is_grounded
from ..timeutil import now_iso
from .common import report_text


def pick_diverse(reports: list, k: int = config.L3_MAX_REPORTS) -> list:
    """媒体・国が偏らないように、国ごとに順番に 1 本ずつ選ぶ。"""
    by_country: dict[str, list] = defaultdict(list)
    seen_media = set()
    for r in reports:
        by_country[r["country"]].append(r)
    picked = []
    while len(picked) < k and any(by_country.values()):
        for c in list(by_country):
            while by_country[c]:
                r = by_country[c].pop(0)
                if r["media"] not in seen_media:
                    seen_media.add(r["media"])
                    picked.append(r)
                    break
            if len(picked) >= k:
                break
    return picked


def needs_summary(e) -> bool:
    if e["confirmed"] or e["report_count"] < 2:
        return False
    if e["summary_status"] in ("pending", "fallback"):
        return True
    # 作り直しの条件: 報道が 2 倍になった / 新しい国の報道が加わった
    return e["report_count"] >= 2 * e["summary_report_count"] or e["country_count"] > e["summary_country_count"]


def run(conn, llm: LLM) -> dict:
    stats = {"summarized": 0, "fallback": 0, "single": 0}
    now = now_iso()
    # 報道 1 件の出来事は L1 の見出しをそのまま使う
    for e in conn.execute("SELECT id FROM events WHERE report_count=1 AND confirmed=0").fetchall():
        r = conn.execute("SELECT r.* FROM event_reports er JOIN reports r ON r.id=er.report_id WHERE er.event_id=?", (e["id"],)).fetchone()
        conn.execute("UPDATE events SET headline=?, summary_status='single' WHERE id=?", (r["title_ja"] or r["title"], e["id"]))
        stats["single"] += 1

    for e in conn.execute("SELECT * FROM events WHERE report_count>=2").fetchall():
        if not needs_summary(e):
            continue
        reports = conn.execute(
            "SELECT r.* FROM event_reports er JOIN reports r ON r.id=er.report_id WHERE er.event_id=? ORDER BY r.published_at", (e["id"],)).fetchall()
        picked = pick_diverse(reports)
        material = "\n\n".join(f"記事{i}（{r['media']}／{r['country']}）\n{report_text(r)}\n抜粋: {r['excerpt'][:300]}" for i, r in enumerate(picked))
        out = None
        for _ in range(2):   # 作り話（入力にない数値）なら 1 回だけ再実行
            try:
                cand = llm.chat_json("l3", prompts.L3_SYSTEM, material, L3Out)
            except LLMError as err:
                if type(err) is not LLMError:
                    raise
                continue
            if l3_is_grounded(cand, material):
                out = cand
                break
        if out:
            conn.execute(
                "UPDATE events SET headline=?, summary=?, occurred_at=COALESCE(?, occurred_at), place=?, actors=?,"
                " summary_status='done', summary_report_count=?, summary_country_count=?, updated_at=? WHERE id=?",
                (out.headline, out.summary, out.occurred_at, out.place, json.dumps(out.actors, ensure_ascii=False),
                 e["report_count"], e["country_count"], now, e["id"]))
            stats["summarized"] += 1
        else:
            top = picked[0]
            conn.execute("UPDATE events SET headline=?, summary_status='fallback', updated_at=? WHERE id=?",
                         (top["title_ja"] or top["title"], now, e["id"]))
            stats["fallback"] += 1
    return stats


def update_importance(conn, since: str | None = None) -> int:
    """重要度 = 報道の多さ × 報道の多様さ（媒体・国・言語の数） × つながりの多さ ＋ 争点テーマへの所属。
    最初は固定の式で始め、使いながら調整する。"""
    q = ("SELECT e.id, e.report_count, COUNT(DISTINCT r.media) m, COUNT(DISTINCT r.country) c, COUNT(DISTINCT r.language) l,"
         " (SELECT COUNT(*) FROM links k WHERE k.status <> 'rejected' AND (k.from_event_id=e.id OR k.to_event_id=e.id)) links,"
         " (SELECT COUNT(*) FROM event_themes et JOIN themes t ON t.id=et.theme_id WHERE et.event_id=e.id AND t.kind='issue') issue"
         " FROM events e JOIN event_reports er ON er.event_id=e.id JOIN reports r ON r.id=er.report_id"
         + (" WHERE e.updated_at >= ?" if since else "") + " GROUP BY e.id")
    n = 0
    for r in conn.execute(q, (since,) if since else ()).fetchall():
        score = (math.log1p(r["report_count"]) * (1 + math.log1p(r["m"] + r["c"] + r["l"]))
                 * (1 + 0.5 * r["links"]) + (config.IMPORTANCE_ISSUE_BONUS if r["issue"] else 0))
        conn.execute("UPDATE events SET importance=? WHERE id=?", (round(score, 4), r["id"]))
        n += 1
    return n
