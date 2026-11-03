"""日次ジョブの周辺: 依頼の取り込み（B10・情報源/テーマの変更依頼）・バックアップ・本文の削除。"""
from __future__ import annotations

import gzip
import json
import shutil
import sqlite3
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path

from ..admin import sources as src_admin
from ..admin import themes as theme_admin
from ..config import BODY_RETENTION_DAYS
from ..contracts import validate_change_request
from ..timeutil import iso, now_iso, now_utc


def _apply(conn, kind: str, p: dict, origin: str = "web") -> None:
    p = validate_change_request(kind, p)   # 形の検証は契約（contracts/change_requests.json）で。画面・MCP と同じ規則
    actor = "web" if origin == "web" else "cli"
    if kind == "source.add":
        fields = {k: v for k, v in p.items() if k != "reason"}
        src_admin.add_source(conn, src_admin.SourceIn.model_validate(fields), actor=actor, reason=p.get("reason"))
    elif kind == "source.disable":
        src_admin.set_enabled(conn, p["id"], False, actor=actor, reason=p.get("reason"))
    elif kind == "source.enable":
        src_admin.set_enabled(conn, p["id"], True, actor=actor, reason=p.get("reason"))
    elif kind == "theme.add":
        fields = {k: v for k, v in p.items() if k != "reason"}
        theme_admin.add_theme(conn, theme_admin.ThemeIn.model_validate(fields), reason=p.get("reason"))
    elif kind == "theme.update":
        theme_admin.update_theme(conn, p["id"], name=p.get("name"), scope=p.get("scope"), stance_axis=p.get("stance_axis"),
                                 phases=p.get("phases"), reason=p.get("reason"))
    elif kind == "link.confirm":
        conn.execute("UPDATE links SET confirmed=1, status='confirmed_by_model', updated_at=? WHERE id=?", (now_iso(), p["id"]))
    elif kind == "link.reject":
        conn.execute("UPDATE links SET confirmed=1, status='rejected', updated_at=? WHERE id=?", (now_iso(), p["id"]))
    elif kind == "event.confirm_theme":
        conn.execute("INSERT OR REPLACE INTO event_themes VALUES (?,?,?,1.0,1)", (p["event_id"], p["theme_id"], p["theme_version"]))
    elif kind == "event.remove_theme":
        conn.execute("DELETE FROM event_themes WHERE event_id=? AND theme_id=?", (p["event_id"], p["theme_id"]))
    else:
        raise ValueError(f"未対応の依頼: {kind}")


def apply_change_requests(conn, only_id: str | None = None) -> dict:
    """未反映の依頼を検証して正本に反映する。失敗した依頼は理由付きで却下し、全体は止めない。
    人の修正は確認済みとして残し、以後の自動処理で上書きしない。"""
    stats = {"applied": 0, "rejected": 0}
    q = "SELECT * FROM change_requests WHERE status='pending'" + (" AND id=?" if only_id else "") + " ORDER BY created_at"
    for r in conn.execute(q, (only_id,) if only_id else ()).fetchall():
        try:
            _apply(conn, r["kind"], json.loads(r["payload"]), r["origin"])
            conn.execute("UPDATE change_requests SET status='applied', applied_at=? WHERE id=?", (now_iso(), r["id"]))
            stats["applied"] += 1
        except Exception as e:
            conn.execute("UPDATE change_requests SET status='rejected', reason=?, applied_at=? WHERE id=?",
                         (str(e)[:300], now_iso(), r["id"]))
            stats["rejected"] += 1
    return stats


def submit_change_request(conn, kind: str, payload: dict, *, origin: str = "local", apply_now: bool = False) -> dict:
    """依頼を 1 件積む（CLI・MCP の入口）。形はここで弾く。apply_now なら次の晩を待たずに反映する。"""
    clean = validate_change_request(kind, payload)
    rid = f"{origin}-{uuid.uuid4().hex[:12]}"
    conn.execute("INSERT INTO change_requests (id, kind, payload, created_at, origin) VALUES (?,?,?,?,?)",
                 (rid, kind, json.dumps(clean, ensure_ascii=False), now_iso(), origin))
    if apply_now:
        apply_change_requests(conn, only_id=rid)
    return dict(conn.execute("SELECT * FROM change_requests WHERE id=?", (rid,)).fetchone())


def import_change_requests(conn, rows: list[dict]) -> int:
    """D1 に画面から積まれた依頼を正本に取り込む。既に取り込んだもの（同じ id）は飛ばす。"""
    n = 0
    for r in rows:
        cur = conn.execute("INSERT OR IGNORE INTO change_requests (id, kind, payload, status, created_at, origin)"
                           " VALUES (?,?,?,'pending',?,'web')", (r["id"], r["kind"], r["payload"], r["created_at"]))
        n += cur.rowcount
    return n


def prune_bodies(conn, now=None) -> int:
    """本文は 30 日で捨てる。見出し・抜粋・埋め込みは残す。"""
    cutoff = iso((now or now_utc()) - timedelta(days=BODY_RETENTION_DAYS))
    return conn.execute("DELETE FROM report_texts WHERE stored_at < ?", (cutoff,)).rowcount


def backup(conn, dest_dir: Path, keep: int = 30, now=None) -> Path:
    """sqlite の online backup → gzip。古い世代は keep 件を残して消す。R2 へは wrangler r2 object put で送る。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or now_utc()).strftime("%Y%m%d-%H%M%S")
    out = dest_dir / f"news-{stamp}.db.gz"
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "snap.db"
        dst = sqlite3.connect(tmp)
        conn.backup(dst)
        dst.close()
        with open(tmp, "rb") as f_in, gzip.open(out, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
    for old in sorted(dest_dir.glob("news-*.db.gz"))[:-keep]:
        old.unlink()
    return out
