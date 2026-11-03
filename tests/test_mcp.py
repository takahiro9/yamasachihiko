"""MCP サーバー（news mcp）のツール。DB は一時ファイルに向ける。"""
from __future__ import annotations

import anyio
import pytest

pytest.importorskip("mcp")

from test_briefs import add_reports, make_brief  # noqa: E402
from world_events import mcp_server as m  # noqa: E402
from world_events.db import connect, migrate  # noqa: E402

from conftest import MIGRATIONS  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "data" / "news.db"
    monkeypatch.setenv("NEWS_DB_PATH", str(path))
    monkeypatch.setenv("NEWS_DIST_DIR", str(tmp_path))
    c = connect(path)
    migrate(c, MIGRATIONS)
    add_reports(c)
    c.execute("INSERT INTO sources (id,type,url,media,country,language,created_at,updated_at)"
              " VALUES ('s1','rss','https://x/rss','M','US','en','2026-11-01T00:00:00Z','2026-11-01T00:00:00Z')")
    return c


def test_tools_are_registered():
    names = {t.name for t in anyio.run(m.server.list_tools)}
    assert {"status", "view", "get_brief", "search_events", "get_event", "collect_start", "collect_status",
            "brief_export", "brief_index", "brief_finalize", "themes_aggregate", "import_dist",
            "submit_request", "pull_requests", "publish"} <= names


def test_read_tools_match_views(db, tmp_path):
    make_brief(tmp_path)
    assert m.import_dist()[0]["label"] == "2026-11-02"
    st = m.status()
    assert st["overview"]["source_total"] == 1 and st["reports"]["reports"] == 3 and st["briefs"][0]["label"] == "2026-11-02"
    assert m.get_brief()["top"][0]["id"] == "E0001"
    assert [e["id"] for e in m.search_events(category="スポーツ")] == ["E0002"]
    assert m.get_event("2026-11-02", "E0001")["reports"]
    assert m.view("sources.list", {"enabled": 1})[0]["id"] == "s1"
    assert any(q["name"] == "brief_events.list" for q in m.list_queries())


def test_label_is_validated(db):
    with pytest.raises(ValueError):
        m.get_brief("../../etc")
    with pytest.raises(ValueError):
        m.brief_index("2026-11-02; rm -rf /")


def test_import_dist_rejects_outside(db):
    with pytest.raises(ValueError):
        m.import_dist("/etc")


def test_submit_request_apply(db):
    r = m.submit_request("source.disable", {"id": "s1", "reason": "テスト"}, apply=True)
    assert r["status"] == "applied"
    assert m.view("sources.list", {"enabled": 0})[0]["id"] == "s1"


def test_collect_status_without_runs(db):
    assert m.collect_status() == {"running": False, "last_run": None}


def test_publish_none_writes_sql(db, tmp_path):
    make_brief(tmp_path)
    out = m.publish("none")
    assert out["applied"] == "none" and "brief_events" in open(out["d1_sql"], encoding="utf-8").read()
