"""S4 多言語埋め込み。見出し + 抜粋から作る（言語が違っても同じ出来事の報道が近くなる）。"""
from __future__ import annotations

from ..embed.bge import Embedder
from ..store import vectors


def run(conn, embedder: Embedder, model: str, limit: int = 2000) -> dict:
    rows = conn.execute(
        "SELECT r.id, r.title, r.excerpt FROM reports r LEFT JOIN report_embeddings e ON e.report_id=r.id"
        " WHERE e.report_id IS NULL ORDER BY r.fetched_at LIMIT ?", (limit,)).fetchall()
    n = 0
    for i in range(0, len(rows), 64):
        chunk = rows[i:i + 64]
        vecs = embedder.embed([f"{r['title']}\n{r['excerpt'][:300]}" for r in chunk])
        conn.execute("BEGIN")
        for r, v in zip(chunk, vecs, strict=True):
            nv = vectors.normalize(v)
            conn.execute("INSERT INTO report_embeddings VALUES (?,?,?,?)", (r["id"], model, len(nv), vectors.pack(nv)))
        conn.execute("COMMIT")
        n += len(chunk)
    return {"embedded": n}
