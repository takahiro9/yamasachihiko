"""L1 記事ごとの日本語見出し・要点・固有表現・数値（全件）。"""
from __future__ import annotations

import json

from .. import config
from ..llm import prompts
from ..llm.client import LLM, LLMError, LLMUnavailable
from ..llm.schemas import L1Out
from ..llm.verify import ground_l1

CHUNK = 200   # 途中で止まっても、ここまでの結果は保存済みになる


def run(conn, llm: LLM, concurrency: int = 1, limit: int = 8000) -> dict:
    rows = conn.execute(
        "SELECT r.*, t.body FROM reports r LEFT JOIN report_texts t ON t.report_id=r.id"
        " WHERE r.l1_status='pending' ORDER BY r.fetched_at LIMIT ?", (limit,)).fetchall()
    done = failed = dropped = 0
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i:i + CHUNK]
        materials = [(r["body"] or r["excerpt"] or "")[:config.EXCERPT_CHARS] for r in chunk]
        outs = llm.chat_many(
            [("l1", prompts.L1_SYSTEM, f"見出し: {r['title']}\n本文: {m}", L1Out) for r, m in zip(chunk, materials, strict=True)],
            concurrency)
        stop: LLMUnavailable | None = None
        conn.execute("BEGIN")
        for r, m, out in zip(chunk, materials, outs, strict=True):
            if isinstance(out, LLMUnavailable):   # サーバー断・上限超過: 未処理のまま残して次回に持ち越す
                stop = stop or out
                continue
            if isinstance(out, LLMError):          # 出力不正はその 1 件だけ失敗として残す
                conn.execute("UPDATE reports SET l1_status='failed' WHERE id=?", (r["id"],))
                failed += 1
                continue
            out, drop = ground_l1(out, f"{r['title']}\n{m}")
            dropped += len(drop)
            conn.execute(
                "UPDATE reports SET title_ja=?, points_ja=?, entities=?, numbers=?, event_time=?, l1_status='done' WHERE id=?",
                (out.title_ja, json.dumps(out.points_ja, ensure_ascii=False),
                 json.dumps([e.model_dump() for e in out.entities], ensure_ascii=False),
                 json.dumps([n.model_dump() for n in out.numbers], ensure_ascii=False),
                 out.event_time, r["id"]))
            done += 1
        conn.execute("COMMIT")
        if stop:
            raise stop
    return {"l1_done": done, "l1_failed": failed, "ungrounded_dropped": dropped}
