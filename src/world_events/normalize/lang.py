from __future__ import annotations

import re

_SCRIPTS = [
    ("ja", re.compile(r"[぀-ヿ]")),
    ("ko", re.compile(r"[가-힯]")),
    ("ru", re.compile(r"[Ѐ-ӿ]")),
    ("ar", re.compile(r"[؀-ۿ]")),
    ("hi", re.compile(r"[ऀ-ॿ]")),
    ("th", re.compile(r"[฀-๿]")),
    ("he", re.compile(r"[֐-׿]")),
    ("zh", re.compile(r"[一-鿿]")),
]


def detect_language(text: str, default: str) -> str:
    """文字種による簡易判定。ラテン文字は区別できないので、情報源の言語を使う。
    fastText lid.176 を入れたらここを差し替える。"""
    for lang, pat in _SCRIPTS:
        if pat.search(text):
            return lang
    return default
