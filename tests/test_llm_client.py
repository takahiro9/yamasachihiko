from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import openai
import pytest

from conftest import FakeLLM
from world_events import runner
from world_events.config import BUDGET_FLOOR_TOKENS, Settings
from world_events.llm import client as c
from world_events.llm.schemas import L3Out
from world_events.stages import l1_extract


def cached(conn, llm, env="local", model="m", limit=None):
    return c.CachedLLM(llm, conn, env, model, c.TokenGuard(limit))


def test_cache_hit_skips_call_and_is_keyed_by_env_and_model(conn):
    llm = FakeLLM()
    a = cached(conn, llm).chat_json("l3", "sys", "user", L3Out)
    assert cached(conn, llm).chat_json("l3", "sys", "user", L3Out) == a and llm.calls == ["l3"]
    cached(conn, llm, env="prod").chat_json("l3", "sys", "user", L3Out)       # ローカルの結果は本番に混ざらない
    cached(conn, llm, model="other").chat_json("l3", "sys", "user", L3Out)
    assert llm.calls == ["l3"] * 3
    row = conn.execute("SELECT served_model, tokens_in, tokens_out FROM llm_results LIMIT 1").fetchone()
    assert (row["served_model"], row["tokens_in"], row["tokens_out"]) == ("fake-model", 100, 20)


def test_token_guard_stops_before_next_call(conn):
    llm = FakeLLM()
    cl = cached(conn, llm, limit=200)           # 1 回 120 トークン → 2 回目で超える → 3 回目は呼ばない
    cl.chat_json("l3", "s", "u1", L3Out)
    cl.chat_json("l3", "s", "u2", L3Out)
    with pytest.raises(c.LLMBudgetExceeded):
        cl.chat_json("l3", "s", "u3", L3Out)
    assert len(llm.calls) == 2 and cl.usage() == (200, 40)


def test_chat_many_parallel_keeps_order_and_caches(conn):
    llm = FakeLLM()
    cl = cached(conn, llm)
    reqs = [("l3", "s", f"u{i}", L3Out) for i in range(6)]
    assert len(cl.chat_many(reqs, concurrency=4)) == 6 and len(llm.calls) == 6
    cl.chat_many(reqs, concurrency=4)
    assert len(llm.calls) == 6                  # 2 回目は全部キャッシュ


def test_chat_many_returns_errors_instead_of_raising(conn):
    class Flaky(FakeLLM):
        def complete(self, task, system, user, schema):
            if user == "bad":
                raise c.LLMError("不正")
            return super().complete(task, system, user, schema)
    out = cached(conn, Flaky()).chat_many([("l3", "s", "ok", L3Out), ("l3", "s", "bad", L3Out)], 2)
    assert isinstance(out[0], L3Out) and type(out[1]) is c.LLMError


def test_l1_unavailable_keeps_completed_chunk_and_leaves_rest_pending(conn):
    for i in range(3):
        conn.execute("INSERT INTO reports (id,url,content_hash,media,country,language,title,fetched_at) VALUES (?,?,?,?,?,?,?,?)",
                     (f"r{i}", f"u{i}", f"h{i}", "m", "JP", "ja", f"t{i}", "2026-10-25T00:00:00Z"))

    class DownAfterTwo(FakeLLM):
        def complete(self, task, system, user, schema):
            if len(self.calls) >= 2:
                raise c.LLMUnavailable("down")
            return super().complete(task, system, user, schema)
    with pytest.raises(c.LLMUnavailable):
        l1_extract.run(conn, cached(conn, DownAfterTwo()), concurrency=1)
    states = [r[0] for r in conn.execute("SELECT l1_status FROM reports ORDER BY id")]
    assert states == ["done", "done", "pending"]


def _openai(handler, **kw):
    s = Settings(llm_base_url="http://x/v1", llm_max_retries=0, **kw)
    cl = openai.OpenAI(base_url=s.llm_base_url, api_key="k", max_retries=0, default_headers=s.llm_headers or None,
                       http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    return c.OpenAICompatLLM(s, cl), s


def _ok(content: dict):
    return httpx.Response(200, json={"id": "1", "object": "chat.completion", "created": 0, "model": "@cf/qwen/served",
                                     "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(content)}}],
                                     "usage": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60}})


def test_openai_compat_parses_usage_model_and_sends_access_headers():
    seen = {}

    def handler(req):
        seen.update(headers=req.headers, body=json.loads(req.content))
        return _ok({"headline": "h", "summary": "s"})
    llm, _ = _openai(handler, cf_access_client_id="id", cf_access_client_secret="sec", llm_extra_body={"think": False})
    res = llm.complete("l3", "sys", "user", L3Out)
    assert (res.tokens_in, res.tokens_out, res.model) == (50, 10, "@cf/qwen/served")
    assert seen["headers"]["cf-access-client-id"] == "id" and seen["headers"]["cf-access-client-secret"] == "sec"
    assert seen["body"]["max_tokens"] == 700 and seen["body"]["think"] is False
    assert seen["body"]["response_format"]["json_schema"]["strict"] is True


def test_openai_compat_retries_bad_json_once_then_errors():
    n = []

    def handler(req):
        n.append(1)
        return _ok({"wrong": 1})
    llm, _ = _openai(handler)
    with pytest.raises(c.LLMError) as e:
        llm.complete("l3", "s", "u", L3Out)
    assert type(e.value) is c.LLMError and len(n) == 2


@pytest.mark.parametrize("status", [429, 500, 503, 401, 403])
def test_http_errors_are_unavailable_not_per_item(status):
    llm, _ = _openai(lambda req: httpx.Response(status, json={"error": {"message": "x"}}))
    with pytest.raises(c.LLMUnavailable):
        llm.complete("l3", "s", "u", L3Out)


def test_settings_prod_requires_summarizer_url_and_token(monkeypatch):
    for k in ("LLM_BASE_URL", "CF_ACCESS_CLIENT_ID", "CF_ACCESS_CLIENT_SECRET", "LLM_MODEL", "LLM_CONCURRENCY"):
        monkeypatch.delenv(k, raising=False)
    local = Settings.from_env("local")
    assert (local.llm_model, local.llm_concurrency) == ("qwen3.5:9b", 2) and "11434" in local.llm_base_url
    with pytest.raises(ValueError, match="LLM_BASE_URL"):
        Settings.from_env("prod")
    monkeypatch.setenv("LLM_BASE_URL", "https://summarizer.x.workers.dev/v1")
    with pytest.raises(ValueError, match="CF_ACCESS"):
        Settings.from_env("prod")
    monkeypatch.setenv("CF_ACCESS_CLIENT_ID", "i")
    monkeypatch.setenv("CF_ACCESS_CLIENT_SECRET", "s")
    prod = Settings.from_env("prod")
    assert (prod.llm_model, prod.llm_concurrency) == ("summarizer", 8) and prod.llm_headers["CF-Access-Client-Id"] == "i"
    with pytest.raises(ValueError):
        Settings.from_env("staging")


def test_token_limit_is_twice_previous_night_with_floor(conn):
    s = Settings()
    assert runner.token_limit(conn, s) is None                      # 履歴が無ければ制限しない
    jid = conn.execute("INSERT INTO job_runs (kind, started_at) VALUES ('daily','x')").lastrowid
    conn.execute("INSERT INTO stage_runs (job_run_id, stage, started_at, tokens_in, tokens_out) VALUES (?, 'l1','x', 1000000, 200000)", (jid,))
    assert runner.token_limit(conn, s) == 2_400_000
    conn.execute("UPDATE stage_runs SET tokens_in=100, tokens_out=10")
    assert runner.token_limit(conn, s) == BUDGET_FLOOR_TOKENS       # 小さい履歴で誤って止まらない
    assert runner.token_limit(conn, Settings(llm_daily_token_limit=123)) == 123


def test_empty_env_values_fall_back_to_defaults(monkeypatch):
    for k, v in {"LLM_BASE_URL": "", "LLM_MODEL": "", "LLM_CONCURRENCY": "", "LLM_EXTRA_BODY": "", "LLM_DAILY_TOKEN_LIMIT": "0",
                 "NEWS_ENV": ""}.items():
        monkeypatch.setenv(k, v)
    s = Settings.from_env()
    assert (s.env, s.llm_model, s.llm_concurrency, s.llm_extra_body) == ("local", "qwen3.5:9b", 2, {})
