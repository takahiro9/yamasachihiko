"""contracts/（画面・MCP・CLI が共有するクエリと依頼の定義）のテスト。"""
from __future__ import annotations

import json
import re
import sqlite3

import pytest

from test_pipeline import add_sources, ctx
from world_events import runner, views
from world_events.contracts import change_request_kinds, parse_queries, queries, validate_change_request
from world_events.publish import d1, ops

D1_TABLES = {t for t, _, _ in d1._TABLES}


def test_every_query_runs_on_empty_db(conn):
    for q in queries().values():
        out = views.run(conn, q.name)
        assert out is None or isinstance(out, list | dict), q.name


def test_queries_only_touch_tables_published_to_d1():
    for q in queries().values():
        used = set(re.findall(r"\b(?:FROM|JOIN)\s+([a-z_]+)", q.sql)) - {"json_each"}
        assert used <= D1_TABLES, f"{q.name}: D1 に無い表 {used - D1_TABLES}"


def test_parse_queries_header():
    q = parse_queries("-- name: a.b\n-- params: x, y\n-- json: j\n-- one\nSELECT ?1 AS x, ?2 AS j;\n")["a.b"]
    assert (q.params, q.json_cols, q.one, q.sql) == (("x", "y"), ("j",), True, "SELECT ?1 AS x, ?2 AS j")


def test_views_reject_unknown_params(conn):
    with pytest.raises(ValueError):
        views.run(conn, "events.list", nope=1)


def test_views_after_daily_run(conn, settings, llm):
    add_sources(conn)
    runner.run_daily(ctx(conn, settings, llm))
    events = views.run(conn, "events.list", limit=5)
    assert events and events[0]["headline"]
    detail = views.event_detail(conn, events[0]["id"])
    assert detail["reports"] and detail["themes"]
    digest = views.run(conn, "digests.get")
    assert isinstance(digest["body"], dict) and "top" in digest["body"]
    assert views.theme_detail(conn, detail["themes"][0]["theme_id"])["events"]


def test_validate_change_request():
    assert validate_change_request("source.disable", {"id": "x", "reason": "重複", "extra": 1}) == {"id": "x", "reason": "重複"}
    with pytest.raises(ValueError):
        validate_change_request("source.disable", {"id": "x"})
    with pytest.raises(ValueError):
        validate_change_request("event.confirm_theme", {"event_id": "e", "theme_id": "t", "theme_version": "1"})
    with pytest.raises(ValueError):
        validate_change_request("nope", {})


def test_every_kind_has_an_apply_branch(conn):
    """契約に足した種類を _apply に実装し忘れると「未対応」で却下されるので、それを検出する。"""
    import inspect
    src = inspect.getsource(ops._apply)
    for k in change_request_kinds():
        assert f'"{k}"' in src, k


def test_submit_local_request_and_apply_now(conn):
    add_sources(conn)
    sid = conn.execute("SELECT id FROM sources LIMIT 1").fetchone()[0]
    r = ops.submit_change_request(conn, "source.disable", {"id": sid, "reason": "テスト"}, apply_now=True)
    assert r["status"] == "applied" and r["origin"] == "local"
    assert conn.execute("SELECT enabled FROM sources WHERE id=?", (sid,)).fetchone()[0] == 0
    assert conn.execute("SELECT actor FROM source_history WHERE source_id=? ORDER BY id DESC", (sid,)).fetchone()[0] == "cli"


def test_import_from_d1_is_idempotent_and_result_is_published(conn):
    rows = [{"id": "w1", "kind": "source.add", "created_at": "2026-11-01T00:00:00Z",
             "payload": json.dumps({"id": "w", "type": "rss", "url": "u", "media": "W", "country": "GB", "language": "en"})}]
    assert ops.import_change_requests(conn, rows) == 1
    assert ops.import_change_requests(conn, rows) == 0
    assert ops.apply_change_requests(conn) == {"applied": 1, "rejected": 0}
    sql = d1.build_sql(conn, since="2026-01-01")
    assert "INSERT OR REPLACE INTO change_requests" in sql and "'applied'" in sql


def test_parse_wrangler_json():
    out = json.dumps([{"results": [{"id": "a"}], "success": True, "meta": {}}])
    assert d1.parse_wrangler_json(out) == [{"id": "a"}]
    with pytest.raises(RuntimeError):
        d1.parse_wrangler_json(json.dumps([{"success": False}]))


def test_d1_sql_applies_on_fresh_d1_schema(conn, settings, llm):
    """publish の upsert SQL が shared の migration だけの DB（= D1）にそのまま入る。"""
    add_sources(conn)
    runner.run_daily(ctx(conn, settings, llm))
    from conftest import MIGRATIONS
    from world_events.db import migrate
    remote = sqlite3.connect(":memory:")
    remote.row_factory = sqlite3.Row
    remote.execute("PRAGMA foreign_keys = ON")   # D1 は外部キーを強制する
    migrate(remote, MIGRATIONS, scopes=("shared",))
    remote.executescript(d1.build_sql(conn))
    remote.executescript(d1.build_sql(conn))       # 2 回目（上書き）も通る
    assert remote.execute("PRAGMA foreign_key_check").fetchall() == []
    assert views.run(remote, "events.list")
