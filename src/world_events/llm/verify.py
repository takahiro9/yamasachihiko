"""作り話の防止: 出力の数値・固有名詞が入力にあるかを規則で照合する（§7）。"""
from __future__ import annotations

import re

from ..normalize.hashing import normalize_text
from .schemas import L1Out, L3Out

_NUM = re.compile(r"\d[\d,.]*")


def _squash(s: str) -> str:
    return re.sub(r"[\s,]", "", normalize_text(s))


def numbers_in(s: str) -> set[str]:
    return {m.group().rstrip(".,").replace(",", "") for m in _NUM.finditer(normalize_text(s)) if m.group().rstrip(".,")}


def ungrounded_numbers(output_text: str, source_text: str) -> set[str]:
    return numbers_in(output_text) - numbers_in(source_text)


def ground_l1(out: L1Out, source_text: str) -> tuple[L1Out, list[str]]:
    """入力にない固有表現・数値を消す。消したものを返す。"""
    src = _squash(source_text)
    dropped: list[str] = []
    ents = []
    for e in out.entities:
        if _squash(e.name) in src:
            ents.append(e)
        else:
            dropped.append(f"entity:{e.name}")
    nums = []
    for n in out.numbers:
        if _squash(n.value) in src:
            nums.append(n)
        else:
            dropped.append(f"number:{n.value}")
    return out.model_copy(update={"entities": ents, "numbers": nums}), dropped


def l3_is_grounded(out: L3Out, source_text: str) -> bool:
    return not ungrounded_numbers(out.headline + out.summary, source_text)
