from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from w3lib.url import canonicalize_url

_TRACKING = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src", "cmpid", "ocid", "ncid", "output"}


def normalize_url(url: str) -> str:
    """URL を正規化する（トラッキング用パラメータとフラグメントを除き、ホストを小文字にする）。"""
    parts = urlsplit(url.strip())
    q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
         if not k.lower().startswith("utm_") and k.lower() not in _TRACKING]
    cleaned = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(q), ""))
    out = canonicalize_url(cleaned)
    path = urlsplit(out).path
    if len(path) > 1 and path.endswith("/"):  # 末尾のスラッシュは同一視
        s = urlsplit(out)
        out = urlunsplit((s.scheme, s.netloc, path.rstrip("/"), s.query, ""))
    return out


def host_of(url: str) -> str:
    return urlsplit(url).netloc.lower()
