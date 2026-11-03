"""要約 LLM の呼び出し。ローカルの Ollama も本番の Worker `summarizer` も OpenAI 互換の
`/v1/chat/completions` を受けるので、切り替えは設定（NEWS_ENV）だけ。プロンプト・スキーマ・
出力の照合・キャッシュはこちら（Python）に 1 つだけ持つ。"""
from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from ..config import Settings
from . import prompts

T = TypeVar("T", bound=BaseModel)
Request = tuple[str, str, str, type]   # (task, system, user, schema)


class LLMError(RuntimeError):
    """出力が不正など、その 1 件だけの失敗。"""


class LLMUnavailable(LLMError):
    """サーバーに繋がらない・429/5xx が続く・認証できない。段階ごと止めて、未処理は次回に持ち越す。"""


class LLMBudgetExceeded(LLMUnavailable):
    """1 晩のトークン数が上限を超えた。止めて通知する。"""


@dataclass
class Result:
    out: BaseModel
    tokens_in: int = 0
    tokens_out: int = 0
    model: str | None = None      # サーバー（Worker）が返した実際のモデル ID


class LLM(Protocol):
    def chat_json(self, task: str, system: str, user: str, schema: type[T]) -> T: ...
    def chat_many(self, requests: Sequence[Request], concurrency: int = 1) -> list: ...
    def usage(self) -> tuple[int, int]: ...


class LLMBase:
    """complete() だけ実装すれば chat_json / chat_many が使える。"""

    def complete(self, task: str, system: str, user: str, schema: type[T]) -> Result:
        raise NotImplementedError

    def chat_json(self, task, system, user, schema):
        return self.complete(task, system, user, schema).out

    def chat_many(self, requests, concurrency: int = 1) -> list:
        """結果を要求と同じ順で返す。失敗した要求は LLMError のインスタンスが入る（例外は投げない）。"""
        out: list = []
        for r in requests:
            try:
                out.append(self.chat_json(*r))
            except LLMError as e:
                out.append(e)
        return out

    def usage(self) -> tuple[int, int]:
        return (0, 0)


def _availability_errors() -> tuple[type[Exception], ...]:
    import openai
    # 接続断・タイムアウト・レート上限・5xx・認証/権限（Access の設定ミス）。いずれも 1 件ごとに失敗扱いにせず止める
    return (openai.APIConnectionError, openai.RateLimitError, openai.InternalServerError,
            openai.AuthenticationError, openai.PermissionDeniedError)


class OpenAICompatLLM(LLMBase):
    def __init__(self, s: Settings, client=None):
        if client is None:
            from openai import OpenAI
            client = OpenAI(base_url=s.llm_base_url, api_key=s.llm_api_key, timeout=s.llm_timeout_sec,
                            max_retries=s.llm_max_retries, default_headers=s.llm_headers or None)
        self.client, self.s = client, s

    def complete(self, task: str, system: str, user: str, schema: type[T]) -> Result:
        extra = {"extra_body": self.s.llm_extra_body} if self.s.llm_extra_body else {}
        last: Exception | None = None
        tin = tout = 0
        for _ in range(2):  # JSON が崩れたら 1 回だけ再実行
            try:
                r = self.client.chat.completions.create(
                    model=self.s.llm_model,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    temperature=self.s.llm_temperature,
                    max_tokens=prompts.MAX_TOKENS[task],
                    response_format={"type": "json_schema", "json_schema": {
                        "name": task, "schema": schema.model_json_schema(), "strict": True}},
                    **extra,
                )
            except _availability_errors() as e:
                raise LLMUnavailable(f"{type(e).__name__}: {e}") from e
            u = getattr(r, "usage", None)
            tin += getattr(u, "prompt_tokens", 0) or 0
            tout += getattr(u, "completion_tokens", 0) or 0
            try:
                return Result(schema.model_validate_json(r.choices[0].message.content or ""), tin, tout, getattr(r, "model", None))
            except (ValidationError, ValueError) as e:
                last = e
        raise LLMError(f"{task}: 出力が不正: {last}")


class TokenGuard:
    """使いすぎの防止。この実行で使ったトークン数を数え、上限を超えたら LLMBudgetExceeded。"""

    def __init__(self, limit: int | None = None):
        self.limit, self.tokens_in, self.tokens_out = limit or None, 0, 0
        self._lock = threading.Lock()

    def check(self) -> None:
        with self._lock:
            used = self.tokens_in + self.tokens_out
            if self.limit and used >= self.limit:
                raise LLMBudgetExceeded(f"トークン数が上限を超えた: {used:,} / {self.limit:,}")

    def add(self, tin: int, tout: int) -> None:
        with self._lock:
            self.tokens_in += tin
            self.tokens_out += tout


class CachedLLM(LLMBase):
    """同じ入力・env・モデル・プロンプト版なら再実行しない（llm_results）。
    DB に触るのは呼び出したスレッドだけ（sqlite の接続は共有しない）。並列に投げるのは API 呼び出しだけ。"""

    def __init__(self, inner: LLMBase, conn, env: str, model: str, guard: TokenGuard | None = None):
        self.inner, self.conn, self.env, self.model = inner, conn, env, model
        self.guard = guard or TokenGuard()

    def usage(self) -> tuple[int, int]:
        return (self.guard.tokens_in, self.guard.tokens_out)

    def _key(self, task: str, system: str, user: str) -> str:
        return hashlib.sha256(f"{task}\0{system}\0{user}".encode()).hexdigest()

    def _lookup(self, key: str, task: str, schema):
        row = self.conn.execute(
            "SELECT output_json FROM llm_results WHERE input_hash=? AND env=? AND model=? AND prompt_version=?",
            (key, self.env, self.model, prompts.VERSIONS[task])).fetchone()
        return schema.model_validate_json(row["output_json"]) if row else None

    def _store(self, key: str, task: str, res: Result, latency_ms: int) -> None:
        from ..timeutil import now_iso
        self.conn.execute(
            "INSERT OR REPLACE INTO llm_results VALUES (?,?,?,?,?,?,?,?,?,?)",
            (key, self.env, self.model, prompts.VERSIONS[task], res.model, res.out.model_dump_json(), latency_ms,
             res.tokens_in, res.tokens_out, now_iso()))

    def _call(self, task, system, user, schema):
        self.guard.check()
        t0 = time.monotonic()
        res = self.inner.complete(task, system, user, schema)
        self.guard.add(res.tokens_in, res.tokens_out)
        return res, int((time.monotonic() - t0) * 1000)

    def complete(self, task, system, user, schema) -> Result:
        key = self._key(task, system, user)
        hit = self._lookup(key, task, schema)
        if hit is not None:
            return Result(hit)
        res, ms = self._call(task, system, user, schema)
        self._store(key, task, res, ms)
        return res

    def chat_many(self, requests, concurrency: int = 1) -> list:
        out: list = [None] * len(requests)
        misses = []
        for i, (task, system, user, schema) in enumerate(requests):
            key = self._key(task, system, user)
            hit = self._lookup(key, task, schema)
            if hit is not None:
                out[i] = hit
            else:
                misses.append((i, key))
        if not misses:
            return out

        def work(i: int):
            try:
                return self._call(*requests[i])
            except LLMError as e:
                return e

        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            results = list(pool.map(work, [i for i, _ in misses]))
        for (i, key), r in zip(misses, results, strict=True):   # 保存は呼び出し元のスレッドで
            if isinstance(r, LLMError):
                out[i] = r
            else:
                res, ms = r
                self._store(key, requests[i][0], res, ms)
                out[i] = res.out
        return out
