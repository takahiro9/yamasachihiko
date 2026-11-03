"""B7 日次ダイジェスト。重要度順に並べ、同じ地域は上位のうち最大 3 件までにする。"""
from __future__ import annotations

import json
from datetime import timedelta

from .. import config
from ..regions import OTHER_REGION
from ..timeutil import iso, jst_date, now_utc


def _pick(candidates: list[dict], size: int, region_cap: int) -> list[dict]:
    """上限で溢れた分は飛ばし、空いた枠に他地域の上位を入れる。"""
    chosen, per_region = [], {}
    for e in candidates:
        r = e["region"] or OTHER_REGION
        if len(chosen) < size and per_region.get(r, 0) < region_cap:
            chosen.append(e)
            per_region[r] = per_region.get(r, 0) + 1
    return chosen


def _event_view(conn, e) -> dict:
    reports = conn.execute(
        "SELECT r.media, r.country, r.url, COALESCE(r.title_ja, r.title) AS title FROM event_reports er"
        " JOIN reports r ON r.id=er.report_id WHERE er.event_id=? ORDER BY r.published_at LIMIT 3", (e["id"],)).fetchall()
    cont = conn.execute(
        "SELECT k.kind, p.headline FROM links k JOIN events p ON p.id=k.from_event_id"
        " WHERE k.to_event_id=? AND k.status='confirmed_by_model' ORDER BY k.confidence DESC LIMIT 1", (e["id"],)).fetchone()
    summary = e["summary"]
    if not summary:   # L3 が終わっていない出来事は代表の報道の要点で代わりに表示する
        pts = conn.execute("SELECT points_ja FROM event_reports er JOIN reports r ON r.id=er.report_id"
                           " WHERE er.event_id=? AND r.points_ja IS NOT NULL LIMIT 1", (e["id"],)).fetchone()
        summary = " ".join(json.loads(pts[0])) if pts else ""
    return {
        "id": e["id"], "headline": e["headline"], "summary": summary, "region": e["region"] or OTHER_REGION,
        "importance": e["importance"], "report_count": e["report_count"], "country_count": e["country_count"],
        "continuation": f"{cont['headline']} の{cont['kind']}" if cont else None,
        "reports": [dict(r) for r in reports],
    }


def build(conn, now=None) -> dict:
    now = now or now_utc()
    today = jst_date(now)
    prev = conn.execute("SELECT MAX(generated_at) FROM digests WHERE date < ?", (today,)).fetchone()[0]
    since = prev or iso(now - timedelta(hours=36))
    cands = [dict(r) for r in conn.execute(
        "SELECT * FROM events WHERE first_seen_at > ? ORDER BY importance DESC, report_count DESC", (since,))]
    top = [_event_view(conn, e) for e in _pick(cands, config.DIGEST_SIZE, config.DIGEST_REGION_CAP)]

    sections = []
    for t in conn.execute("SELECT t.id, v.name FROM themes t JOIN theme_versions v ON v.theme_id=t.id AND v.version=t.current_version"
                          " WHERE t.kind='basic' ORDER BY t.is_fallback, t.id"):
        ids = {e["id"] for e in conn.execute("SELECT event_id AS id FROM event_themes WHERE theme_id=?", (t["id"],))}
        items = [e for e in cands if e["id"] in ids][: config.DIGEST_PER_THEME]
        if items:
            sections.append({"theme_id": t["id"], "name": t["name"], "events": [_event_view(conn, e) for e in items]})

    spikes = [dict(r) | {"name": conn.execute(
        "SELECT v.name FROM themes t JOIN theme_versions v ON v.theme_id=t.id AND v.version=t.current_version WHERE t.id=?",
        (r["theme_id"],)).fetchone()[0]} for r in conn.execute("SELECT * FROM spikes WHERE date=? ORDER BY count DESC", (today,))]
    notices = [f"情報源 {r['source_id']} を自動停止: {r['reason']}" for r in conn.execute(
        "SELECT source_id, reason FROM source_history WHERE actor='auto' AND action='disable' AND at > ?",
        (iso(now - timedelta(hours=26)),))]
    notices += [f"段階 {r['stage']} を {'スキップ' if r['status'] == 'skipped' else '実行できず'}: {r['error']}" for r in conn.execute(
        "SELECT stage, status, error FROM stage_runs WHERE status IN ('skipped','failed') AND started_at > ? ORDER BY id",
        (iso(now - timedelta(hours=26)),))]
    return {"date": today, "generated_at": iso(now), "top": top, "sections": sections, "spikes": spikes, "notices": notices,
            "event_total": len(cands), "since": since if prev else None}


def run(conn, now=None) -> dict:
    body = build(conn, now)
    conn.execute("INSERT OR REPLACE INTO digests VALUES (?,?,?)", (body["date"], body["generated_at"], json.dumps(body, ensure_ascii=False)))
    return {"digest_date": body["date"], "top": len(body["top"]), "spikes": len(body["spikes"])}
