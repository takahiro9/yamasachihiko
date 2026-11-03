from __future__ import annotations

from urllib.parse import urljoin

from ..normalize.text import strip_html
from .base import RawReport


def parse_html_list(page_html: str, base_url: str, extractor: dict) -> list[RawReport]:
    """HTML 一覧ページからリンクを抜く（最後の手段）。extractor = {"link_selector": "a.headline"}"""
    try:
        from selectolax.parser import HTMLParser
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("html_list には selectolax が必要: uv sync --extra extract") from e
    seen, out = set(), []
    for node in HTMLParser(page_html).css(extractor["link_selector"]):
        href = node.attributes.get("href")
        title = strip_html(node.text(deep=True))
        if not href or not title:
            continue
        url = urljoin(base_url, href)
        if url not in seen:
            seen.add(url)
            out.append(RawReport(url=url, title=title))
    return out
