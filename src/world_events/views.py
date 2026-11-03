"""読み取り用のビュー。contracts/queries.sql の名前付きクエリをローカルの正本（SQLite）に流す。
閲覧 UI（cloud/worker）は同じクエリを D1 に流すので、MCP・CLI と画面で見えるものが一致する。"""
from __future__ import annotations

import json
import sqlite3

from .contracts import Query, queries


def _decode(q: Query, row: sqlite3.Row) -> dict:
    d = dict(row)
    for c in q.json_cols:
        if isinstance(d.get(c), str):
            try:
                d[c] = json.loads(d[c])
            except json.JSONDecodeError:
                pass
    return d


def run(conn: sqlite3.Connection, name: str, /, **params):
    q = queries().get(name)
    if not q:
        raise KeyError(f"クエリが無い: {name}（{', '.join(queries())}）")
    unknown = set(params) - set(q.params)
    if unknown:
        raise ValueError(f"{name} に無い引数: {', '.join(sorted(unknown))}（使えるもの: {', '.join(q.params) or 'なし'}）")
    rows = conn.execute(q.sql, [params.get(p) for p in q.params]).fetchall()
    if q.one:
        return _decode(q, rows[0]) if rows else None
    return [_decode(q, r) for r in rows]


def event_detail(conn: sqlite3.Connection, event_id: str) -> dict | None:
    """出来事 1 件の詳細（画面の出来事ページ・MCP の get_event と同じ形）。"""
    e = run(conn, "events.get", id=event_id)
    if not e:
        return None
    return e | {"reports": run(conn, "events.reports", id=event_id), "themes": run(conn, "events.themes", id=event_id),
                "links": run(conn, "events.links", id=event_id)}


def theme_detail(conn: sqlite3.Connection, theme_id: str) -> dict | None:
    t = run(conn, "themes.get", id=theme_id)
    if not t:
        return None
    return t | {"versions": run(conn, "themes.versions", id=theme_id), "snapshots": run(conn, "themes.snapshots", id=theme_id),
                "spikes": run(conn, "spikes.recent", theme=theme_id), "events": run(conn, "events.list", theme=theme_id, limit=30)}


def brief_detail(conn: sqlite3.Connection, label: str | None = None) -> dict | None:
    """ブリーフ 1 本（画面のブリーフページ・MCP の get_brief と同じ形）。label を省くと最新。"""
    b = run(conn, "briefs.get", label=label)
    if not b:
        return None
    lb = b["label"]
    doc = lambda name: (run(conn, "docs.get", scope="brief", label=lb, name=name) or {}).get("body")  # noqa: E731
    return b | {"overview": doc("overview"), "brief_md": None if b["event_count"] else doc("brief"),
                "top": run(conn, "brief_events.list", label=lb, limit=30), "links": run(conn, "brief_links.list", label=lb),
                "facets": run(conn, "brief_events.facets", label=lb)}


def brief_event_detail(conn: sqlite3.Connection, label: str, event_id: str) -> dict | None:
    e = run(conn, "brief_events.get", label=label, id=event_id)
    if not e:
        return None
    return e | {"reports": run(conn, "brief_events.reports", label=label, id=event_id),
                "links": run(conn, "brief_events.links", label=label, id=event_id)}


def theme_review_detail(conn: sqlite3.Connection, label: str | None = None) -> dict | None:
    r = run(conn, "theme_reviews.get", label=label)
    if not r:
        return None
    return r | {"review": (run(conn, "docs.get", scope="themes", label=r["label"], name="themes") or {}).get("body")}
