from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RawReport:
    url: str
    title: str
    excerpt: str = ""
    published_at: str | None = None     # ISO8601 UTC
    media: str | None = None            # 指定が無ければ情報源の値を使う
    country: str | None = None
    language: str | None = None
    body: str | None = None
