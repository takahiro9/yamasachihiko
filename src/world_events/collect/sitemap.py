from __future__ import annotations

import re
from datetime import UTC, datetime
from xml.etree import ElementTree as ET

from ..timeutil import iso
from .base import RawReport


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_dt(s: str | None) -> str | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return iso(dt if dt.tzinfo else dt.replace(tzinfo=UTC))


def parse_sitemap(content: bytes | str, since: str | None = None) -> list[RawReport]:
    """news サイトマップ（<news:title>・<news:publication_date>）と通常の lastmod に対応。
    since（ISO）より新しいものだけ返す。サイトマップインデックスは呼び出し側で辿る。"""
    root = ET.fromstring(content)
    out: list[RawReport] = []
    for url_el in root:
        if _local(url_el.tag) != "url":
            continue
        d = {}
        for ch in url_el.iter():
            d.setdefault(_local(ch.tag), (ch.text or "").strip())
        loc = d.get("loc")
        if not loc:
            continue
        published = _parse_dt(d.get("publication_date") or d.get("lastmod"))
        if since and published and published <= since:
            continue
        title = d.get("title") or re.sub(r"[-_]+", " ", loc.rstrip("/").rsplit("/", 1)[-1]).strip() or loc
        out.append(RawReport(url=loc, title=title, published_at=published))
    return out


def sitemap_index_children(content: bytes | str) -> list[str]:
    root = ET.fromstring(content)
    if _local(root.tag) != "sitemapindex":
        return []
    return [el.text.strip() for el in root.iter() if _local(el.tag) == "loc" and el.text]
