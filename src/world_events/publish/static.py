"""静的ページを生成する（Cloudflare Pages に置く）。ページは日ごとだけ。出来事ごとのページは作らない（ファイル数の上限のため）。"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

_ENV = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=select_autoescape(["html", "j2"]))


def render(conn, dist: Path) -> list[Path]:
    (dist / "digest").mkdir(parents=True, exist_ok=True)
    tpl = _ENV.get_template("digest.html.j2")
    written = []
    rows = conn.execute("SELECT date, body FROM digests ORDER BY date DESC").fetchall()
    for r in rows:
        p = dist / "digest" / f"{r['date']}.html"
        p.write_text(tpl.render(d=json.loads(r["body"])), encoding="utf-8")
        written.append(p)
    if rows:
        latest = dist / "index.html"
        # 最新ページは相対リンクが digest/ 配下前提なので、index にだけ書き直す
        latest.write_text(tpl.render(d=json.loads(rows[0]["body"])).replace('href="../index.html"', 'href="index.html"'), encoding="utf-8")
        written.append(latest)
    return written


def clean(dist: Path) -> None:
    shutil.rmtree(dist, ignore_errors=True)
