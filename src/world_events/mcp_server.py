"""Claude Code から収集と出来事の整理を回すための MCP サーバー（`news mcp`）。

- 読み取りは contracts/queries.sql（閲覧画面と同じクエリ）。戻り値の形も画面の API とそろえる
- 書く処理は決まった手順のものだけ（収集・ブリーフの書き出し / 検証 / 組み立て・取り込み・依頼・D1 への反映）
- 出来事の統合・つながりの判断は Claude Code 側（news-brief スキルの手順 2・4）に残す

docker compose の `mcp` サービスとして HTTP（http://localhost:8765/mcp）で動かすのが基本。
`news mcp --transport stdio` で Claude Code から直接起動することもできる。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from . import briefs, views
from .config import Settings
from .contracts import change_request_kinds, queries
from .db import connect, migrate
from .publish import d1, ops

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / ".claude" / "skills"
_LABEL = re.compile(r"^[\d_-]{4,40}$")

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
LOCAL_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
EXTERNAL = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)

server = MCPServer(
    name="world-events",
    instructions=(
        "世の中の出来事を把握するシステム（world-events）の操作口。正本は Mac の DB と dist/ のファイル。\n"
        "ブリーフを作る流れ: brief_export → （Claude Code が地域ごとに events/<地域>.json を書く）→ brief_index → "
        "（Claude Code が curation.json と overview.md を書く）→ brief_finalize。手順の詳細は news-brief スキルに従う。\n"
        "collect_start と publish(target='remote') は外部に出る操作なので、実行前にユーザーの確認を取ること。"
    ),
)

_collect: dict[str, Any] = {}   # 実行中の収集（このプロセスで起動したもの）


def _conn():
    s = Settings.from_env()
    c = connect(s.db_path)
    migrate(c, s.migrations_dir)
    return c, s


def _label(label: str) -> str:
    if not _LABEL.match(label or ""):
        raise ValueError(f"ラベルは日付（2026-11-02）か <開始日>_<終了日>: {label!r}")
    return label


def _script(path: Path, *args: str) -> dict:
    """スキルのスクリプトを実行する。stdout の JSON を返し、警告（stderr）は末尾だけ添える。"""
    p = subprocess.run([sys.executable, str(path), *args], cwd=ROOT, capture_output=True, text=True)
    try:
        out = json.loads(p.stdout)
    except json.JSONDecodeError:
        out = p.stdout.strip()[-4000:]
    res = {"ok": p.returncode == 0, "result": out}
    if p.stderr.strip():
        res["stderr"] = p.stderr.strip()[-4000:]
    return res


# ---------- 読む ----------
@server.tool(annotations=READ)
def status() -> dict:
    """全体の状態: 情報源の数・最新のブリーフ・未反映の依頼・収集済みの期間・実行中の収集。"""
    c, s = _conn()
    rng = c.execute("SELECT MIN(COALESCE(published_at, fetched_at)) AS oldest, MAX(fetched_at) AS latest_fetch,"
                    " COUNT(*) AS reports FROM reports").fetchone()
    return {"overview": views.run(c, "status.overview"), "reports": dict(rng),
            "briefs": views.run(c, "briefs.list", limit=5), "collect": collect_status()}


@server.tool(annotations=READ)
def list_queries() -> list[dict]:
    """view で使える読み取りクエリ（閲覧画面と共通の contracts/queries.sql）と引数。"""
    return [{"name": q.name, "params": list(q.params), "one": q.one} for q in queries().values()]


@server.tool(annotations=READ)
def view(name: str, params: dict[str, Any] | None = None) -> Any:
    """名前付きの読み取りクエリを実行する（例: name='sources.list', params={'enabled': 1}）。"""
    c, _ = _conn()
    return views.run(c, name, **(params or {}))


@server.tool(annotations=READ)
def get_brief(label: str | None = None) -> dict | None:
    """ブリーフ 1 本（全体の要点・主な出来事・つながり・内訳）。label を省くと最新。取り込み済みのものだけ。"""
    c, _ = _conn()
    return views.brief_detail(c, _label(label) if label else None)


@server.tool(annotations=READ)
def search_events(label: str | None = None, category: str | None = None, region: str | None = None,
                  q: str | None = None, limit: int = 30, offset: int = 0) -> list[dict]:
    """ブリーフの出来事を探す（重要度順）。label を省くとすべてのブリーフから。category は基本テーマ名（例: 経済・市場）。"""
    c, _ = _conn()
    return views.run(c, "brief_events.list", label=_label(label) if label else None, category=category, region=region,
                     q=q, limit=min(limit, 200), offset=offset)


@server.tool(annotations=READ)
def get_event(label: str, id: str) -> dict | None:
    """ブリーフの出来事 1 件（報道・つながり付き）。id は E0001 の形。"""
    c, _ = _conn()
    return views.brief_event_detail(c, _label(label), id)


@server.tool(annotations=READ)
def get_theme_review(label: str | None = None) -> dict | None:
    """テーマ整理（news-themes の結果）。label を省くと最新。"""
    c, _ = _conn()
    return views.theme_review_detail(c, _label(label) if label else None)


# ---------- 収集 ----------
def _running() -> bool:
    p = _collect.get("proc")
    return p is not None and p.poll() is None


@server.tool(annotations=EXTERNAL)
def collect_start(fetch_bodies: bool = False) -> dict:
    """取りに行く日が来た情報源から報道を集める（news run-stage collect）。時間がかかるのでバックグラウンドで始め、
    collect_status で進み具合を見る。外部のサイトに取りに行くので、実行前にユーザーの確認を取ること。"""
    if _running():
        return {"started": False, "reason": "収集はすでに実行中", **collect_status()}
    _, s = _conn()
    logs = s.db_path.parent / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log = logs / f"collect-{datetime.now():%Y%m%d-%H%M%S}.log"
    cmd = [sys.executable, "-m", "world_events.cli", "run-stage", "collect", *(["--fetch-bodies"] if fetch_bodies else [])]
    f = open(log, "w")  # noqa: SIM115（子プロセスが書く）
    _collect.update(proc=subprocess.Popen(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT),
                    log=str(log), started_at=datetime.now().isoformat(timespec="seconds"))
    return {"started": True, "log": str(log)}


@server.tool(annotations=READ)
def collect_status() -> dict:
    """収集の進み具合: 実行中か、直近の収集段階の結果、ログの末尾。"""
    c, _ = _conn()
    last = c.execute("SELECT started_at, finished_at, status, processed, error FROM stage_runs WHERE stage='collect'"
                     " ORDER BY id DESC LIMIT 1").fetchone()
    out: dict[str, Any] = {"running": _running(), "last_run": dict(last) if last else None}
    if _collect.get("log"):
        tail = Path(_collect["log"]).read_text(encoding="utf-8", errors="replace").splitlines()[-15:]
        out |= {"log": _collect["log"], "started_at": _collect.get("started_at"), "log_tail": tail}
        if not _running() and _collect.get("proc") is not None:
            out["exit_code"] = _collect["proc"].returncode
    return out


# ---------- ブリーフ（news-brief / news-themes の決まった手順） ----------
@server.tool(annotations=LOCAL_WRITE)
def brief_export(since: str | None = None, until: str | None = None, hours: int | None = None) -> dict:
    """news-brief 手順 1: 期間の報道を地域ごとに dist/briefs/<ラベル>/regions/ に書き出す。
    since / until は JST の日付（YYYY-MM-DD、until はその日を含む）か UTC ISO。省略すると最新の収集日時から hours（既定 24）時間。"""
    args = [*(["--since", since] if since else []), *(["--until", until] if until else []),
            *(["--hours", str(hours)] if hours else [])]
    return _script(SKILLS / "news-brief/scripts/export_headlines.py", *args)


@server.tool(annotations=LOCAL_WRITE)
def brief_index(label: str, prev: str | None = None) -> dict:
    """news-brief 手順 3: 地域ごとの events/<地域>.json を検証し events_index.md（統合の候補・前のブリーフ）を書く。"""
    args = ["index", f"dist/briefs/{_label(label)}", *(["--prev", f"dist/briefs/{_label(prev)}"] if prev else [])]
    return _script(SKILLS / "news-brief/scripts/build_events.py", *args)


@server.tool(annotations=LOCAL_WRITE)
def brief_finalize(label: str) -> dict:
    """news-brief 手順 5: curation.json から events.json・links.json・brief.md を組み立て、DB に取り込む（画面・検索に出る）。"""
    res = _script(SKILLS / "news-brief/scripts/build_events.py", "finalize", f"dist/briefs/{_label(label)}")
    if res["ok"]:
        c, s = _conn()
        res["imported"] = briefs.import_brief(c, s.dist_dir / "briefs" / label)
    return res


@server.tool(annotations=LOCAL_WRITE)
def themes_aggregate(since: str | None = None, until: str | None = None) -> dict:
    """news-themes 手順 1: ブリーフをまとめてテーマごとの件数・急な動きを dist/themes/<ラベル>/aggregate.md に書く。
    themes.md を書いたあとは import_dist で取り込む。"""
    args = [*(["--since", since] if since else []), *(["--until", until] if until else [])]
    return _script(SKILLS / "news-themes/scripts/aggregate_themes.py", *args)


@server.tool(annotations=LOCAL_WRITE)
def import_dist(path: str | None = None) -> Any:
    """dist/ のブリーフ・テーマ整理を DB に取り込む（何度やってもよい）。path は dist/briefs/<ラベル> か dist/themes/<ラベル>。省略で全部。"""
    c, s = _conn()
    if not path:
        return briefs.import_all(c, s.dist_dir)
    p = (ROOT / path).resolve()
    if not p.is_relative_to(s.dist_dir.resolve() if s.dist_dir.is_absolute() else (ROOT / s.dist_dir).resolve()):
        raise ValueError("dist/ の下だけ取り込める")
    return briefs.import_theme_review(c, p) if (p / "aggregate.json").exists() else briefs.import_brief(c, p)


# ---------- 依頼と反映 ----------
@server.tool(annotations=READ)
def request_kinds() -> dict:
    """変更の依頼の種類と payload の形（contracts/change_requests.json）。"""
    return {k.name: {"label": k.label, "fields": k.fields} for k in change_request_kinds().values()}


@server.tool(annotations=LOCAL_WRITE)
def submit_request(kind: str, payload: dict[str, Any], apply: bool = False) -> dict:
    """変更の依頼を積む（例: kind='source.disable', payload={'id': ..., 'reason': ...}）。apply=True ですぐ正本に反映する。"""
    c, _ = _conn()
    return ops.submit_change_request(c, kind, payload, apply_now=apply)


@server.tool(annotations=LOCAL_WRITE)
def pull_requests(apply: bool = False, target: str = "local") -> dict:
    """閲覧画面から D1 に積まれた依頼を取り込む。target は local（docker compose の viewer）か remote（Cloudflare）。"""
    c, _ = _conn()
    n = ops.import_change_requests(c, d1.fetch_pending_requests(os.environ.get("D1_DATABASE", "world-events"),
                                                                  remote=target == "remote"))
    return {"imported": n} | (ops.apply_change_requests(c) if apply else {})


@server.tool(annotations=EXTERNAL)
def publish(target: str = "local", full: bool = False) -> dict:
    """dist/ を取り込み、閲覧画面の D1 に写す。target: local（compose の viewer）/ remote（Cloudflare。前回以降の差分。
    ユーザーの確認を取ってから）/ none（SQL を作るだけ）。"""
    if target not in ("local", "remote", "none"):
        raise ValueError("target は local / remote / none")
    c, s = _conn()
    imported = briefs.import_all(c, s.dist_dir)
    since = "" if full or target != "remote" else d1.last_published(c)
    started = d1.now_iso()
    sql = d1.write(c, s.dist_dir, since=since)
    if target != "none":
        d1.apply_sql(sql, os.environ.get("D1_DATABASE", "world-events"), remote=target == "remote")
        if target == "remote":
            c.execute("INSERT INTO publish_log (at, applied) VALUES (?, 1)", (started,))
    return {"imported": [x["label"] for x in imported], "since": since or None, "d1_sql": str(sql), "applied": target}


def main(transport: str = "stdio", host: str = "127.0.0.1", port: int = 8765) -> None:
    if transport == "stdio":
        server.run("stdio")
        return
    import anyio
    # コンテナ内では 0.0.0.0 で待つが、受け付ける Host はローカルだけ（DNS リバインディング対策）
    sec = TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                    allowed_hosts=["localhost:*", "127.0.0.1:*", "[::1]:*"],
                                    allowed_origins=["http://localhost:*", "http://127.0.0.1:*"])
    anyio.run(lambda: server.run_streamable_http_async(host=host, port=port, transport_security=sec))
