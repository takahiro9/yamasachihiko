"""モデルを替えるときの評価（bench のコード。評価データは DB の eval_samples にある。§4.8）。
L1 について、JSON の成立率・作り話として消えた項目の割合・1 件あたりの秒数を測る。"""
from __future__ import annotations

import json
import statistics
import time
import uuid

from . import config
from .llm import prompts
from .llm.client import LLMBase, LLMError
from .llm.schemas import L1Out
from .llm.verify import ground_l1
from .timeutil import now_iso


def sample_from_reports(conn, task: str, n: int) -> int:
    """直近の報道から評価用の入力を抜き出す（正解は人が expected_json に書く）。言語が偏らないよう言語ごとに取る。"""
    langs = [r[0] for r in conn.execute("SELECT language FROM reports GROUP BY language ORDER BY COUNT(*) DESC")]
    per = max(1, n // max(len(langs), 1))
    added = 0
    for lang in langs:
        for r in conn.execute("SELECT title, excerpt, language FROM reports WHERE language=? ORDER BY RANDOM() LIMIT ?", (lang, per)):
            conn.execute("INSERT INTO eval_samples VALUES (?,?,?,NULL,?)",
                         (str(uuid.uuid4()), task, json.dumps(dict(r), ensure_ascii=False), now_iso()))
            added += 1
    return added


def run_l1(conn, llm: LLMBase) -> dict:
    rows = conn.execute("SELECT input_json FROM eval_samples WHERE task='l1'").fetchall()
    ok = dropped = total_items = tin = tout = 0
    times = []
    for row in rows:
        s = json.loads(row["input_json"])
        material = s["excerpt"][:config.EXCERPT_CHARS]
        t0 = time.monotonic()
        try:
            res = llm.complete("l1", prompts.L1_SYSTEM, f"見出し: {s['title']}\n本文: {material}", L1Out)
        except LLMError:
            continue
        times.append(time.monotonic() - t0)
        out = res.out
        tin, tout = tin + res.tokens_in, tout + res.tokens_out
        ok += 1
        total_items += len(out.entities) + len(out.numbers)
        dropped += len(ground_l1(out, f"{s['title']}\n{material}")[1])
    n = len(rows)
    return {
        "samples": n,
        "json_ok_rate": round(ok / n, 3) if n else None,
        "ungrounded_rate": round(dropped / total_items, 3) if total_items else 0.0,
        "sec_per_item_mean": round(statistics.mean(times), 2) if times else None,
        "tokens_in": tin, "tokens_out": tout,
        "tokens_per_item": round((tin + tout) / ok) if ok else None,
        "sec_per_item_p95": round(sorted(times)[int(len(times) * 0.95) - 1], 2) if len(times) >= 2 else None,
    }
