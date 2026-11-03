from __future__ import annotations

import html
import re


def strip_html(s: str) -> str:
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def extract_body(page_html: str, url: str | None = None) -> str | None:
    """本文抽出。trafilatura が無い環境では None（見出しと抜粋だけで登録する）。"""
    try:
        import trafilatura
    except ImportError:
        return None
    return trafilatura.extract(page_html, url=url, include_comments=False) or None


def clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n]
