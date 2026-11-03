import sqlite3

import pytest
from pydantic import ValidationError

from world_events.admin import sources as s
from world_events.admin import themes as t


def rss(**kw):
    return s.SourceIn(**{"id": "nhk", "type": "rss", "url": "https://x/rss", "media": "NHK", "country": "jp", "language": "JA", **kw})


def test_add_normalizes_and_records_history(conn):
    row = s.add_source(conn, rss())
    assert row["country"] == "JP" and row["language"] == "ja"
    h = s.history(conn, "nhk")
    assert [x["action"] for x in h] == ["add"] and h[0]["actor"] == "cli"


def test_interval_below_one_day_rejected_at_input_and_db(conn):
    with pytest.raises(ValidationError):
        rss(interval_days=0)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO sources (id,type,url,media,country,language,interval_days,created_at,updated_at)"
                     " VALUES ('a','rss','u','m','JP','ja',0,'x','x')")


def test_media_country_language_required(conn):
    with pytest.raises(ValidationError):
        rss(media="")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO sources (id,type,url,media,country,language,created_at,updated_at)"
                     " VALUES ('a','rss','u','','JP','ja','x','x')")


def test_interval_can_only_be_extended(conn):
    s.add_source(conn, rss(interval_days=2))
    with pytest.raises(ValueError):
        s.update_source(conn, "nhk", interval_days=1)
    assert s.update_source(conn, "nhk", interval_days=7)["interval_days"] == 7


def test_disable_enable_history_and_failures_reset(conn):
    s.add_source(conn, rss())
    conn.execute("UPDATE source_state SET consecutive_failures=3 WHERE source_id='nhk'")
    s.set_enabled(conn, "nhk", False, actor="auto", reason="失敗")
    s.set_enabled(conn, "nhk", True)
    assert conn.execute("SELECT consecutive_failures FROM source_state").fetchone()[0] == 0
    assert [(x["actor"], x["action"]) for x in s.history(conn, "nhk")] == [("cli", "add"), ("auto", "disable"), ("cli", "enable")]


def test_gdelt_and_html_list_validation():
    with pytest.raises(ValidationError):
        s.SourceIn(id="g", type="gdelt_query", media="GDELT", country="ZZ", language="en")
    with pytest.raises(ValidationError):
        s.SourceIn(id="h", type="html_list", url="https://x", media="m", country="JP", language="ja")


def test_import_export_roundtrip_continues_after_error(conn):
    rows = '[{"id":"a","type":"rss","url":"u","media":"A","country":"US","language":"en"},{"id":"b","type":"rss"}]'
    added, errors = s.import_sources(conn, rows, "json")
    assert added == 1 and len(errors) == 1
    assert '"id": "a"' in s.export_sources(conn, "json")


def test_seed_has_13_basic_themes_plus_fallback(conn):
    ths = t.list_themes(conn)
    assert len(ths) == 14 and sum(x["is_fallback"] for x in ths) == 1


def test_issue_theme_requires_axis_phases_and_basic_parent(conn):
    with pytest.raises(ValidationError):
        t.ThemeIn(id="x", kind="issue", parent_id="tech-ai", name="n", scope="s")
    with pytest.raises(ValidationError):
        t.ThemeIn(id="x", kind="basic", name="n", scope="s", phases=["a"])
    with pytest.raises(ValueError):
        t.add_theme(conn, t.ThemeIn(id="x", kind="issue", parent_id="nope", name="n", scope="s", stance_axis=["a"], phases=["b"]))


def test_theme_update_creates_new_version(conn):
    t.add_theme(conn, t.ThemeIn(id="genai", kind="issue", parent_id="tech-ai", name="生成AI", scope="著作権",
                                stance_axis=t.split_csv("推進,慎重,反対"), phases=t.split_csv("黎明,拡大")))
    new = t.update_theme(conn, "genai", scope="著作権と規制", reason="広げた")
    assert new["version"] == 2
    assert t.get_theme(conn, "genai", 1)["scope"] == "著作権"
    assert len(t.theme_history(conn, "genai")) == 2
    assert t.update_theme(conn, "genai", scope="著作権と規制")["version"] == 2   # 変更なしなら版は増えない
