from __future__ import annotations

from datetime import UTC, datetime

from ..timeutil import iso
from .base import RawReport

DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"

_LANG = {"english": "en", "japanese": "ja", "chinese": "zh", "korean": "ko", "russian": "ru", "spanish": "es",
         "french": "fr", "german": "de", "arabic": "ar", "portuguese": "pt", "italian": "it", "turkish": "tr",
         "hindi": "hi", "indonesian": "id", "thai": "th", "vietnamese": "vi", "ukrainian": "uk", "polish": "pl",
         "dutch": "nl", "persian": "fa", "hebrew": "he", "swahili": "sw", "bengali": "bn", "urdu": "ur"}
_COUNTRY = {"united states": "US", "japan": "JP", "china": "CN", "india": "IN", "united kingdom": "GB",
            "russia": "RU", "germany": "DE", "france": "FR", "brazil": "BR", "south korea": "KR", "israel": "IL",
            "iran": "IR", "turkey": "TR", "australia": "AU", "canada": "CA", "mexico": "MX", "nigeria": "NG",
            "south africa": "ZA", "egypt": "EG", "indonesia": "ID", "pakistan": "PK", "ukraine": "UA",
            "saudi arabia": "SA", "kenya": "KE", "singapore": "SG", "philippines": "PH", "taiwan": "TW",
            "italy": "IT", "spain": "ES", "argentina": "AR", "thailand": "TH", "vietnam": "VN", "bangladesh": "BD"}


def doc_params(query: str, start: datetime, end: datetime, max_records: int = 250) -> dict:
    """期間指定（startdatetime / enddatetime）で前回以降を取る。1 リクエスト 5 秒以上空けるのは呼び出し側。"""
    f = "%Y%m%d%H%M%S"
    return {"query": query, "mode": "artlist", "format": "json", "maxrecords": max_records,
            "sort": "datedesc", "startdatetime": start.strftime(f), "enddatetime": end.strftime(f)}


def parse_artlist(data: dict) -> list[RawReport]:
    out = []
    for a in data.get("articles", []) or []:
        url, title = a.get("url"), (a.get("title") or "").strip()
        if not url or not title:
            continue
        seen = a.get("seendate")
        published = None
        if seen:
            try:
                published = iso(datetime.strptime(seen, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC))
            except ValueError:
                pass
        out.append(RawReport(
            url=url, title=title, published_at=published, media=a.get("domain") or None,
            country=_COUNTRY.get((a.get("sourcecountry") or "").lower()),
            language=_LANG.get((a.get("language") or "").lower()),
        ))
    return out
