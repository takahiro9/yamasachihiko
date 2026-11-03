from __future__ import annotations

import json
import sqlite3

import numpy as np

from ..store import vectors


def report_text(r: sqlite3.Row | dict) -> str:
    """LLM に渡す記事の材料（日本語化済みならそれを優先）。"""
    pts = json.loads(r["points_ja"]) if r["points_ja"] else []
    head = r["title_ja"] or r["title"]
    return head + ("\n" + "\n".join(f"- {p}" for p in pts) if pts else "")


def load_event_matrix(conn: sqlite3.Connection, where: str = "1=1", params=()):
    rows = conn.execute(
        f"SELECT e.id, ee.vector, ee.n FROM events e JOIN event_embeddings ee ON ee.event_id=e.id WHERE {where}", params
    ).fetchall()
    ids = [r["id"] for r in rows]
    mat = np.vstack([vectors.unpack(r["vector"]) for r in rows]) if rows else np.zeros((0, 0), dtype="float32")
    return ids, mat
