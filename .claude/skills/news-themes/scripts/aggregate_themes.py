"""これまでの簡易ブリーフ（dist/briefs/*/events.json・links.json）を集計し、テーマと急な動きの材料を作る
（news-themes スキル用。DB は読み取りのみ）。

uv run python .claude/skills/news-themes/scripts/aggregate_themes.py [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--out DIR]

- 対象期間（JST の日付。省略で最新のブリーフの期間）に重なるブリーフの出来事を、カテゴリ（基本テーマ）ごとにまとめる
- 急な動き: 報道数を カテゴリ × JST 日 と カテゴリ × 地域 × JST 日 で数え、直近 14 日（ブリーフで覆われた日だけ）の平均と比べる。
  基準はパイプラインの B6 と同じ（config.SPIKE_*）。覆われた過去の日が 7 日未満なら「参考」として出す
- 争点テーマ: DB の themes から現在の定義（範囲・論調の軸・フェーズ）を載せる
- 出力: DIR（省略で dist/themes/<ラベル>）/aggregate.md（Claude が読む）と aggregate.json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
from world_events import config  # noqa: E402
from world_events.regions import region_of  # noqa: E402

JST = timezone(timedelta(hours=9))
CATEGORIES = ["国際関係・外交", "紛争・安全保障", "政治・選挙", "経済・市場", "企業・産業", "テクノロジー・AI", "科学・宇宙",
              "気候・環境・エネルギー", "災害・事故", "健康・医療", "社会・事件", "エンタメ・カルチャー", "スポーツ", "その他"]
MIN_HISTORY_DAYS = 7
COVER_HOURS = 12      # その JST 日のうちこれだけブリーフの期間に入っていれば「覆われた日」とみなす
TOP_EVENTS = 8


def rel(p: Path) -> str:
    return str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)


def dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def jst_day(s: str) -> date:
    return dt(s).astimezone(JST).date()


def load_briefs(briefs_dir: Path) -> list[dict]:
    out = []
    for p in sorted(briefs_dir.glob("*/events.json")):
        ev = json.loads(p.read_text(encoding="utf-8"))
        lp = p.parent / "links.json"
        ev["links"] = json.loads(lp.read_text(encoding="utf-8"))["links"] if lp.exists() else []
        out.append(ev)
    return sorted(out, key=lambda b: b["until"])


def covered_days(briefs: list[dict]) -> set[date]:
    days: set[date] = set()
    for b in briefs:
        s, u = dt(b["since"]), dt(b["until"])
        d = s.astimezone(JST).date()
        while d <= u.astimezone(JST).date():
            lo = max(s, datetime(d.year, d.month, d.day, tzinfo=JST))
            hi = min(u, datetime(d.year, d.month, d.day, tzinfo=JST) + timedelta(days=1))
            if (hi - lo) >= timedelta(hours=COVER_HOURS):
                days.add(d)
            d += timedelta(days=1)
    return days


def report_meta(ids: list[str]) -> dict[str, dict]:
    conn = sqlite3.connect(f"file:{ROOT / 'data/news.db'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    out: dict[str, dict] = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = (f"SELECT id, country, COALESCE(published_at, fetched_at) AS at FROM reports"
             f" WHERE id IN ({','.join('?' * len(chunk))})")
        for r in conn.execute(q, chunk):
            out[r["id"]] = dict(r)
    return out


def issue_themes() -> list[dict]:
    conn = sqlite3.connect(f"file:{ROOT / 'data/news.db'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT t.id, t.kind, t.parent_id, v.name, v.scope, v.stance_axis, v.phases FROM themes t"
        " JOIN theme_versions v ON v.theme_id = t.id AND v.version = t.current_version ORDER BY t.kind, t.id").fetchall()
    names = {r["id"]: r["name"] for r in rows}
    return [{"id": r["id"], "parent": r["parent_id"], "parent_name": names.get(r["parent_id"]), "name": r["name"],
             "scope": r["scope"], "stance_axis": json.loads(r["stance_axis"] or "null"),
             "phases": json.loads(r["phases"] or "null")} for r in rows if r["kind"] == "issue"] + \
        [{"basic_ids": {r["name"]: r["id"] for r in rows if r["kind"] == "basic"}}]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="JST の日付 YYYY-MM-DD")
    ap.add_argument("--until", help="JST の日付 YYYY-MM-DD（その日を含む）")
    ap.add_argument("--briefs", default=str(ROOT / "dist/briefs"))
    ap.add_argument("--out")
    a = ap.parse_args()

    briefs = load_briefs(Path(a.briefs))
    if not briefs:
        sys.exit("events.json のあるブリーフがありません。先に news-brief を最後（finalize）まで実行してください")
    latest = briefs[-1]
    since = date.fromisoformat(a.since) if a.since else jst_day(latest["since"])
    until = date.fromisoformat(a.until) if a.until else jst_day(latest["until"])
    label = until.isoformat() if since == until else f"{since}_{until}"
    out = Path(a.out) if a.out else ROOT / "dist/themes" / label
    lo = datetime(since.year, since.month, since.day, tzinfo=JST)
    hi = datetime(until.year, until.month, until.day, tzinfo=JST) + timedelta(days=1)

    # 報道 → カテゴリ（同じ報道が複数のブリーフにあれば新しいブリーフの判定を使う）
    report_cats: dict[str, list[str]] = {}
    for b in briefs:
        for e in b["events"]:
            for r in e["report_ids"]:
                report_cats[r] = e["categories"]
    meta = report_meta(list(report_cats))
    covered = covered_days(briefs)

    daily: dict[tuple[str, str], Counter] = defaultdict(Counter)   # (カテゴリ, 地域 or "全体") → 日 → 報道数
    for r, cats in report_cats.items():
        m = meta.get(r)
        if not m:
            continue
        d, reg = jst_day(m["at"]), region_of(m["country"])
        for c in cats:
            daily[(c, "全体")][d] += 1
            daily[(c, reg)][d] += 1

    spikes = []
    target_days = sorted(d for d in covered if since <= d <= until)
    for (cat, reg), days in daily.items():
        for d in target_days:
            today = days.get(d, 0)
            hist = [x for x in (d - timedelta(days=i) for i in range(1, config.SPIKE_WINDOW_DAYS + 1)) if x in covered]
            if not hist or today < config.SPIKE_MIN_COUNT:
                continue
            base = sum(days.get(x, 0) for x in hist) / len(hist)
            if today > config.SPIKE_RATIO_BASIC * max(base, 1.0):
                spikes.append({"day": d.isoformat(), "category": cat, "region": reg, "count": today,
                               "baseline": round(base, 2), "ratio": round(today / max(base, 1.0), 1),
                               "history_days": len(hist), "provisional": len(hist) < MIN_HISTORY_DAYS})
    spikes.sort(key=lambda s: (s["provisional"], -s["ratio"]))

    # 対象期間の出来事（期間の重なるブリーフから。報道の過半が既出の出来事は重複として新しい方だけ残す）
    events, seen_reports = [], set()
    for b in reversed(briefs):
        if dt(b["until"]) < lo or dt(b["since"]) >= hi:
            continue
        for e in b["events"]:
            rs = set(e["report_ids"])
            if len(rs & seen_reports) * 2 > len(rs):
                continue
            seen_reports |= rs
            events.append({**e, "ref": f"{b['label']}/{e['id']}"})
    refs = {e["ref"] for e in events}
    links = [{**ln, "from": ln["from"] if "/" in ln["from"] else f"{b['label']}/{ln['from']}",
              "to": ln["to"] if "/" in ln["to"] else f"{b['label']}/{ln['to']}"}
             for b in briefs for ln in b["links"]]
    links = [ln for ln in links if ln["from"] in refs or ln["to"] in refs]
    by_ref = {e["ref"]: e for e in events}

    themes = []
    for cat in CATEGORIES:
        evs = sorted((e for e in events if cat in e["categories"]), key=lambda e: -e["importance"])
        if not evs:
            continue
        cat_refs = {e["ref"] for e in evs}
        regions: Counter = Counter()
        for e in evs:
            regions.update(e["regions"])
        themes.append({
            "category": cat, "events": len(evs), "primary_events": sum(1 for e in evs if e["categories"][0] == cat),
            "reports": sum(e["n_reports"] for e in evs), "regions": dict(regions.most_common()),
            "top": [{"ref": e["ref"], "title": e["title"], "summary": e["summary"], "n_reports": e["n_reports"],
                     "regions": list(e["regions"]), "importance": e["importance"]} for e in evs[:TOP_EVENTS]],
            "links": [ln for ln in links if ln["from"] in cat_refs or ln["to"] in cat_refs],
        })

    defs = issue_themes()
    basic_ids = defs.pop()["basic_ids"]
    result = {"label": label, "since": since.isoformat(), "until": until.isoformat(),
              "briefs": [b["label"] for b in briefs], "covered_days": sorted(d.isoformat() for d in covered),
              "events": len(events), "themes": themes, "spikes": spikes, "issue_themes": defs, "basic_ids": basic_ids}
    out.mkdir(parents=True, exist_ok=True)
    (out / "aggregate.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "aggregate.md").write_text(render(result, by_ref), encoding="utf-8")
    print(json.dumps({"out": rel(out), "events": len(events), "themes": len(themes), "spikes": len(spikes),
                      "provisional_spikes": sum(s["provisional"] for s in spikes),
                      "covered_days": len(covered), "issue_themes": len(defs)}, ensure_ascii=False, indent=2))


def render(r: dict, by_ref: dict) -> str:
    def t(ref: str) -> str:
        return by_ref[ref]["title"] if ref in by_ref else ref

    L = [f"# テーマ集計 {r['label']}（{r['since']}〜{r['until']} JST、出来事 {r['events']} 件）", "",
         f"- 使ったブリーフ: {', '.join(r['briefs'])}",
         f"- ブリーフで覆われた日: {len(r['covered_days'])} 日（{', '.join(r['covered_days'][:3])}{' …' if len(r['covered_days']) > 3 else ''}）",
         f"- 急な動きの基準: 直近 {config.SPIKE_WINDOW_DAYS} 日の平均の {config.SPIKE_RATIO_BASIC} 倍超・"
         f"{config.SPIKE_MIN_COUNT} 件以上。覆われた過去の日が {MIN_HISTORY_DAYS} 日未満なら「参考」", ""]
    L += ["## 基本テーマ（カテゴリ）ごと", ""]
    for th in r["themes"]:
        regs = "、".join(f"{k} {v}" for k, v in list(th["regions"].items())[:6])
        L += [f"### {th['category']}（出来事 {th['events']}・うち主分類 {th['primary_events']}、報道 {th['reports']}）",
              f"地域: {regs}", ""]
        for e in th["top"]:
            L.append(f"- {e['ref']}｜{e['n_reports']}本｜{'・'.join(e['regions'][:3])}｜{e['title']}: {e['summary']}")
        for ln in th["links"][:10]:
            L.append(f"  - つながり: {t(ln['from'])} —{ln['type']}→ {t(ln['to'])}（{ln.get('reason', '')}）")
        L.append("")
    L += ["## 急な動き", ""]
    if r["spikes"]:
        for s in r["spikes"]:
            L.append(f"- {'［参考］' if s['provisional'] else ''}{s['day']} {s['category']} × {s['region']}: "
                     f"{s['count']} 件（平常 {s['baseline']}、{s['ratio']} 倍、履歴 {s['history_days']} 日）")
    else:
        L.append("- なし（履歴が無い、または基準を超えたものが無い）")
    L += ["", "## 争点テーマ（DB の現在の定義）", ""]
    if r["issue_themes"]:
        for it in r["issue_themes"]:
            L.append(f"- {it['id']}「{it['name']}」（親: {it['parent_name']}）範囲: {it['scope']}｜"
                     f"論調: {it['stance_axis']}｜フェーズ: {it['phases']}")
    else:
        L.append("- まだ無い")
    L += ["", "基本テーマの id: " + "、".join(f"{k}={v}" for k, v in r["basic_ids"].items()), ""]
    return "\n".join(L)


if __name__ == "__main__":
    main()
