from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import httpx
import typer

from . import bench as bench_mod
from . import briefs as briefs_mod
from . import runner, views
from .admin import sources as src_admin
from .admin import themes as theme_admin
from .config import Settings
from .db import connect, migrate
from .embed.bge import SentenceTransformerEmbedder
from .llm.client import CachedLLM, OpenAICompatLLM, TokenGuard
from .contracts import change_request_kinds, queries
from .publish import d1 as d1_mod
from .publish import ops as ops_mod
from .stages import b1_collect, b5_snapshot
from .timeutil import now_utc

app = typer.Typer(no_args_is_help=True, help="世の中の出来事を把握するシステム")
source_app = typer.Typer(no_args_is_help=True, help="情報源（収集設定）")
theme_app = typer.Typer(no_args_is_help=True, help="テーマの定義")
eval_app = typer.Typer(no_args_is_help=True, help="評価セット")
app.add_typer(source_app, name="source")
app.add_typer(theme_app, name="theme")
app.add_typer(eval_app, name="eval")
brief_app = typer.Typer(no_args_is_help=True, help="簡易ブリーフ・テーマ整理（dist/ のファイル）の取り込み")
app.add_typer(brief_app, name="brief")
requests_app = typer.Typer(no_args_is_help=True, help="変更依頼（閲覧 UI・MCP からの依頼）")
app.add_typer(requests_app, name="requests")


def _conn(env: str | None = None, model: str | None = None):
    s = Settings.from_env(env, model)
    c = connect(s.db_path)
    migrate(c, s.migrations_dir)
    return c, s


def _print(obj) -> None:
    typer.echo(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


@app.command("db-migrate")
def db_migrate() -> None:
    """未適用の migration を適用する。"""
    s = Settings.from_env()
    for name in migrate(connect(s.db_path), s.migrations_dir) or ["（適用なし）"]:
        typer.echo(name)


# ---- 情報源 ----
@source_app.command("add")
def source_add(id: str, type: str, media: str, country: str, language: str,
               url: Annotated[str, typer.Option()] = "", query: Annotated[str | None, typer.Option()] = None,
               extractor: Annotated[str | None, typer.Option(help="JSON（html_list のみ）")] = None,
               interval_days: Annotated[int, typer.Option()] = 1, note: Annotated[str | None, typer.Option()] = None) -> None:
    c, _ = _conn()
    try:
        row = src_admin.add_source(c, src_admin.SourceIn(
            id=id, type=type, url=url, media=media, country=country, language=language, interval_days=interval_days,
            extractor=json.loads(extractor) if extractor else None, query=query, note=note))
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
    _print(row)


@source_app.command("update")
def source_update(id: str, url: str | None = None, media: str | None = None, country: str | None = None,
                  language: str | None = None, interval_days: int | None = None, note: str | None = None,
                  reason: str | None = None) -> None:
    c, _ = _conn()
    changes = {k: v for k, v in dict(url=url, media=media, country=country, language=language,
                                      interval_days=interval_days, note=note).items() if v is not None}
    try:
        _print(src_admin.update_source(c, id, reason=reason, **changes))
    except (ValueError, KeyError) as e:
        raise typer.BadParameter(str(e)) from e


@source_app.command("disable")
def source_disable(id: str, reason: Annotated[str, typer.Option(help="停止の理由")]) -> None:
    c, _ = _conn()
    _print(src_admin.set_enabled(c, id, False, reason=reason))


@source_app.command("enable")
def source_enable(id: str) -> None:
    c, _ = _conn()
    _print(src_admin.set_enabled(c, id, True))


@source_app.command("list")
def source_list(enabled_only: bool = False) -> None:
    c, _ = _conn()
    for r in src_admin.list_sources(c, only_enabled=enabled_only):
        mark = "●" if r["enabled"] else "○"
        typer.echo(f"{mark} {r['id']:<24} {r['type']:<11} {r['country']} {r['language']} {r['media']}"
                   f"  失敗{r['consecutive_failures'] or 0}  最終 {r['last_fetched_at'] or '-'}")


@source_app.command("history")
def source_history(id: str) -> None:
    c, _ = _conn()
    for h in src_admin.history(c, id):
        typer.echo(f"{h['at']} [{h['actor']}] {h['action']} {h['reason'] or ''}")


@source_app.command("import")
def source_import(file: Path) -> None:
    c, _ = _conn()
    added, errors = src_admin.import_sources(c, file.read_text(encoding="utf-8"), "csv" if file.suffix == ".csv" else "json")
    typer.echo(f"追加 {added} 件")
    for e in errors:
        typer.echo(f"  エラー {e}", err=True)
    if errors:
        raise typer.Exit(1)


@source_app.command("export")
def source_export(fmt: str = "json") -> None:
    c, _ = _conn()
    typer.echo(src_admin.export_sources(c, fmt))  # type: ignore[arg-type]


# ---- テーマ ----
@theme_app.command("list")
def theme_list() -> None:
    c, _ = _conn()
    for t in theme_admin.list_themes(c):
        parent = f" ← {t['parent_id']}" if t["parent_id"] else ""
        typer.echo(f"{t['kind']:<5} {t['id']:<20} v{t['version']} {t['name']}{parent}")


@theme_app.command("add")
def theme_add(id: str, name: Annotated[str, typer.Option()], scope: Annotated[str, typer.Option()],
              kind: Annotated[str, typer.Option()] = "issue", parent: Annotated[str | None, typer.Option()] = None,
              stance: Annotated[str | None, typer.Option(help="推進,慎重,反対")] = None,
              phases: Annotated[str | None, typer.Option(help="黎明,拡大,摩擦")] = None) -> None:
    c, _ = _conn()
    try:
        t = theme_admin.ThemeIn(id=id, kind=kind, parent_id=parent, name=name, scope=scope,
                                stance_axis=theme_admin.split_csv(stance) if stance else None,
                                phases=theme_admin.split_csv(phases) if phases else None)
        _print(theme_admin.add_theme(c, t))
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e


@theme_app.command("update")
def theme_update(id: str, name: str | None = None, scope: str | None = None, stance: str | None = None,
                 phases: str | None = None, reason: str | None = None) -> None:
    c, _ = _conn()
    try:
        _print(theme_admin.update_theme(c, id, name=name, scope=scope, reason=reason,
                                        stance_axis=theme_admin.split_csv(stance) if stance else None,
                                        phases=theme_admin.split_csv(phases) if phases else None))
    except (ValueError, KeyError) as e:
        raise typer.BadParameter(str(e)) from e


@theme_app.command("history")
def theme_history(id: str) -> None:
    c, _ = _conn()
    for v in theme_admin.theme_history(c, id):
        typer.echo(f"v{v['version']} {v['created_at']} {v['name']} — {v['scope']}  ({v['reason'] or ''})")


# ---- 実行 ----
def _ctx(fetch_bodies: bool = False) -> runner.Ctx:
    c, s = _conn()
    llm = CachedLLM(OpenAICompatLLM(s), c, s.env, s.llm_model, TokenGuard(runner.token_limit(c, s)))
    fetcher = b1_collect.Fetcher(httpx.Client(timeout=30), s.user_agent)
    return runner.Ctx(conn=c, settings=s, llm=llm, embedder=SentenceTransformerEmbedder(s.embed_model), fetcher=fetcher,
                      now=now_utc(), fetch_bodies=fetch_bodies)


@app.command("run-daily")
def run_daily(fetch_bodies: bool = False) -> None:
    """日次ジョブ（収集 → … → ダイジェスト → publish）。"""
    res = runner.run_daily(_ctx(fetch_bodies))
    _print(res)
    if res["status"] == "failed":
        raise typer.Exit(1)


@app.command("run-stage")
def run_stage(name: str, fetch_bodies: Annotated[bool, typer.Option(help="collect のとき本文も取る")] = False) -> None:
    """1 段階だけ実行する（デバッグ・再実行用）。"""
    if name not in runner.STAGES:
        raise typer.BadParameter(f"{', '.join(runner.STAGES)} のどれか")
    status, result = runner.run_stage(_ctx(fetch_bodies), name)
    _print({"stage": name, "status": status, **result})
    if status == "failed":
        raise typer.Exit(1)


@app.command("fetch-bodies")
def fetch_bodies_cmd(
    since: Annotated[str | None, typer.Option(help="この時刻（UTC ISO）以降に収集した報道だけ")] = None,
    limit: Annotated[int | None, typer.Option(help="最大件数")] = None,
) -> None:
    """本文が無い報道の全文を後から取りに行く（robots.txt・ドメイン間隔は収集と同じ）。"""
    c, s = _conn()
    fetcher = b1_collect.Fetcher(httpx.Client(timeout=30), s.user_agent)
    _print(b1_collect.backfill_bodies(c, fetcher, since=since, limit=limit))


@app.command("run-monthly")
def run_monthly(month: Annotated[str | None, typer.Option(help="YYYY-MM。省略すると月初のときだけ前月分")] = None) -> None:
    if month:
        b5_snapshot.previous_month(month)   # 形式の検証
    _print(runner.run_monthly(_ctx(), month))


@app.command("digest")
def digest(date: str | None = None) -> None:
    """保存済みダイジェストを端末に表示する。"""
    c, _ = _conn()
    row = c.execute("SELECT body FROM digests WHERE date=?", (date,)).fetchone() if date else \
        c.execute("SELECT body FROM digests ORDER BY date DESC LIMIT 1").fetchone()
    if not row:
        typer.echo("ダイジェストがない", err=True)
        raise typer.Exit(1)
    d = json.loads(row["body"])
    typer.echo(f"# {d['date']}")
    for s in d["spikes"]:
        typer.echo(f"! 急な動き {s['name']} {s['region']} {s['count']}件（平常 {s['baseline']}）")
    for n in d["notices"]:
        typer.echo(f"! {n}")
    for e in d["top"]:
        typer.echo(f"\n- [{e['region']}] {e['headline']}（報道 {e['report_count']}）\n  {e['summary']}")
        if e["continuation"]:
            typer.echo(f"  ↳ {e['continuation']}")


# ---- 閲覧（contracts/queries.sql。画面・MCP と同じクエリ） ----
@app.command("view")
def view(name: Annotated[str | None, typer.Argument(help="クエリ名（省略すると一覧）")] = None,
         param: Annotated[list[str] | None, typer.Option("--param", "-p", help="key=value（数値は数値として渡す）")] = None) -> None:
    """閲覧用のクエリを正本に流す。例: news view events.list -p theme=economy -p limit=5"""
    if not name:
        for q in queries().values():
            typer.echo(f"{q.name}({', '.join(q.params)})")
        return
    kw = {}
    for p in param or []:
        k, _, v = p.partition("=")
        kw[k] = int(v) if v.lstrip("-").isdigit() else v
    c, _ = _conn()
    _print(views.run(c, name, **kw))


# ---- ブリーフの取り込みと公開 ----
@brief_app.command("import")
def brief_import(path: Annotated[Path | None, typer.Argument(help="dist/briefs/<ラベル> か dist/themes/<ラベル>。省略すると dist/ を全部")] = None) -> None:
    """news-brief / news-themes の結果を DB（briefs・brief_* ・theme_reviews・docs）に取り込む。何度やってもよい。"""
    c, s = _conn()
    if path is None:
        _print(briefs_mod.import_all(c, s.dist_dir))
    elif (path / "aggregate.json").exists():
        _print(briefs_mod.import_theme_review(c, path))
    else:
        _print(briefs_mod.import_brief(c, path))


@brief_app.command("show")
def brief_show(label: Annotated[str | None, typer.Argument(help="省略すると最新")] = None) -> None:
    c, _ = _conn()
    _print(views.brief_detail(c, label))


@app.command("publish")
def publish(apply: Annotated[bool, typer.Option(help="wrangler で D1 に適用する（CLOUDFLARE_API_TOKEN が要る）")] = False,
            database: Annotated[str, typer.Option(envvar="D1_DATABASE")] = "world-events",
            local: Annotated[bool, typer.Option(help="wrangler dev のローカル D1 に適用する")] = False,
            full: Annotated[bool, typer.Option(help="前回分に関係なく全部送る")] = False,
            skip_import: Annotated[bool, typer.Option(help="dist/ の取り込みを省く")] = False) -> None:
    """閲覧 UI 向けに D1 へ写す。dist/ のブリーフを取り込み → 前回以降の差分の upsert SQL を作る →（--apply）適用。"""
    c, s = _conn()
    imported = [] if skip_import else briefs_mod.import_all(c, s.dist_dir)
    since = "" if full or local else d1_mod.last_published(c)
    started = d1_mod.now_iso()
    sql = d1_mod.write(c, s.dist_dir, since=since)
    out = {"imported": [x["label"] for x in imported], "since": since or None, "d1_sql": str(sql),
           "statements": sql.read_text(encoding="utf-8").count(";\n")}
    if apply or local:
        d1_mod.apply_sql(sql, database, remote=not local)
        if not local:
            c.execute("INSERT INTO publish_log (at, applied) VALUES (?, 1)", (started,))
        out["applied"] = "local" if local else "remote"
    _print(out)


@app.command("mcp")
def mcp_cmd(transport: Annotated[str, typer.Option(help="stdio | http")] = "stdio",
            host: Annotated[str, typer.Option()] = "127.0.0.1", port: Annotated[int, typer.Option()] = 8765) -> None:
    """Claude Code 用の MCP サーバー（収集・ブリーフの手順・取り込み・依頼・反映）。要: uv sync --extra mcp"""
    from .mcp_server import main as mcp_main
    mcp_main(transport, host, port)


# ---- 変更依頼 ----
@requests_app.command("pull")
def requests_pull(database: Annotated[str, typer.Option(envvar="D1_DATABASE", help="D1 のデータベース名")] = "world-events",
                  local: Annotated[bool, typer.Option(help="wrangler dev のローカル D1 から読む")] = False,
                  apply: Annotated[bool, typer.Option(help="取り込んだらすぐ反映する（既定は晩の requests 段階で反映）")] = False) -> None:
    """閲覧 UI から D1 に積まれた依頼を正本に取り込む。結果は次の publish で D1 に返る。"""
    c, _ = _conn()
    n = ops_mod.import_change_requests(c, d1_mod.fetch_pending_requests(database, remote=not local))
    out = {"imported": n}
    if apply:
        out |= ops_mod.apply_change_requests(c)
    _print(out)


@requests_app.command("list")
def requests_list(status: Annotated[str | None, typer.Option(help="pending / applied / rejected")] = None) -> None:
    c, _ = _conn()
    _print(views.run(c, "requests.list", status=status))


@requests_app.command("kinds")
def requests_kinds() -> None:
    """依頼の種類と payload の形（contracts/change_requests.json）。"""
    _print({k.name: {"label": k.label, "fields": k.fields} for k in change_request_kinds().values()})


@requests_app.command("submit")
def requests_submit(kind: str, payload: Annotated[str, typer.Argument(help="JSON")],
                    apply: Annotated[bool, typer.Option(help="すぐ反映する")] = False) -> None:
    """依頼を積む。例: news requests submit source.disable '{"id":"x","reason":"重複"}' --apply"""
    c, _ = _conn()
    try:
        _print(ops_mod.submit_change_request(c, kind, json.loads(payload), apply_now=apply))
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e


# ---- 評価 ----
@eval_app.command("sample")
def eval_sample(task: str = "l1", n: int = 100) -> None:
    """収集済みの報道から評価用の入力を抜き出す。"""
    c, _ = _conn()
    typer.echo(f"{bench_mod.sample_from_reports(c, task, n)} 件追加")


@app.command("bench")
def bench(env: Annotated[str | None, typer.Option(help="local | prod（省略すると NEWS_ENV）")] = None,
          model: Annotated[str | None, typer.Option(help="モデル名（本番は Worker の論理名ではなく候補を試すとき）")] = None) -> None:
    """評価セットで L1 の JSON 成立率・作り話の率・速度・トークン数を測る。モデルやプロンプトを替えるときは必ず通す。
    採否は本番のモデルの成績で決める（ローカルは開発中の変更が良くなったかの目安）。"""
    c, s = _conn(env, model)
    _print({"env": s.env, "model": s.llm_model, **bench_mod.run_l1(c, OpenAICompatLLM(s))})


def main() -> None:  # pragma: no cover
    app(prog_name="news")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
