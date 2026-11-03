"""日次ジョブ・月次ジョブ。各段階は冪等で、前回成功した時点以降の分を処理する（途中から再開できる）。"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field

import structlog

from . import config
from .config import Settings
from .embed.bge import Embedder
from .llm.client import LLM, LLMUnavailable
from .publish import d1, ops, static
from .stages import b1_collect, b2_cluster, b4_theme, b5_snapshot, b6_spike, b7_digest, embed, l1_extract, l3_summary
from .timeutil import JST, iso, jst_date, jst_month, now_iso, now_utc

log = structlog.get_logger()


@dataclass
class Ctx:
    conn: sqlite3.Connection
    settings: Settings
    llm: LLM
    embedder: Embedder
    fetcher: b1_collect.Fetcher | None = None
    now: object = field(default_factory=now_utc)
    fetch_bodies: bool = False


def _publish(c: Ctx) -> dict:
    last = d1.last_published(c.conn)
    pages = static.render(c.conn, c.settings.dist_dir)
    sql = d1.write(c.conn, c.settings.dist_dir, since=last)
    backup = ops.backup(c.conn, c.settings.db_path.parent / "backup", now=c.now)
    # wrangler での D1 / Pages / R2 への反映は ops 側（`wrangler d1 execute` ほか）。ここは成果物を作るところまで
    return {"pages": len(pages), "d1_sql": str(sql), "backup": str(backup)}


STAGES: dict[str, Callable[[Ctx], dict]] = {
    "requests": lambda c: ops.apply_change_requests(c.conn),
    "collect": lambda c: b1_collect.run(c.conn, c.fetcher, now=c.now, fetch_bodies=c.fetch_bodies),
    "embed": lambda c: embed.run(c.conn, c.embedder, c.settings.embed_model),
    "l1": lambda c: l1_extract.run(c.conn, c.llm, c.settings.llm_concurrency),
    "b2": lambda c: b2_cluster.run(c.conn, c.llm, c.now),
    "l3": lambda c: l3_summary.run(c.conn, c.llm),
    "b4": lambda c: b4_theme.run(c.conn, c.llm, c.embedder, c.settings.embed_model),
    "importance": lambda c: {"updated": l3_summary.update_importance(c.conn)},
    "b6": lambda c: b6_spike.run(c.conn, jst_date(c.now)),
    "b7": lambda c: b7_digest.run(c.conn, c.now),
    "prune": lambda c: {"bodies_pruned": ops.prune_bodies(c.conn, c.now)},
    "publish": _publish,
}
DAILY_ORDER = list(STAGES)


def run_stage(c: Ctx, name: str, job_run_id: int | None = None) -> tuple[str, dict]:
    sid = c.conn.execute("INSERT INTO stage_runs (job_run_id, stage, started_at) VALUES (?,?,?)",
                         (job_run_id, name, now_iso())).lastrowid
    in0, out0 = c.llm.usage()
    try:
        result = STAGES[name](c)
        status, err = "success", None
    except LLMUnavailable as e:   # 要約 LLM が応答しない・上限超過: 未処理は残るので次回の実行で続きから
        status, err, result = "skipped", f"要約 LLM を使えない: {e}", {}
    except Exception as e:        # 1 つの段階の失敗で全体は止めない
        log.exception("stage_failed", stage=name)
        status, err, result = "failed", f"{type(e).__name__}: {e}"[:500], {}
    if c.conn.in_transaction:
        c.conn.execute("ROLLBACK")
    in1, out1 = c.llm.usage()
    c.conn.execute("UPDATE stage_runs SET finished_at=?, status=?, processed=?, error=?, tokens_in=?, tokens_out=? WHERE id=?",
                   (now_iso(), status, sum(v for v in result.values() if isinstance(v, int)), err, in1 - in0, out1 - out0, sid))
    log.info("stage", stage=name, status=status, **{k: v for k, v in result.items() if isinstance(v, int | str)})
    return status, result


def previous_night_tokens(conn) -> int:
    """直近の日次ジョブ 1 回分の要約 LLM のトークン数（入力 + 出力）。"""
    r = conn.execute(
        "SELECT SUM(tokens_in + tokens_out) FROM stage_runs WHERE job_run_id = ("
        " SELECT MAX(j.id) FROM job_runs j WHERE j.kind='daily' AND EXISTS ("
        "  SELECT 1 FROM stage_runs s WHERE s.job_run_id=j.id AND s.tokens_in + s.tokens_out > 0))").fetchone()[0]
    return r or 0


def token_limit(conn, settings: Settings) -> int | None:
    """使いすぎの防止: 前日の 2 倍を超えたら止める（小さい履歴で誤って止まらないよう下限あり）。明示の上限があればそれを使う。"""
    if settings.llm_daily_token_limit:
        return settings.llm_daily_token_limit
    prev = previous_night_tokens(conn)
    return max(int(prev * config.BUDGET_RATIO), config.BUDGET_FLOOR_TOKENS) if prev else None


def run_daily(c: Ctx, only: list[str] | None = None) -> dict:
    jid = c.conn.execute("INSERT INTO job_runs (kind, started_at) VALUES ('daily', ?)", (now_iso(),)).lastrowid
    statuses = {}
    for name in only or DAILY_ORDER:
        statuses[name], _ = run_stage(c, name, jid)
    bad = [n for n, s in statuses.items() if s != "success"]
    status = "success" if not bad else ("failed" if statuses.get("b7") != "success" else "partial")
    published = iso(c.now) if statuses.get("publish") == "success" else None
    c.conn.execute("UPDATE job_runs SET finished_at=?, status=?, published_at=? WHERE id=?", (now_iso(), status, published, jid))
    return {"job_run_id": jid, "status": status, "stages": statuses}


def run_monthly(c: Ctx, month: str | None = None) -> dict:
    """毎月 1 日の日次ジョブの後に呼ぶ。月初でなければ何もしない（month を指定したときは常に実行）。"""
    local = c.now.astimezone(JST)
    if month is None:
        if local.day != 1:
            return {"skipped": "月初ではない"}
        month = b5_snapshot.previous_month(jst_month(c.now))
    jid = c.conn.execute("INSERT INTO job_runs (kind, started_at) VALUES ('monthly', ?)", (now_iso(),)).lastrowid
    sid = c.conn.execute("INSERT INTO stage_runs (job_run_id, stage, started_at) VALUES (?, 'b5', ?)", (jid, now_iso())).lastrowid
    try:
        res = b5_snapshot.run(c.conn, month)
        status, err = "success", None
    except Exception as e:
        status, err, res = "failed", str(e)[:500], {}
    c.conn.execute("UPDATE stage_runs SET finished_at=?, status=?, error=? WHERE id=?", (now_iso(), status, err, sid))
    c.conn.execute("UPDATE job_runs SET finished_at=?, status=? WHERE id=?", (now_iso(), status, jid))
    return {"status": status, **res}
