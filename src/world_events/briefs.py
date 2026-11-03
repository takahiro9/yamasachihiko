"""簡易ブリーフ（dist/briefs/<ラベル>/）とテーマ整理（dist/themes/<ラベル>/）をローカルの DB に取り込む。

正本はファイル（スキルが書く）。ここで briefs / brief_* / theme_reviews / docs に写し、
閲覧 UI（D1 経由）・MCP・CLI が contracts/queries.sql の同じクエリで読めるようにする。
取り込みは何度やっても同じ結果になる（そのラベルの行を消して入れ直す）。"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .timeutil import now_iso

DOC_CHUNK = 30_000   # 文字。日本語は 1 文字 3 バイトなので 1 文 ~90KB（D1 の上限 100KB 未満）
_REF = re.compile(r"^(?P<label>[\d_-]+)/(?P<id>E\d+)$")


def _load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def _put_doc(conn, scope: str, label: str, name: str, text: str | None) -> None:
    conn.execute("DELETE FROM docs WHERE scope=? AND label=? AND name=?", (scope, label, name))
    if not text:
        return
    for seq, i in enumerate(range(0, len(text), DOC_CHUNK)):
        conn.execute("INSERT INTO docs VALUES (?,?,?,?,?)", (scope, label, name, seq, text[i:i + DOC_CHUNK]))


def get_doc(conn, scope: str, label: str, name: str) -> str | None:
    rows = conn.execute("SELECT body FROM docs WHERE scope=? AND label=? AND name=? ORDER BY seq", (scope, label, name)).fetchall()
    return "".join(r[0] for r in rows) if rows else None


def _ref(label: str, x: str) -> tuple[str, str]:
    m = _REF.match(x)
    return (m["label"], m["id"]) if m else (label, x)


def _j(v) -> str | None:
    return None if v is None else json.dumps(v, ensure_ascii=False)


def import_brief(conn, d: Path) -> dict:
    d = Path(d)
    manifest = _load(d / "manifest.json")
    label = manifest.get("label") or d.name
    ev_path = d / "events.json"
    data = _load(ev_path) if ev_path.exists() else {"events": []}
    links = _load(d / "links.json")["links"] if (d / "links.json").exists() else []
    points = {}
    for p in sorted((d / "events").glob("*.json")) if (d / "events").exists() else []:
        pts = (_load(p).get("points") or "").strip()
        if pts:
            points[p.stem] = pts

    for t in ("brief_links", "brief_event_reports", "brief_events"):
        conn.execute(f"DELETE FROM {t} WHERE label=?", (label,))
    conn.execute("INSERT OR REPLACE INTO briefs VALUES (?,?,?,?,?,?,?,?)",
                 (label, data.get("since") or manifest["since"], data.get("until") or manifest["until"],
                  manifest.get("total", 0), len(data["events"]), len(links), _j(points) if points else None, now_iso()))
    for e in data["events"]:
        cats = e.get("categories") or ["その他"]
        conn.execute(
            "INSERT INTO brief_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (label, e["id"], e["title"], e.get("summary", ""), e.get("when") or None, e.get("where") or None,
             e.get("about") or None, _j(e.get("who") or []), cats[0], _j(cats), e.get("region") or "その他",
             _j(e.get("regions") or {}), e.get("n_reports", len(e.get("report_ids", []))), _j(e.get("media", [])),
             _j(e.get("countries", [])), _j(e.get("languages", [])), e.get("importance", 0),
             e.get("first_seen"), e.get("last_seen")))
        rids = e.get("report_ids", [])
        order = {r["id"]: i for i, r in enumerate(conn.execute(
            f"SELECT id FROM reports WHERE id IN ({','.join('?' * len(rids))})"
            " ORDER BY COALESCE(published_at, fetched_at)", rids))} if rids else {}
        for rid in rids:
            conn.execute("INSERT OR IGNORE INTO brief_event_reports VALUES (?,?,?,?)",
                         (label, e["id"], rid, order.get(rid, len(order) + rids.index(rid))))
    for seq, ln in enumerate(links):
        fl, fi = _ref(label, ln["from"])
        tl, ti = _ref(label, ln["to"])
        conn.execute("INSERT INTO brief_links VALUES (?,?,?,?,?,?,?,?,?)",
                     (label, seq, fl, fi, tl, ti, ln["type"], ln.get("reason", ""), float(ln.get("confidence", 0.5))))
    for name in ("overview", "brief"):
        p = d / f"{name}.md"
        _put_doc(conn, "brief", label, name, p.read_text(encoding="utf-8") if p.exists() else None)
    return {"label": label, "events": len(data["events"]), "links": len(links), "legacy": not ev_path.exists()}


def import_theme_review(conn, d: Path) -> dict:
    d = Path(d)
    agg = _load(d / "aggregate.json")
    label = agg.get("label") or d.name
    themes = [{k: t[k] for k in ("category", "events", "primary_events", "reports", "regions") if k in t}
              | {"top": [x["ref"] for x in t.get("top", [])]} for t in agg.get("themes", [])]
    conn.execute("INSERT OR REPLACE INTO theme_reviews VALUES (?,?,?,?,?,?,?,?)",
                 (label, agg["since"], agg["until"], _j(agg.get("briefs", [])), agg.get("events", 0), _j(themes),
                  _j(agg.get("spikes", [])), now_iso()))
    p = d / "themes.md"
    _put_doc(conn, "themes", label, "themes", p.read_text(encoding="utf-8") if p.exists() else None)
    return {"label": label, "themes": len(themes), "has_review": p.exists()}


def import_all(conn, dist: Path) -> list[dict]:
    """dist/briefs/*/ と dist/themes/*/ をすべて取り込む。manifest / aggregate の無いものは飛ばす。"""
    out = [import_brief(conn, p.parent) for p in sorted((Path(dist) / "briefs").glob("*/manifest.json"))]
    out += [import_theme_review(conn, p.parent) for p in sorted((Path(dist) / "themes").glob("*/aggregate.json"))]
    return out
