"""B1 報道を集める。LLM は使わない。"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from datetime import timedelta
from urllib.parse import urlsplit

import httpx
import structlog
from protego import Protego

from .. import config
from ..admin.sources import set_enabled
from ..collect import gdelt
from ..collect.base import RawReport
from ..collect.html_list import parse_html_list
from ..collect.rss import parse_feed
from ..collect.sitemap import parse_sitemap, sitemap_index_children
from ..db import tx
from ..normalize.hashing import content_hash
from ..normalize.lang import detect_language
from ..normalize.text import clip, extract_body
from ..normalize.url import host_of, normalize_url
from ..timeutil import iso, now_iso, now_utc, parse_iso

log = structlog.get_logger()
MAX_AGE_DAYS = 7
FEED_CAP_MIN_ITEMS = 20


class Throttle:
    """ドメインごとにアクセス間隔を空ける。"""

    def __init__(self, interval: float = config.DOMAIN_INTERVAL_SEC, sleep=time.sleep, clock=time.monotonic):
        self.interval, self._sleep, self._clock, self._last = interval, sleep, clock, {}

    def wait(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            gap = self.interval - (self._clock() - last)
            if gap > 0:
                self._sleep(gap)
        self._last[host] = self._clock()


class Fetcher:
    def __init__(self, client: httpx.Client, user_agent: str, throttle: Throttle | None = None):
        self.client, self.ua, self.throttle = client, user_agent, throttle or Throttle()
        self._robots: dict[str, Protego | None] = {}

    def allowed(self, url: str) -> bool:
        s = urlsplit(url)
        if s.netloc not in self._robots:
            try:
                self.throttle.wait(s.netloc)
                r = self.client.get(f"{s.scheme}://{s.netloc}/robots.txt")
                self._robots[s.netloc] = Protego.parse(r.text) if r.status_code == 200 else None
            except httpx.HTTPError:
                self._robots[s.netloc] = None
        rp = self._robots[s.netloc]
        return True if rp is None else rp.can_fetch(url, self.ua)

    def get(self, url: str, *, params: dict | None = None, check_robots: bool = True) -> httpx.Response:
        if check_robots and not self.allowed(url):
            raise PermissionError(f"robots.txt で禁止: {url}")
        self.throttle.wait(host_of(url))
        r = self.client.get(url, params=params, headers={"User-Agent": self.ua}, follow_redirects=True)
        r.raise_for_status()
        return r


def is_due(source: dict, last_fetched_at: str | None, now) -> bool:
    if not source["enabled"]:
        return False
    if not last_fetched_at:
        return True
    # 夜間バッチの開始時刻のぶれを吸収するため、6 時間の余裕を見る
    return now - parse_iso(last_fetched_at) >= timedelta(days=source["interval_days"]) - timedelta(hours=6)


def fetch_source(src: dict, last_fetched_at: str | None, fetcher: Fetcher, now) -> list[RawReport]:
    t = src["type"]
    if t == "rss":
        return parse_feed(fetcher.get(src["url"]).content)
    if t == "sitemap":
        content = fetcher.get(src["url"]).content
        out: list[RawReport] = []
        for child in sitemap_index_children(content)[:5]:
            out += parse_sitemap(fetcher.get(child).content, since=last_fetched_at)
        return out or parse_sitemap(content, since=last_fetched_at)
    if t == "html_list":
        return parse_html_list(fetcher.get(src["url"]).text, src["url"], json.loads(src["extractor"]))
    if t == "gdelt_query":
        start = parse_iso(last_fetched_at) if last_fetched_at else now - timedelta(days=1)
        r = fetcher.get(gdelt.DOC_API, params=gdelt.doc_params(src["query"], start, now), check_robots=False)
        return gdelt.parse_artlist(r.json())
    raise ValueError(t)


def store_report(conn: sqlite3.Connection, src: dict, raw: RawReport, now_s: str) -> bool:
    """重複（URL・本文ハッシュ）なら False。"""
    url = normalize_url(raw.url)
    h = content_hash(raw.title, raw.excerpt)
    if conn.execute("SELECT 1 FROM reports WHERE url=? OR content_hash=?", (url, h)).fetchone():
        return False
    language = raw.language or detect_language(raw.title, src["language"])
    rid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO reports (id,url,content_hash,source_id,media,country,language,title,excerpt,published_at,fetched_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (rid, url, h, src["id"], raw.media or src["media"], raw.country or src["country"], language,
         raw.title, clip(raw.excerpt, 1000), raw.published_at, now_s),
    )
    if raw.body:
        conn.execute("INSERT INTO report_texts (report_id, body, stored_at) VALUES (?,?,?)", (rid, raw.body, now_s))
    return True


def run(conn: sqlite3.Connection, fetcher: Fetcher, *, now=None, fetch_bodies: bool = False,
        daily_cap: int = config.DAILY_REPORT_CAP) -> dict:
    now = now or now_utc()
    now_s = iso(now)
    stats = {"sources": 0, "failed": 0, "new": 0, "auto_disabled": []}
    for src in [dict(r) for r in conn.execute("SELECT * FROM sources WHERE enabled=1 ORDER BY id")]:
        st = conn.execute("SELECT * FROM source_state WHERE source_id=?", (src["id"],)).fetchone()
        last = st["last_fetched_at"] if st else None
        if not is_due(src, last, now):
            continue
        stats["sources"] += 1
        try:
            items = fetch_source(src, last, fetcher, now)
        except Exception as e:  # 1 件の失敗で収集ジョブ全体は止めない
            stats["failed"] += 1
            fails = (st["consecutive_failures"] if st else 0) + 1
            conn.execute("UPDATE source_state SET consecutive_failures=?, last_error=? WHERE source_id=?",
                         (fails, str(e)[:300], src["id"]))
            log.warning("source_failed", source=src["id"], error=str(e), failures=fails)
            if fails >= config.SOURCE_AUTO_DISABLE_FAILURES:
                set_enabled(conn, src["id"], False, actor="auto", reason=f"連続 {fails} 回失敗: {str(e)[:100]}")
                stats["auto_disabled"].append(src["id"])
            continue
        cutoff = iso(now - timedelta(days=MAX_AGE_DAYS))
        items = [i for i in items if not i.published_at or i.published_at >= cutoff]
        new = 0
        with tx(conn):
            for raw in items:
                if stats["new"] + new >= daily_cap:
                    break
                if fetch_bodies and not raw.body and len(raw.excerpt) < 200:
                    raw.body = _try_body(fetcher, raw.url)
                new += store_report(conn, src, raw, now_s)
            cap_hit = int(src["type"] == "rss" and last is not None and len(items) >= FEED_CAP_MIN_ITEMS and new == len(items))
            conn.execute(
                "UPDATE source_state SET last_fetched_at=?, consecutive_failures=0, last_error=NULL,"
                " last_item_count=?, feed_cap_hits=feed_cap_hits+? WHERE source_id=?",
                (now_s, len(items), cap_hit, src["id"]),
            )
        stats["new"] += new
    return stats


def backfill_bodies(conn: sqlite3.Connection, fetcher: Fetcher, *, since: str | None = None,
                    limit: int | None = None) -> dict:
    """本文（report_texts）が無い報道の全文を後から取りに行く。

    ドメインを順番に回して、ドメインごとのアクセス間隔の待ちが重ならないようにする。
    1 件ずつ保存するので、途中で止めても次の実行で続きから。取れなかったものは
    fetch_failed に数えるだけで印は付けない（次の実行で再度試す）。
    """
    q = ("SELECT r.id, r.url FROM reports r LEFT JOIN report_texts t ON t.report_id = r.id"
         " WHERE t.report_id IS NULL" + (" AND r.fetched_at >= ?" if since else "") + " ORDER BY r.fetched_at")
    rows = conn.execute(q, (since,) if since else ()).fetchall()
    if limit:
        rows = rows[:limit]
    by_host: dict[str, list[tuple[str, str]]] = {}
    for r in rows:
        by_host.setdefault(host_of(r["url"]), []).append((r["id"], r["url"]))
    stats = {"targets": len(rows), "hosts": len(by_host), "stored": 0, "fetch_failed": 0}
    queues = [list(reversed(v)) for v in by_host.values()]
    done = 0
    while queues:
        for qu in queues:
            rid, url = qu.pop()
            body = _try_body(fetcher, url)
            if body:
                conn.execute("INSERT OR IGNORE INTO report_texts (report_id, body, stored_at) VALUES (?,?,?)",
                             (rid, body, now_iso()))
                stats["stored"] += 1
            else:
                stats["fetch_failed"] += 1
            done += 1
            if done % 100 == 0:
                log.info("backfill_progress", done=done, total=len(rows), stored=stats["stored"])
        queues = [qu for qu in queues if qu]
    return stats


def _try_body(fetcher: Fetcher, url: str) -> str | None:
    try:
        return extract_body(fetcher.get(url).text, url)
    except Exception:  # 本文が取れなくても見出しと抜粋で登録する
        return None
