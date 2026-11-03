"""LLM の出力スキーマ。サーバー側で JSON スキーマとして強制する（pydantic から生成）。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Entity(BaseModel):
    name: str                       # 原語のまま（入力にある表記）
    name_ja: str | None = None
    type: Literal["person", "org", "place"]


class NumberFact(BaseModel):
    value: str
    unit: str = ""
    what: str = ""


class L1Out(BaseModel):
    title_ja: str = Field(max_length=80)
    points_ja: list[str] = Field(max_length=5)
    entities: list[Entity] = []
    numbers: list[NumberFact] = []
    event_time: str | None = None


class L2Pair(BaseModel):
    index: int
    verdict: Literal["same", "different", "unsure"]


class L2Out(BaseModel):
    results: list[L2Pair]


class L3Out(BaseModel):
    headline: str = Field(max_length=80)
    summary: str
    occurred_at: str | None = None
    place: str | None = None
    actors: list[str] = []
    discrepancies: list[str] = []      # 報道による食い違い
    uncertain: list[str] = []          # 不確かな点


class ThemeJudge(BaseModel):
    theme_id: str
    belongs: bool
    confidence: float = Field(ge=0, le=1)


class StanceJudge(BaseModel):
    report_index: int
    theme_id: str
    stance: str


class L4Out(BaseModel):
    themes: list[ThemeJudge]
    stances: list[StanceJudge] = []
