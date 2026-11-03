from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx

from conftest import FakeEmbedder
from world_events import runner
from world_events.admin import sources as s
from world_events.publish import d1, ops
from world_events.stages import b1_collect, b5_snapshot, b6_spike, b7_digest
from world_events.stages.b1_collect import Fetcher, Throttle
from world_events.timeutil import iso

NOW = datetime(2026, 10, 25, 16, 0, tzinfo=UTC)   # JST 10/26 01:00


def feed(*items):
    body = "".join(
        f"<item><title>{t}</title><link>{u}</link><pubDate>Sun, 25 Oct 2026 10:00:00 GMT</pubDate><description>{d}</description></item>"
        for t, u, d in items)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>{body}</channel></rss>'


QUAKE = "strong earthquake hits tokyo region magnitude buildings damaged"
FEEDS = {
    "https://jp.example/rss": feed(("Earthquake hits Tokyo", "https://jp.example/1", QUAKE),
                                   ("Film awards announced", "https://jp.example/2", "annual film festival awards ceremony winners")),
    "https://us.example/rss": feed(("Earthquake hits Tokyo", "https://us.example/1?utm_source=a", QUAKE + " reuters")),
}


def make_fetcher(feeds=FEEDS, status=200):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if status != 200:
            return httpx.Response(status)
        return httpx.Response(200, text=feeds[str(req.url)])
    return Fetcher(httpx.Client(transport=httpx.MockTransport(handler)), "test", Throttle(0, sleep=lambda _: None))


def add_sources(conn):
    s.add_source(conn, s.SourceIn(id="jp", type="rss", url="https://jp.example/rss", media="JP News", country="JP", language="ja"))
    s.add_source(conn, s.SourceIn(id="us", type="rss", url="https://us.example/rss", media="US News", country="US", language="en"))


def ctx(conn, settings, llm, fetcher=None, now=NOW, embedder=None):
    return runner.Ctx(conn=conn, settings=settings, llm=llm, embedder=embedder or FakeEmbedder(), fetcher=fetcher or make_fetcher(), now=now)


def test_daily_end_to_end(conn, settings, llm):
    add_sources(conn)
    res = runner.run_daily(ctx(conn, settings, llm))
    assert res["status"] == "success", res
    assert conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 3
    # 転載（同じ地震）は 1 つの出来事に、別の話題は別の出来事になる
    counts = sorted(r[0] for r in conn.execute("SELECT report_count FROM events"))
    assert counts == [1, 2]
    quake = conn.execute("SELECT * FROM events WHERE report_count=2").fetchone()
    assert quake["summary_status"] == "done" and quake["headline"] == "まとめ見出し" and quake["country_count"] == 2
    assert conn.execute("SELECT COUNT(*) FROM event_themes").fetchone()[0] == 2     # すべての出来事が基本テーマに属する
    d = json.loads(conn.execute("SELECT body FROM digests").fetchone()[0])
    assert d["date"] == "2026-10-26" and len(d["top"]) == 2 and d["top"][0]["headline"] == "まとめ見出し"
    assert (settings.dist_dir / "index.html").exists() and (settings.dist_dir / "digest/2026-10-26.html").exists()
    assert "INSERT OR REPLACE INTO events" in (settings.dist_dir / "d1_upsert.sql").read_text()
    assert list((settings.db_path.parent / "backup").glob("news-*.db.gz"))
    assert conn.execute("SELECT published_at FROM job_runs").fetchone()[0]


def test_rerun_is_idempotent(conn, settings, llm):
    add_sources(conn)
    runner.run_daily(ctx(conn, settings, llm))
    before = [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("reports", "events", "event_reports", "event_themes")]
    # 間隔（1 日）が経っていないので再収集されず、LLM も呼ばれない
    n = len(llm.calls)
    runner.run_daily(ctx(conn, settings, llm, now=NOW + timedelta(hours=1)))
    after = [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("reports", "events", "event_reports", "event_themes")]
    assert before == after and len(llm.calls) == n


def test_llm_down_defers_work_and_resumes(conn, settings, llm):
    add_sources(conn)
    llm.down = True
    res = runner.run_daily(ctx(conn, settings, llm))
    assert res["status"] == "partial" and res["stages"]["l1"] == "skipped" and res["stages"]["collect"] == "success"
    assert res["stages"]["embed"] == "success"    # 埋め込みは要約 LLM と別（CPU）なので進む
    assert conn.execute("SELECT COUNT(*) FROM reports WHERE l1_status='pending'").fetchone()[0] == 3
    llm.down = False
    res = runner.run_daily(ctx(conn, settings, llm, now=NOW + timedelta(days=1)))
    assert res["status"] == "success"
    assert conn.execute("SELECT COUNT(*) FROM reports WHERE l1_status='done'").fetchone()[0] == 3


def test_gray_zone_goes_to_l2_and_unsure_splits(conn, settings, llm):
    from world_events import config
    add_sources(conn)
    runner.run_stage(ctx(conn, settings, llm), "collect")
    runner.run_stage(ctx(conn, settings, llm), "embed")
    # 閾値を広げて全部をグレーゾーンにし、unsure は混ぜずに分けることを確かめる
    config_orig = (config.CLUSTER_HIGH, config.CLUSTER_LOW)
    config.CLUSTER_HIGH, config.CLUSTER_LOW = 1.01, 0.0
    try:
        llm.same_verdict = "unsure"
        runner.run_stage(ctx(conn, settings, llm), "b2")
    finally:
        config.CLUSTER_HIGH, config.CLUSTER_LOW = config_orig
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 3
    assert "l2" in llm.calls


def test_human_confirmed_event_is_not_overwritten(conn, settings, llm):
    add_sources(conn)
    runner.run_daily(ctx(conn, settings, llm))
    conn.execute("UPDATE events SET headline='人の直し', confirmed=1 WHERE report_count=2")
    conn.execute("UPDATE events SET summary_status='pending'")
    runner.run_stage(ctx(conn, settings, llm), "l3")
    assert conn.execute("SELECT headline FROM events WHERE report_count=2").fetchone()[0] == "人の直し"


def test_source_failure_auto_disables_and_notifies(conn, settings, llm):
    add_sources(conn)
    for day in range(5):
        runner.run_stage(ctx(conn, settings, llm, make_fetcher(status=500), now=NOW + timedelta(days=day)), "collect")
    row = conn.execute("SELECT enabled, disabled_reason FROM sources WHERE id='jp'").fetchone()
    assert row["enabled"] == 0 and "連続 5 回失敗" in row["disabled_reason"]
    assert s.history(conn, "jp")[-1]["actor"] == "auto"
    d = b7_digest.build(conn)   # 履歴の時刻は実時計なので、ダイジェストも実時計で作る
    assert any("自動停止" in n for n in d["notices"])


def test_url_and_content_dedupe(conn, settings, llm):
    add_sources(conn)
    st = b1_collect.run(conn, make_fetcher(), now=NOW)
    assert st["new"] == 3
    conn.execute("UPDATE source_state SET last_fetched_at=NULL")
    assert b1_collect.run(conn, make_fetcher(), now=NOW)["new"] == 0


def test_old_articles_skipped(conn):
    add_sources(conn)
    old = {"https://jp.example/rss": feed(("Old", "https://jp.example/o", "x")).replace("25 Oct 2026", "01 Oct 2026"),
           "https://us.example/rss": feed()}
    assert b1_collect.run(conn, make_fetcher(old), now=NOW)["new"] == 0


def test_digest_region_cap():
    cands = [{"region": "欧州", "importance": 10 - i} for i in range(6)] + [{"region": "東アジア", "importance": 1}]
    picked = b7_digest._pick(cands, size=10, region_cap=3)
    assert sum(e["region"] == "欧州" for e in picked) == 3 and picked[-1]["region"] == "東アジア"


def test_spike_detection(conn):
    # 8 日前から平常（1 日 1 件）、当日だけ 10 件
    conn.execute("INSERT INTO events (id, headline, first_seen_at, updated_at, region) VALUES ('e','h','x','x','東アジア')")
    conn.execute("INSERT INTO event_themes VALUES ('e','security',1,NULL,0)")
    for i in range(30):
        day = datetime(2026, 10, 26, 3, 0, tzinfo=UTC) - timedelta(days=(0 if i >= 20 else (i % 9) + 1))
        conn.execute("INSERT INTO reports (id,url,content_hash,media,country,language,title,fetched_at) VALUES (?,?,?,?,?,?,?,?)",
                     (f"r{i}", f"u{i}", f"h{i}", "m", "JP", "ja", "t", iso(day)))
        conn.execute("INSERT INTO event_reports VALUES ('e',?,0,'x')", (f"r{i}",))
    found = b6_spike.detect(conn, "2026-10-26")
    assert {(x["theme_id"], x["region"]) for x in found} == {("security", ""), ("security", "東アジア")}
    assert b6_spike.detect(conn, "2026-10-20") == []     # 履歴が 7 日に満たない


def test_change_requests_applied_and_rejected(conn):
    ins = "INSERT INTO change_requests (id,kind,payload,created_at) VALUES (?,?,?, '2026-10-25T00:00:00Z')"
    conn.execute(ins, ("1", "source.add", json.dumps({"id": "w", "type": "rss", "url": "u", "media": "W", "country": "GB", "language": "en"})))
    conn.execute(ins, ("2", "source.add", json.dumps({"id": "bad", "type": "rss", "url": "u", "media": "", "country": "GB", "language": "en"})))
    conn.execute(ins, ("3", "nope", "{}"))
    assert ops.apply_change_requests(conn) == {"applied": 1, "rejected": 2}
    assert s.history(conn, "w")[0]["actor"] == "web"
    assert conn.execute("SELECT status FROM change_requests WHERE id='2'").fetchone()[0] == "rejected"


def test_monthly_snapshot_basic(conn, settings, llm):
    add_sources(conn)
    runner.run_daily(ctx(conn, settings, llm))
    res = runner.run_monthly(ctx(conn, settings, llm), "2026-10")
    assert res["status"] == "success" and res["snapshots"] >= 1
    snap = conn.execute("SELECT * FROM snapshots LIMIT 1").fetchone()
    assert json.loads(snap["distribution"])["country"]
    assert runner.run_monthly(ctx(conn, settings, llm)) == {"skipped": "月初ではない"}
    assert b5_snapshot.previous_month("2026-01") == "2025-12"


def test_d1_sql_escapes_and_limits_reports(conn):
    conn.execute("INSERT INTO events (id, headline, first_seen_at, updated_at) VALUES ('e', 'it''s', 'x', '2026-10-25T00:00:00Z')")
    sql = d1.build_sql(conn, since="2026-10-01T00:00:00Z")
    assert "'it''s'" in sql
    assert "body" not in d1.build_sql(conn)   # 本文は載せない


def test_stage_tokens_recorded_and_budget_stop_is_notified(conn, settings):
    from world_events.llm.client import CachedLLM, TokenGuard
    from world_events.stages.b7_digest import build
    add_sources(conn)
    llm = CachedLLM(__import__("conftest").FakeLLM(), conn, "local", "m", TokenGuard(limit=240))   # 1 回 120 → 2 回で止まる
    res = runner.run_daily(ctx(conn, settings, llm))
    assert res["stages"]["l1"] == "skipped"
    row = conn.execute("SELECT tokens_in, tokens_out, error FROM stage_runs WHERE stage='l1'").fetchone()
    assert row["tokens_in"] > 0 and "上限" in row["error"]
    assert conn.execute("SELECT COUNT(*) FROM reports WHERE l1_status='pending'").fetchone()[0] == 1   # 3 件中 2 件だけ処理済み
    assert any("l1" in n and "上限" in n for n in build(conn)["notices"])
