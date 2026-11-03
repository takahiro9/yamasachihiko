from __future__ import annotations

from calendar import timegm
from datetime import UTC, datetime

import feedparser

from ..normalize.text import clip, strip_html
from ..timeutil import iso
from .base import RawReport


def parse_feed(content: bytes | str) -> list[RawReport]:
    feed = feedparser.parse(content)
    out: list[RawReport] = []
    for e in feed.entries:
        url, title = e.get("link"), strip_html(e.get("title", ""))
        if not url or not title:
            continue
        t = e.get("published_parsed") or e.get("updated_parsed")
        published = iso(datetime.fromtimestamp(timegm(t), UTC)) if t else None
        body = strip_html(e.get("summary", "") or (e.get("content") or [{}])[0].get("value", ""))
        out.append(RawReport(url=url, title=title, excerpt=clip(body, 1000), published_at=published))
    return out
