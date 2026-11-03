"""B4 出来事をテーマに振り分ける。

基本テーマには必ず 1 つ以上（どれにも当たらなければ「その他」）。争点テーマは親の基本テーマに
属した出来事の中から判定し、各報道に立場を付ける。判定に使ったテーマの版を残し、自動では判定し直さない。"""
from __future__ import annotations

import numpy as np

from ..admin.themes import list_themes
from ..llm import prompts
from ..llm.client import LLM, LLMError
from ..llm.schemas import L4Out
from ..embed.bge import Embedder
from ..store import vectors
from .common import report_text

TOP_K = 3
MIN_CONFIDENCE = 0.5


def ensure_theme_embeddings(conn, embedder: Embedder, model: str) -> dict[str, np.ndarray]:
    out, missing = {}, []
    for t in list_themes(conn):
        row = conn.execute("SELECT vector FROM theme_embeddings WHERE theme_id=? AND version=? AND model=?",
                           (t["id"], t["version"], model)).fetchone()
        if row:
            out[t["id"]] = vectors.unpack(row["vector"])
        else:
            missing.append(t)
    if missing:
        vecs = embedder.embed([f"{t['name']}: {t['scope']}" for t in missing])
        for t, v in zip(missing, vecs, strict=True):
            nv = vectors.normalize(v)
            conn.execute("INSERT INTO theme_embeddings VALUES (?,?,?,?,?)", (t["id"], t["version"], model, len(nv), vectors.pack(nv)))
            out[t["id"]] = nv
    return out


def _event_material(conn, e) -> tuple[str, list]:
    reports = conn.execute(
        "SELECT r.* FROM event_reports er JOIN reports r ON r.id=er.report_id WHERE er.event_id=? ORDER BY r.published_at LIMIT 6",
        (e["id"],)).fetchall()
    body = f"出来事: {e['headline']}\n{e['summary']}\n\n" + "\n".join(f"記事{i}: {report_text(r)}" for i, r in enumerate(reports))
    return body, reports


def _candidates(themes: list[dict], embs: dict[str, np.ndarray], v: np.ndarray, k: int = TOP_K) -> list[dict]:
    scored = sorted(themes, key=lambda t: float(embs[t["id"]] @ v), reverse=True)
    return scored[:k]


def _theme_block(cands: list[dict]) -> str:
    lines = []
    for t in cands:
        ax = f"（論調の軸: {' / '.join(t['stance_axis'])}）" if t["stance_axis"] else ""
        lines.append(f"- id={t['id']} {t['name']}: {t['scope']}{ax}")
    return "候補テーマ:\n" + "\n".join(lines)


def run(conn, llm: LLM, embedder: Embedder, model: str) -> dict:
    embs = ensure_theme_embeddings(conn, embedder, model)
    themes = list_themes(conn)
    basic = [t for t in themes if t["kind"] == "basic" and not t["is_fallback"]]
    fallback = next((t for t in themes if t["is_fallback"]), None)
    issues = [t for t in themes if t["kind"] == "issue"]
    by_id = {t["id"]: t for t in themes}
    stats = {"events": 0, "other": 0, "issue_hits": 0}

    events = conn.execute(
        "SELECT e.*, ee.vector FROM events e JOIN event_embeddings ee ON ee.event_id=e.id"
        " WHERE NOT EXISTS (SELECT 1 FROM event_themes et WHERE et.event_id=e.id) ORDER BY e.first_seen_at").fetchall()
    for e in events:
        v = vectors.unpack(e["vector"])
        material, reports = _event_material(conn, e)
        cands = _candidates(basic, embs, v)
        try:
            out = llm.chat_json("l4", prompts.L4_SYSTEM, material + "\n\n" + _theme_block(cands), L4Out)
        except LLMError as err:
            if type(err) is not LLMError:
                raise
            continue   # 判定できなかった出来事は次回に回す
        allowed = {t["id"] for t in cands}
        hits = [j for j in out.themes if j.belongs and j.theme_id in allowed and j.confidence >= MIN_CONFIDENCE]
        conn.execute("BEGIN")
        for j in hits:
            conn.execute("INSERT OR IGNORE INTO event_themes VALUES (?,?,?,?,0)",
                         (e["id"], j.theme_id, by_id[j.theme_id]["version"], j.confidence))
        if not hits and fallback:
            conn.execute("INSERT OR IGNORE INTO event_themes VALUES (?,?,?,NULL,0)", (e["id"], fallback["id"], fallback["version"]))
            stats["other"] += 1
        conn.execute("COMMIT")
        stats["events"] += 1

        parents = {j.theme_id for j in hits}
        icands = _candidates([t for t in issues if t["parent_id"] in parents], embs, v) if parents else []
        if not icands:
            continue
        try:
            iout = llm.chat_json("l4", prompts.L4_SYSTEM, material + "\n\n" + _theme_block(icands), L4Out)
        except LLMError as err:
            if type(err) is not LLMError:
                raise
            continue
        iallowed = {t["id"]: t for t in icands}
        conn.execute("BEGIN")
        for j in iout.themes:
            t = iallowed.get(j.theme_id)
            if not (t and j.belongs and j.confidence >= MIN_CONFIDENCE):
                continue
            conn.execute("INSERT OR IGNORE INTO event_themes VALUES (?,?,?,?,0)", (e["id"], t["id"], t["version"], j.confidence))
            stats["issue_hits"] += 1
            for s in iout.stances:
                if s.theme_id == t["id"] and 0 <= s.report_index < len(reports) and s.stance in t["stance_axis"]:
                    conn.execute("INSERT OR REPLACE INTO report_stances VALUES (?,?,?,?)",
                                 (reports[s.report_index]["id"], t["id"], t["version"], s.stance))
        conn.execute("COMMIT")
    return stats
