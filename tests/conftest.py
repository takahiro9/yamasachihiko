from __future__ import annotations

import hashlib
import re
from pathlib import Path

import numpy as np
import pytest

from world_events.config import Settings
from world_events.db import connect, migrate
from world_events.llm.client import LLMBase, LLMUnavailable, Result
from world_events.llm.schemas import L1Out, L2Out, L2Pair, L3Out, L4Out, ThemeJudge

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


@pytest.fixture
def conn():
    c = connect(":memory:")
    migrate(c, MIGRATIONS)
    return c


@pytest.fixture
def settings(tmp_path):
    return Settings(db_path=tmp_path / "news.db", dist_dir=tmp_path / "dist", migrations_dir=MIGRATIONS)


class FakeEmbedder:
    """単語のハッシュで埋め込みを作る。"""

    def __init__(self):
        self.down = False

    def embed(self, texts):
        if self.down:
            raise RuntimeError("embedder down")
        out = []
        for t in texts:
            v = np.zeros(64)
            for w in re.findall(r"\w+", t.lower()):
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 64] += 1
            out.append(v.tolist())
        return out


class FakeLLM(LLMBase):
    """決め打ちの JSON を返す。1 回の呼び出しで入力 100・出力 20 トークンを使ったことにする。"""

    def __init__(self, same_verdict="same"):
        self.calls: list[str] = []
        self.same_verdict = same_verdict
        self.down = False
        self.tin = self.tout = 0

    def usage(self):
        return (self.tin, self.tout)

    def complete(self, task, system, user, schema):
        if self.down:
            raise LLMUnavailable("down")
        self.calls.append(task)
        self.tin, self.tout = self.tin + 100, self.tout + 20
        return Result(self._out(task, user), 100, 20, "fake-model")

    def _out(self, task, user):
        if task == "l1":
            title = re.search(r"見出し: (.*)", user).group(1)
            return L1Out(title_ja=f"日本語:{title}"[:80], points_ja=["要点"], entities=[], numbers=[])
        if task == "l2":
            n = len(re.findall(r"^\[\d+\]$", user, re.M))
            return L2Out(results=[L2Pair(index=i, verdict=self.same_verdict) for i in range(n)])
        if task == "l3":
            return L3Out(headline="まとめ見出し", summary="まとめ要約。")
        if task == "l4":
            ids = re.findall(r"id=([\w-]+)", user)
            return L4Out(themes=[ThemeJudge(theme_id=ids[0], belongs=True, confidence=0.9)])
        raise AssertionError(task)


@pytest.fixture
def llm():
    return FakeLLM()


@pytest.fixture
def embedder():
    return FakeEmbedder()
