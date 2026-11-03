"""簡易ブリーフ・テーマ整理の取り込みと、D1 への写し。"""
from __future__ import annotations

import json
import sqlite3

from conftest import MIGRATIONS
from world_events import briefs, views
from world_events.db import migrate
from world_events.publish import d1

T = "2026-11-02T00:00:00Z"


def add_reports(conn, n=3):
    for i in range(n):
        conn.execute("INSERT INTO reports (id,url,content_hash,media,country,language,title,fetched_at,published_at)"
                     " VALUES (?,?,?,?,?,?,?,?,?)", (f"r{i}", f"https://n/{i}", f"h{i}", f"M{i}", "US", "en", f"t{i}", T,
                                                     f"2026-11-02T0{i}:00:00Z"))


def make_brief(tmp_path, label="2026-11-02", events=None, links=None, overview="## 全体の要点\n- a"):
    d = tmp_path / "briefs" / label
    (d / "events").mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps({"since": "2026-11-01T15:00:00Z", "until": "2026-11-02T15:00:00Z", "total": 3}))
    events = events if events is not None else [
        {"id": "E0001", "title": "出来事1", "summary": "要約", "categories": ["経済・市場", "政治・選挙"], "region": "北米",
         "regions": {"北米": 2}, "report_ids": ["r1", "r0"], "n_reports": 2, "media": ["M0", "M1"], "countries": ["US"],
         "languages": ["en"], "importance": 3.0, "who": ["A"], "when": "", "where": "米国"},
        {"id": "E0002", "title": "出来事2", "summary": "", "categories": ["スポーツ"], "region": "北米", "report_ids": ["r2"],
         "n_reports": 1, "importance": 1.0},
    ]
    (d / "events.json").write_text(json.dumps({"label": label, "since": "2026-11-01T15:00:00Z", "until": "2026-11-02T15:00:00Z",
                                               "events": events}, ensure_ascii=False))
    links = links if links is not None else [
        {"from": "E0001", "to": "E0002", "type": "反応", "reason": "r", "confidence": 0.7},
        {"from": "2026-11-01/E0009", "to": "E0001", "type": "続報", "reason": "前の", "confidence": 0.6},
    ]
    (d / "links.json").write_text(json.dumps({"label": label, "links": links}, ensure_ascii=False))
    (d / "events" / "北米.json").write_text(json.dumps({"points": "北米の要点", "events": []}, ensure_ascii=False))
    (d / "overview.md").write_text(overview, encoding="utf-8")
    return d


def test_import_brief_and_views(conn, tmp_path):
    add_reports(conn)
    out = briefs.import_brief(conn, make_brief(tmp_path))
    assert out == {"label": "2026-11-02", "events": 2, "links": 2, "legacy": False}
    b = views.brief_detail(conn)
    assert b["overview"].startswith("## 全体の要点") and b["region_points"] == {"北米": "北米の要点"}
    assert [e["id"] for e in b["top"]] == ["E0001", "E0002"] and b["top"][0]["link_count"] == 2
    assert {(f["facet"], f["value"]) for f in b["facets"]} >= {("category", "政治・選挙"), ("region", "北米")}
    e = views.brief_event_detail(conn, "2026-11-02", "E0001")
    assert [r["id"] for r in e["reports"]] == ["r0", "r1"]      # 報じた時刻順
    assert {(x["direction"], x["other_label"], x["other_id"]) for x in e["links"]} == {
        ("out", "2026-11-02", "E0002"), ("in", "2026-11-01", "E0009")}
    assert views.run(conn, "brief_events.list", category="政治・選挙")[0]["id"] == "E0001"
    assert views.run(conn, "brief_events.list", q="出来事2")[0]["id"] == "E0002"


def test_reimport_replaces_rows(conn, tmp_path):
    add_reports(conn)
    briefs.import_brief(conn, make_brief(tmp_path))
    d = make_brief(tmp_path / "x", events=[{"id": "E0001", "title": "新", "categories": ["その他"], "region": "北米",
                                            "report_ids": ["r0"], "n_reports": 1}], links=[])
    briefs.import_brief(conn, d)
    assert [e["title"] for e in views.run(conn, "brief_events.list")] == ["新"]
    assert views.run(conn, "brief_links.list", label="2026-11-02") == []


def test_legacy_brief_without_events_json(conn, tmp_path):
    d = make_brief(tmp_path)
    (d / "events.json").unlink()
    (d / "brief.md").write_text("# 旧形式", encoding="utf-8")
    assert briefs.import_brief(conn, d)["legacy"]
    assert views.brief_detail(conn)["brief_md"] == "# 旧形式"


def test_long_doc_is_chunked_and_joined(conn, tmp_path):
    text = "あ" * (briefs.DOC_CHUNK * 2 + 5)
    briefs.import_brief(conn, make_brief(tmp_path, overview=text))
    assert conn.execute("SELECT COUNT(*) FROM docs WHERE name='overview'").fetchone()[0] == 3
    assert views.brief_detail(conn)["overview"] == text


def test_theme_review(conn, tmp_path):
    d = tmp_path / "themes" / "2026-11-02"
    d.mkdir(parents=True)
    (d / "aggregate.json").write_text(json.dumps({"label": "2026-11-02", "since": "2026-11-01", "until": "2026-11-02",
        "briefs": ["2026-11-02"], "events": 2, "spikes": [{"category": "経済・市場", "region": "北米", "count": 9}],
        "themes": [{"category": "経済・市場", "events": 1, "reports": 2, "regions": {"北米": 2},
                    "top": [{"ref": "2026-11-02/E0001", "title": "x"}], "links": []}]}, ensure_ascii=False))
    (d / "themes.md").write_text("# テーマ整理", encoding="utf-8")
    briefs.import_theme_review(conn, d)
    r = views.theme_review_detail(conn)
    assert r["review"] == "# テーマ整理" and r["themes"][0]["top"] == ["2026-11-02/E0001"]


def _d1():
    remote = sqlite3.connect(":memory:")
    remote.row_factory = sqlite3.Row
    remote.execute("PRAGMA foreign_keys = ON")
    migrate(remote, MIGRATIONS, scopes=("shared",))
    return remote


def test_publish_briefs_to_d1_incrementally(conn, tmp_path):
    add_reports(conn)
    briefs.import_brief(conn, make_brief(tmp_path))
    remote = _d1()
    remote.executescript(d1.build_sql(conn, since=""))
    assert len(views.brief_event_detail(remote, "2026-11-02", "E0001")["reports"]) == 2
    assert views.brief_detail(remote)["overview"].startswith("## 全体の要点")
    # 取り込み直し（出来事が減った）→ 差分の publish で D1 の古い行も消える
    mark = conn.execute("SELECT imported_at FROM briefs").fetchone()[0]
    briefs.import_brief(conn, make_brief(tmp_path / "x", events=[{"id": "E0001", "title": "新", "categories": ["その他"],
                                                                "region": "北米", "report_ids": ["r0"], "n_reports": 1}], links=[]))
    conn.execute("UPDATE briefs SET imported_at='2099-01-01T00:00:00Z'")
    remote.executescript(d1.build_sql(conn, since=mark))
    assert [e["id"] for e in views.run(remote, "brief_events.list")] == ["E0001"]
    assert remote.execute("PRAGMA foreign_key_check").fetchall() == []
    # 前回以降に変わっていなければ何も送らない
    assert "brief_events" not in d1.build_sql(conn, since="2099-12-31T00:00:00Z")


def test_last_published_uses_manual_publish_log(conn):
    assert d1.last_published(conn) == ""
    conn.execute("INSERT INTO publish_log (at, applied) VALUES ('2026-11-03T00:00:00Z', 1)")
    conn.execute("INSERT INTO publish_log (at, applied) VALUES ('2026-11-04T00:00:00Z', 0)")
    assert d1.last_published(conn) == "2026-11-03T00:00:00Z"
