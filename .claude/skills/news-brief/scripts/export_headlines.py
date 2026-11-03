"""指定した期間の報道を、地域ごとに見出しと抜粋の Markdown に書き出す（news-brief スキル用。DB は読み取りのみ）。

uv run python .claude/skills/news-brief/scripts/export_headlines.py [--since S] [--until U] [--hours H] [--out DIR]

期間の指定（S / U は JST の日付 YYYY-MM-DD か、UTC の ISO 時刻 2026-11-02T15:00:00Z）
- --since 2026-11-01 --until 2026-11-02 … JST の 11/1 0:00 から 11/2 の終わりまで（日付の until はその日を含む）
- --since 2026-11-01                     … JST の 11/1 0:00 から DB の最新の収集日時まで
- --hours 48                             … until（省略で DB の最新の収集日時）から 48 時間さかのぼる（既定 24）

- 対象: 公開日時（無ければ収集日時）が [since, until] の報道
- 出力（DIR を省略すると dist/briefs/<ラベル>。ラベルは 1 日以内なら JST の日付、それより長ければ <開始日>_<終了日>）:
  - regions/<地域>.md       … 「番号. [媒体 / 国 / 言語] 見出し — 抜粋」の一覧（サブエージェントの入力）
  - regions/<地域>.ids.json … 番号 → report id の対応（build_events.py が使う）
  - manifest.json           … 期間・地域ごとの件数と文字数
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
from world_events.regions import REGIONS, region_of  # noqa: E402

EXCERPT_CHARS = 200
JST = timezone(timedelta(hours=9))
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def rel(p: Path) -> str:
    return str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)


def clean(s: str | None, n: int | None = None) -> str:
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    s = re.sub(r"\s+", " ", s).strip()
    return s if n is None or len(s) <= n else s[:n] + "…"


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_point(s: str, *, end: bool) -> datetime:
    """JST の日付なら、開始はその日の 0:00、終了は翌日 0:00 の 1 秒前。ISO はそのまま（タイムゾーン無しは UTC）。"""
    if DATE_RE.match(s):
        d = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=JST)
        return d + timedelta(days=1, seconds=-1) if end else d
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def label_of(since: datetime, until: datetime) -> str:
    a, b = since.astimezone(JST).date(), until.astimezone(JST).date()
    if until - since <= timedelta(hours=24) or a == b:
        return b.isoformat()
    return f"{a.isoformat()}_{b.isoformat()}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data/news.db"))
    ap.add_argument("--since", help="開始。JST の日付 YYYY-MM-DD か UTC ISO")
    ap.add_argument("--until", help="終了。JST の日付（その日を含む）か UTC ISO。省略で DB の最新の収集日時")
    ap.add_argument("--hours", type=int, default=24, help="--since が無いときの期間（時間）")
    ap.add_argument("--out", help="出力先。省略で dist/briefs/<ラベル>")
    a = ap.parse_args()

    conn = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    if a.until:
        until_dt = parse_point(a.until, end=True)
    else:
        latest = conn.execute("SELECT MAX(fetched_at) FROM reports").fetchone()[0]
        if not latest:
            sys.exit("reports が空です。先に収集してください")
        until_dt = parse_point(latest, end=True)
    since_dt = parse_point(a.since, end=False) if a.since else until_dt - timedelta(hours=a.hours)
    if since_dt >= until_dt:
        sys.exit(f"期間が空です: {iso(since_dt)} 〜 {iso(until_dt)}")
    since, until = iso(since_dt), iso(until_dt)
    label = label_of(since_dt, until_dt)
    out = Path(a.out) if a.out else ROOT / "dist/briefs" / label

    rows = conn.execute(
        "SELECT id, media, country, language, title, excerpt, COALESCE(published_at, fetched_at) AS at FROM reports"
        " WHERE COALESCE(published_at, fetched_at) BETWEEN ? AND ? ORDER BY at DESC",
        (since, until),
    ).fetchall()
    if not rows:
        sys.exit(f"{since} 〜 {until} の報道がありません（収集済みの範囲を確認してください）")

    by_region: dict[str, list] = defaultdict(list)
    seen: set[str] = set()
    for r in rows:
        key = clean(r["title"]).lower()
        if key in seen:   # 同じ見出しの転載は 1 本に
            continue
        seen.add(key)
        by_region[region_of(r["country"])].append(r)

    (out / "regions").mkdir(parents=True, exist_ok=True)
    manifest = {"label": label, "since": since, "until": until, "total": 0, "regions": {}}
    for reg in list(REGIONS) + ["その他"]:
        items = by_region.get(reg, [])
        lines = [f"# {reg}（{since} 〜 {until}、{len(items)} 件）", ""]
        for i, r in enumerate(items, 1):
            ex = clean(r["excerpt"], EXCERPT_CHARS)
            lines.append(f"{i}. [{r['media']} / {r['country']} / {r['language']}] {clean(r['title'])}"
                         + (f" — {ex}" if ex and ex.lower() != clean(r['title']).lower() else ""))
        text = "\n".join(lines) + "\n"
        path = out / "regions" / f"{reg}.md"
        path.write_text(text, encoding="utf-8")
        (out / "regions" / f"{reg}.ids.json").write_text(json.dumps([r["id"] for r in items]), encoding="utf-8")
        manifest["regions"][reg] = {"count": len(items), "chars": len(text), "file": rel(path)}
        manifest["total"] += len(items)
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"out": rel(out), **manifest}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
