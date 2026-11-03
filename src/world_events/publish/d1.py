"""公開用の写し（D1）へ送る upsert の SQL を作る。冪等（INSERT OR REPLACE）。
適用は `wrangler d1 execute <DB> --file dist/d1_upsert.sql`。報道の本文・埋め込み・LLM キャッシュは載せない（§4.7.3）。"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from ..timeutil import now_iso

BRIEF_REPORTS_PER_EVENT = 8   # ブリーフの出来事ごとに D1 に載せる報道の数（見出し・媒体・URL）
_NEW_BRIEFS = "SELECT label FROM briefs WHERE imported_at > :since"
_NEW_REVIEWS = "SELECT label FROM theme_reviews WHERE imported_at > :since"
_REPORT_COLS = ("id", "url", "content_hash", "media", "country", "language", "title", "title_ja", "published_at", "fetched_at")

# (表, 載せる列, 絞り込み。{since} は前回の publish の時刻)
# D1 は外部キーを強制するので、参照される側の表を先に並べる（themes は親の基本テーマを先に）
_TABLES = [
    ("themes", None, "1=1 ORDER BY parent_id IS NOT NULL, id"), ("theme_versions", None, None),
    ("sources", None, None), ("source_state", None, None), ("source_history", None, None),
    ("events", None, "updated_at > :since"),
    # 報道は出来事ごとに代表の数本（見出し・媒体・国・URL）だけ。content_hash はスキーマ上必須なので載せる
    ("reports", _REPORT_COLS,
     "id IN (SELECT report_id FROM (SELECT er.report_id, ROW_NUMBER() OVER (PARTITION BY er.event_id ORDER BY r.published_at) rn"
     " FROM event_reports er JOIN reports r ON r.id=er.report_id WHERE er.event_id IN (SELECT id FROM events WHERE updated_at > :since))"
     " WHERE rn <= 5)"),
    # 載せた報道の分だけ（D1 に無い報道を指す行は外部キーで弾かれる）
    ("event_reports", None, "report_id IN (SELECT report_id FROM (SELECT er.report_id, ROW_NUMBER() OVER (PARTITION BY er.event_id"
     " ORDER BY r.published_at) rn FROM event_reports er JOIN reports r ON r.id=er.report_id"
     " WHERE er.event_id IN (SELECT id FROM events WHERE updated_at > :since)) WHERE rn <= 5)"),
    ("event_themes", None, "event_id IN (SELECT id FROM events WHERE updated_at > :since)"),
    ("links", None, "updated_at > :since"),
    ("snapshots", None, None), ("spikes", None, None), ("digests", None, None), ("job_runs", None, None),
    # 画面から来た依頼の結果（反映済み / 却下と理由）を D1 に返す。Mac 上で積んだ依頼も載せて、画面から履歴を見られるようにする
    ("change_requests", None, "applied_at > :since OR (origin = 'local' AND created_at > :since)"),
    # 簡易ブリーフとテーマ整理（前回以降に取り込んだラベルだけ。入れ直す前に _PRE_DELETES で古い行を消す）
    ("briefs", None, "imported_at > :since"),
    ("brief_events", None, f"label IN ({_NEW_BRIEFS})"),
    ("brief_links", None, f"label IN ({_NEW_BRIEFS})"),
    ("brief_event_reports", None, f"rank < {BRIEF_REPORTS_PER_EVENT} AND label IN ({_NEW_BRIEFS})"),
    ("reports", _REPORT_COLS, f"id IN (SELECT report_id FROM brief_event_reports WHERE rank < {BRIEF_REPORTS_PER_EVENT}"
     f" AND label IN ({_NEW_BRIEFS}))"),
    ("theme_reviews", None, "imported_at > :since"),
    ("docs", None, f"(scope = 'brief' AND label IN ({_NEW_BRIEFS})) OR (scope = 'themes' AND label IN ({_NEW_REVIEWS}))"),
]
# 取り込み直したラベルは、出来事が減っていても古い行が残らないよう先に消す
_PRE_DELETES = [
    ("brief_event_reports", _NEW_BRIEFS), ("brief_links", _NEW_BRIEFS), ("brief_events", _NEW_BRIEFS),
]


def _lit(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, int | float):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def build_sql(conn, since: str = "") -> str:
    out = []
    for table, sub in _PRE_DELETES:
        for (label,) in conn.execute(sub, {"since": since}).fetchall():
            out.append(f"DELETE FROM {table} WHERE label = {_lit(label)};")
    for label, scope in [(r[0], "brief") for r in conn.execute(_NEW_BRIEFS, {"since": since})] + \
                        [(r[0], "themes") for r in conn.execute(_NEW_REVIEWS, {"since": since})]:
        out.append(f"DELETE FROM docs WHERE scope = {_lit(scope)} AND label = {_lit(label)};")
    for table, cols, where in _TABLES:
        select = ", ".join(cols) if cols else "*"
        q = f"SELECT {select} FROM {table}" + (f" WHERE {where}" if where else "")
        rows = conn.execute(q, {"since": since} if ":since" in q else {}).fetchall()
        for r in rows:
            names = r.keys()
            out.append(f"INSERT OR REPLACE INTO {table} ({', '.join(names)}) VALUES ({', '.join(_lit(r[n]) for n in names)});")
    return "\n".join(out) + "\n"


def last_published(conn) -> str:
    """前回 D1 に反映した時刻（夜間の run-daily の publish と、手動の news publish --apply の新しい方）。"""
    a = conn.execute("SELECT MAX(published_at) FROM job_runs WHERE kind='daily'").fetchone()[0] or ""
    b = conn.execute("SELECT MAX(at) FROM publish_log WHERE applied=1").fetchone()[0] or ""
    return max(a, b)


def apply_sql(path: Path, database: str, *, remote: bool = True, run=subprocess.run) -> None:
    cmd = _wrangler_d1(database, remote) + ["--file", str(Path(path).resolve()), "-y"]
    run(cmd, cwd=WORKER_DIR, check=True)


def write(conn, dist: Path, since: str = "") -> Path:
    dist.mkdir(parents=True, exist_ok=True)
    p = dist / "d1_upsert.sql"
    p.write_text(build_sql(conn, since), encoding="utf-8")
    return p


WORKER_DIR = Path(__file__).resolve().parents[3] / "cloud" / "worker"
PULL_SQL = "SELECT id, kind, payload, created_at FROM change_requests WHERE status = 'pending' AND origin = 'web' ORDER BY created_at"


def _wrangler_d1(database: str, remote: bool) -> list[str]:
    """wrangler d1 execute の共通部分。ローカルの D1 の置き場所は WRANGLER_PERSIST_TO（compose で viewer と共有）。"""
    cmd = ["npx", "--no-install", "wrangler", "d1", "execute", database, "--remote" if remote else "--local"]
    if not remote and os.environ.get("WRANGLER_PERSIST_TO"):
        cmd += ["--persist-to", os.environ["WRANGLER_PERSIST_TO"]]
    return cmd


def parse_wrangler_json(out: str) -> list[dict]:
    """`wrangler d1 execute --json` の出力（[{results: [...], success, meta}]）から行を取り出す。"""
    data = json.loads(out)
    rows: list[dict] = []
    for part in data if isinstance(data, list) else [data]:
        if not part.get("success", True):
            raise RuntimeError(f"D1 の実行に失敗: {part}")
        rows.extend(part.get("results") or [])
    return rows


def fetch_pending_requests(database: str, *, remote: bool = True, run=subprocess.run) -> list[dict]:
    """閲覧 UI から積まれた未反映の依頼を D1 から読む（wrangler 経由。認証は CLOUDFLARE_API_TOKEN）。"""
    cmd = _wrangler_d1(database, remote) + ["--json", "--command", PULL_SQL]
    p = run(cmd, cwd=WORKER_DIR, capture_output=True, text=True, check=True)
    return parse_wrangler_json(p.stdout)
