from __future__ import annotations

import hashlib
import re
import unicodedata


def normalize_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower()
    return re.sub(r"\s+", " ", s).strip()


def content_hash(title: str, excerpt: str) -> str:
    return hashlib.sha256(normalize_text(title + "\n" + excerpt).encode()).hexdigest()


def simhash(text: str, bits: int = 64) -> int:
    """文字 3-gram の SimHash。通信社記事の転載検出に使う。"""
    t = normalize_text(text)
    grams = [t[i:i + 3] for i in range(max(len(t) - 2, 1))]
    acc = [0] * bits
    for g in grams:
        h = int.from_bytes(hashlib.blake2b(g.encode(), digest_size=bits // 8).digest(), "big")
        for i in range(bits):
            acc[i] += 1 if (h >> i) & 1 else -1
    return sum(1 << i for i, v in enumerate(acc) if v > 0)


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()
