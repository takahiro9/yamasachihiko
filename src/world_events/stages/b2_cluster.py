"""B2 報道を出来事にまとめる（S5 + L2）。

埋め込みの近さで決め、どちらとも言えないものだけ L2 で判定する。"""
from __future__ import annotations

import uuid
from collections import Counter
from datetime import timedelta

import numpy as np

from .. import config
from ..llm import prompts
from ..llm.client import LLM, LLMError
from ..llm.schemas import L2Out
from ..regions import region_of
from ..store import vectors
from ..timeutil import iso, now_iso, now_utc
from .common import load_event_matrix, report_text


def _refresh_event(conn, event_id: str, now_s: str) -> None:
    rows = conn.execute(
        "SELECT r.country FROM event_reports er JOIN reports r ON r.id=er.report_id WHERE er.event_id=?", (event_id,)).fetchall()
    countries = Counter(r["country"] for r in rows)
    region = region_of(countries.most_common(1)[0][0]) if countries else None
    conn.execute("UPDATE events SET report_count=?, country_count=?, region=?, updated_at=? WHERE id=?",
                 (len(rows), len(countries), region, now_s, event_id))


def _new_event(conn, r, now_s: str) -> str:
    eid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO events (id, headline, occurred_at, summary_status, first_seen_at, updated_at) VALUES (?,?,?, 'pending', ?, ?)",
        (eid, r["title_ja"] or r["title"], r["event_time"] or r["published_at"], now_s, now_s))
    return eid


def _attach(conn, eid: str, r, vec: np.ndarray, now_s: str) -> None:
    conn.execute("INSERT INTO event_reports (event_id, report_id, joined_at) VALUES (?,?,?)", (eid, r["id"], now_s))
    row = conn.execute("SELECT vector, n FROM event_embeddings WHERE event_id=?", (eid,)).fetchone()
    if row:
        n = row["n"]
        centroid = vectors.normalize(vectors.unpack(row["vector"]) * n + vec)
        conn.execute("UPDATE event_embeddings SET vector=?, n=? WHERE event_id=?", (vectors.pack(centroid), n + 1, eid))
    else:
        conn.execute("INSERT INTO event_embeddings VALUES (?,?,?,1)", (eid, len(vec), vectors.pack(vec)))
    _refresh_event(conn, eid, now_s)


def run(conn, llm: LLM, now=None) -> dict:
    now = now or now_utc()
    now_s = iso(now)
    window = iso(now - timedelta(days=config.CLUSTER_WINDOW_DAYS))
    ids, mat = load_event_matrix(conn, "e.updated_at >= ?", (window,))
    ids = list(ids)

    reports = conn.execute(
        "SELECT r.*, emb.vector FROM reports r JOIN report_embeddings emb ON emb.report_id=r.id"
        " LEFT JOIN event_reports er ON er.report_id=r.id WHERE er.report_id IS NULL ORDER BY r.fetched_at, r.id").fetchall()

    stats = {"added": 0, "created": 0, "l2_calls": 0, "l2_same": 0}
    gray: list[tuple] = []   # (report, vec, event_id)
    conn.execute("BEGIN")
    for r in reports:
        v = vectors.unpack(r["vector"])
        if len(ids):
            sims = mat @ v
            best = int(np.argmax(sims))
            sim = float(sims[best])
        else:
            best, sim = -1, -1.0
        if sim >= config.CLUSTER_HIGH:
            _attach(conn, ids[best], r, v, now_s)
            mat[best] = vectors.unpack(conn.execute("SELECT vector FROM event_embeddings WHERE event_id=?", (ids[best],)).fetchone()[0])
            stats["added"] += 1
        elif sim >= config.CLUSTER_LOW:
            gray.append((r, v, ids[best]))
        else:
            eid = _new_event(conn, r, now_s)
            _attach(conn, eid, r, v, now_s)
            ids.append(eid)
            mat = np.vstack([mat, v]) if mat.size else v[None, :].copy()   # frombuffer は読み取り専用なので複製する
            stats["created"] += 1
    conn.execute("COMMIT")

    # グレーゾーンは 10 組ずつまとめて L2 で判定する
    for i in range(0, len(gray), config.L2_BATCH):
        batch = gray[i:i + config.L2_BATCH]
        lines = []
        for j, (r, _, eid) in enumerate(batch):
            ev = conn.execute("SELECT headline FROM events WHERE id=?", (eid,)).fetchone()
            top = conn.execute(
                "SELECT r.* FROM event_reports er JOIN reports r ON r.id=er.report_id WHERE er.event_id=? LIMIT 1", (eid,)).fetchone()
            lines.append(f"[{j}]\nA: {ev['headline']}\n{report_text(top) if top else ''}\nB: {report_text(r)}")
        verdicts = {}
        try:
            stats["l2_calls"] += 1
            out = llm.chat_json("l2", prompts.L2_SYSTEM, "\n\n".join(lines), L2Out)
            verdicts = {p.index: p.verdict for p in out.results}
        except LLMError as e:
            if type(e) is not LLMError:
                raise   # サーバー断は上へ（未処理の報道は次回に持ち越す）
        conn.execute("BEGIN")
        for j, (r, v, eid) in enumerate(batch):
            if verdicts.get(j) == "same":
                _attach(conn, eid, r, v, now_s)
                stats["l2_same"] += 1
            else:                                   # different / unsure / 判定失敗は新しい出来事（混ぜるより分ける）
                new_id = _new_event(conn, r, now_s)
                _attach(conn, new_id, r, v, now_s)
                stats["created"] += 1
        conn.execute("COMMIT")
    return stats
