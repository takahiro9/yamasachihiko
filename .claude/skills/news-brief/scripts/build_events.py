"""地域ごとの出来事を検証・統合し、つながりを付けてブリーフを組み立てる（news-brief スキル用。DB は読み取りのみ）。

uv run python .claude/skills/news-brief/scripts/build_events.py index    <DIR> [--prev DIR]
uv run python .claude/skills/news-brief/scripts/build_events.py finalize <DIR>

index:    events/<地域>.json（サブエージェントの出力）を検証し、メインセッションが読む events_index.md を書く。
          出来事は「<地域>-<番号>」のキーで並べ、地域をまたいで同じ出来事らしい組を「統合の候補」として添える。
          --prev に前のブリーフを渡すと、その主な出来事も載せる（続報などのつながりを前の出来事に張るため）。
finalize: curation.json（統合とつながり）と overview.md（全体の要点・カテゴリ別の横断）を読み、
          events.json・links.json・brief.md を書く。

curation.json の形（メインセッションが書く）:
{
  "merge": [["中東-3", "欧州-12"], {"members": ["北米-1", "欧州-2"], "title": "…", "summary": "…"}],
  "links": [{"from": "中東-3", "to": "中東-7", "type": "反応", "reason": "…", "confidence": 0.7},
            {"from": "2026-11-01/E0004", "to": "欧州-5", "type": "続報", "reason": "…", "confidence": 0.8}]
}
- link の端は、このブリーフの「<地域>-<番号>」か、前のブリーフの「<ラベル>/<出来事 id>」
- 種類は 原因 / 反応 / 続報 / 激化 / 収束。from → to の向き（原因 → 結果、元の出来事 → 続報・反応）
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
from world_events.regions import REGIONS, region_of  # noqa: E402

CATEGORIES = ["国際関係・外交", "紛争・安全保障", "政治・選挙", "経済・市場", "企業・産業", "テクノロジー・AI", "科学・宇宙",
              "気候・環境・エネルギー", "災害・事故", "健康・医療", "社会・事件", "エンタメ・カルチャー", "スポーツ", "その他"]
LINK_TYPES = {"原因", "反応", "続報", "激化", "収束"}
REGION_ORDER = list(REGIONS) + ["その他"]
TOP_N = 10            # 主な出来事の数
TOP_PER_REGION = 3    # 主な出来事のうち同じ地域から出す上限
PER_CATEGORY = 6      # 地域別で 1 カテゴリに並べる出来事の数（残りは「ほか」）


def rel(p: Path) -> str:
    return str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)


def load_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def report_meta(ids: list[str]) -> dict[str, dict]:
    conn = sqlite3.connect(f"file:{ROOT / 'data/news.db'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    out: dict[str, dict] = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = f"SELECT id, media, country, language, COALESCE(published_at, fetched_at) AS at FROM reports WHERE id IN ({','.join('?' * len(chunk))})"
        for r in conn.execute(q, chunk):
            out[r["id"]] = dict(r)
    return out


def load_region_events(d: Path) -> tuple[dict, list[dict], list[str]]:
    """events/<地域>.json を読み、キー・report id・媒体などを補った地域の出来事を返す。"""
    manifest = load_json(d / "manifest.json")
    warnings: list[str] = []
    region_points: dict[str, str] = {}
    events: list[dict] = []
    for reg in REGION_ORDER:
        count = manifest["regions"].get(reg, {}).get("count", 0)
        if not count:
            continue
        p = d / "events" / f"{reg}.json"
        if not p.exists():
            warnings.append(f"{reg}: events/{reg}.json がありません（{count} 件が未処理）")
            continue
        data = load_json(p)
        ids = load_json(d / "regions" / f"{reg}.ids.json")
        region_points[reg] = (data.get("points") or "").strip()
        used: set[int] = set()
        for n, ev in enumerate(data.get("events", []), 1):
            key = f"{reg}-{n}"
            nums = []
            for x in ev.get("reports", []):
                try:
                    x = int(x)
                except (TypeError, ValueError):
                    warnings.append(f"{key}: 報道番号 {x!r} が数値でない")
                    continue
                if not 1 <= x <= len(ids):
                    warnings.append(f"{key}: 報道番号 {x} が範囲外（1〜{len(ids)}）")
                elif x in used:
                    warnings.append(f"{key}: 報道番号 {x} は別の出来事に使われている（先の出来事に残す）")
                else:
                    used.add(x)
                    nums.append(x)
            if not nums:
                warnings.append(f"{key}: 有効な報道が無いので捨てた（{ev.get('title', '')[:30]}）")
                continue
            cats = [c for c in ev.get("categories", []) if c in CATEGORIES]
            bad = [c for c in ev.get("categories", []) if c not in CATEGORIES]
            if bad:
                warnings.append(f"{key}: 不明なカテゴリ {bad} を外した")
            events.append({
                "key": key, "region": reg, "title": (ev.get("title") or "").strip(),
                "summary": (ev.get("summary") or "").strip(), "when": ev.get("when") or "",
                "where": ev.get("where") or "", "who": ev.get("who") or [],
                "categories": cats or ["その他"], "about": ev.get("about") or "",
                "report_ids": [ids[x - 1] for x in nums],
            })
        missing = count - len(used)
        if missing:
            warnings.append(f"{reg}: {count} 件中 {missing} 件はどの出来事にも入っていない（判断できない記事として省かれた）")
    meta = report_meta([r for e in events for r in e["report_ids"]])
    for e in events:
        ms = [meta[r] for r in e["report_ids"] if r in meta]
        e["media"] = sorted({m["media"] for m in ms})
    return {"manifest": manifest, "points": region_points}, events, warnings


def bigrams(s: str) -> set[str]:
    s = re.sub(r"[\s、。・「」（）()［］\[\],.:：/]+", "", s)
    return {s[i:i + 2] for i in range(len(s) - 1)}


def merge_candidates(events: list[dict], limit: int = 150) -> list[tuple[float, dict, dict]]:
    """地域をまたいで、見出し・場所・人物の文字の重なりが大きい組（同じ出来事かもしれない）。"""
    grams = [(e, bigrams(e["title"] + e["where"] + "".join(e["who"]))) for e in events]
    out = []
    for i, (a, ga) in enumerate(grams):
        for b, gb in grams[i + 1:]:
            if a["region"] == b["region"] or not ga or not gb:
                continue
            if not set(a["categories"]) & set(b["categories"]):
                continue
            j = len(ga & gb) / len(ga | gb)
            if j >= 0.25:
                out.append((j, a, b))
    out.sort(key=lambda t: -t[0])
    return out[:limit]


def cmd_index(d: Path, prev: Path | None) -> None:
    info, events, warnings = load_region_events(d)
    lines = [f"# 出来事の一覧（{d.name}、地域の出来事 {len(events)} 件）", "",
             "書式: キー｜報道数・媒体数｜カテゴリ｜見出し（対象）", ""]
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        by_cat[e["categories"][0]].append(e)
    for cat in CATEGORIES:
        evs = sorted(by_cat.get(cat, []), key=lambda e: (-len(e["report_ids"]), e["key"]))
        if not evs:
            continue
        lines += [f"## {cat}（{len(evs)}）"]
        for e in evs:
            other = "・".join(e["categories"][1:])
            lines.append(f"- {e['key']}｜{len(e['report_ids'])}本・{len(e['media'])}媒体｜{other or '-'}｜{e['title']}"
                         + (f"（{e['about']}）" if e["about"] else ""))
        lines.append("")
    cands = merge_candidates(events)
    lines += ["## 統合の候補（地域をまたいで似ている組。同じ出来事なら curation.json の merge に入れる）"]
    lines += [f"- {a['key']} ⇔ {b['key']}（{j:.2f}）{a['title']} ／ {b['title']}" for j, a, b in cands] or ["- なし"]
    lines.append("")
    if prev:
        pe = load_json(prev / "events.json")
        lines += [f"## 前のブリーフの主な出来事（{pe['label']}。つながりの端には「{pe['label']}/<id>」を使う）"]
        for e in sorted(pe["events"], key=lambda e: -e["importance"])[:60]:
            lines.append(f"- {pe['label']}/{e['id']}｜{e['categories'][0]}｜{e['title']}")
        lines.append("")
    if warnings:
        lines += ["## 検証の警告"] + [f"- {w}" for w in warnings] + [""]
    (d / "events_index.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"index": rel(d / "events_index.md"), "region_events": len(events),
                      "merge_candidates": len(cands), "warnings": len(warnings)}, ensure_ascii=False, indent=2))
    for w in warnings[:30]:
        print("WARN", w, file=sys.stderr)


def cmd_finalize(d: Path) -> None:
    info, events, warnings = load_region_events(d)
    manifest = info["manifest"]
    cur_p = d / "curation.json"
    cur = load_json(cur_p) if cur_p.exists() else {"merge": [], "links": []}
    by_key = {e["key"]: e for e in events}

    # 統合: 地域の出来事 → 出来事（報道の多いメンバーの見出し・要約を代表にする。指定があればそれを使う）
    owner: dict[str, int] = {}
    groups: list[dict] = []
    for m in cur.get("merge", []):
        spec = m if isinstance(m, dict) else {"members": m}
        keys = [k for k in spec["members"] if k in by_key and k not in owner]
        for k in spec["members"]:
            if k not in by_key:
                warnings.append(f"merge: {k} が見つからない")
        if len(keys) < 2:
            continue
        for k in keys:
            owner[k] = len(groups)
        groups.append({"keys": keys, "title": spec.get("title"), "summary": spec.get("summary")})
    for e in events:
        if e["key"] not in owner:
            owner[e["key"]] = len(groups)
            groups.append({"keys": [e["key"]], "title": None, "summary": None})

    meta = report_meta([r for e in events for r in e["report_ids"]])
    merged: list[dict] = []
    for g in groups:
        mem = [by_key[k] for k in g["keys"]]
        rep = max(mem, key=lambda e: len(e["report_ids"]))
        rids = [r for e in mem for r in e["report_ids"]]
        ms = [meta[r] for r in rids if r in meta]
        cats = list(dict.fromkeys(c for e in [rep] + mem for c in e["categories"]))
        regions = Counter(region_of(m["country"]) for m in ms)
        times = sorted(m["at"] for m in ms)
        merged.append({
            "title": g["title"] or rep["title"], "summary": g["summary"] or rep["summary"],
            "when": rep["when"], "where": rep["where"], "who": rep["who"], "about": rep["about"],
            "categories": cats, "members": g["keys"],
            "region": regions.most_common(1)[0][0] if regions else rep["region"],
            "regions": dict(regions), "report_ids": rids, "n_reports": len(rids),
            "media": sorted({m["media"] for m in ms}), "countries": sorted({m["country"] for m in ms}),
            "languages": sorted({m["language"] for m in ms}),
            "first_seen": times[0] if times else None, "last_seen": times[-1] if times else None,
        })

    # つながり（端を出来事に解決。前のブリーフの出来事はそのまま参照として残す）
    def resolve(k: str) -> int | str | None:
        if k in owner:
            return owner[k]
        if re.match(r"^[\d_-]+/E\d+$", k):
            return k
        return None

    links, seen = [], set()
    n_links: Counter = Counter()
    for ln in cur.get("links", []):
        a, b, t = resolve(ln.get("from", "")), resolve(ln.get("to", "")), ln.get("type")
        if a is None or b is None or t not in LINK_TYPES or a == b or (isinstance(a, str) and isinstance(b, str)):
            warnings.append(f"link を捨てた: {ln}")
            continue
        if (a, b, t) in seen:
            continue
        seen.add((a, b, t))
        links.append({"from": a, "to": b, "type": t, "reason": ln.get("reason", ""),
                      "confidence": float(ln.get("confidence", 0.5))})
        for x in (a, b):
            if isinstance(x, int):
                n_links[x] += 1

    # 重要度 = 報道の多さ × 多様さ（媒体・国・言語の数の平均）× つながりの多さ（README の式の簡易版）
    for i, e in enumerate(merged):
        diversity = (len(e["media"]) + len(e["countries"]) + len(e["languages"])) / 3
        e["importance"] = round(e["n_reports"] * diversity * (1 + 0.25 * n_links[i]), 2)
    order = sorted(range(len(merged)), key=lambda i: (-merged[i]["importance"], merged[i]["members"][0]))
    new_id = {old: f"E{n:04d}" for n, old in enumerate(order, 1)}
    for old, e in enumerate(merged):
        e["id"] = new_id[old]
    for ln in links:
        for side in ("from", "to"):
            if isinstance(ln[side], int):
                ln[side] = new_id[ln[side]]
    events_out = [merged[i] for i in order]
    key_to_id = {k: new_id[owner[k]] for k in owner}

    label = manifest.get("label") or d.name
    (d / "events.json").write_text(json.dumps(
        {"label": label, "since": manifest["since"], "until": manifest["until"], "events": events_out,
         "region_event_ids": key_to_id}, ensure_ascii=False, indent=1), encoding="utf-8")
    (d / "links.json").write_text(json.dumps({"label": label, "links": links}, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
    (d / "brief.md").write_text(render(d, manifest, info["points"], events, events_out, links, key_to_id),
                                encoding="utf-8")
    print(json.dumps({"brief": rel(d / "brief.md"), "events": len(events_out),
                      "merged_groups": sum(1 for e in events_out if len(e["members"]) > 1),
                      "links": len(links), "warnings": len(warnings)}, ensure_ascii=False, indent=2))
    for w in warnings[:30]:
        print("WARN", w, file=sys.stderr)


def media_str(media: list[str], n: int = 5) -> str:
    return "［" + ", ".join(media[:n]) + (f" ほか {len(media) - n}" if len(media) > n else "") + "］"


def render(d: Path, manifest: dict, points: dict, region_events: list[dict], events: list[dict],
           links: list[dict], key_to_id: dict[str, str]) -> str:
    by_id = {e["id"]: e for e in events}

    prev_titles: dict[str, str] = {}
    for x in {ln[s] for ln in links for s in ("from", "to")} - set(by_id):
        p = d.parent / x.split("/")[0] / "events.json"
        if p.exists() and x not in prev_titles:
            prev_titles.update({f"{x.split('/')[0]}/{e['id']}": e["title"] for e in load_json(p)["events"]})

    def title_of(x: str) -> str:
        return by_id.get(x, {}).get("title") or f"{prev_titles.get(x, '前のブリーフの出来事')}（{x.split('/')[0]}）"

    label = manifest.get("label") or d.name
    out = [f"# ワールドブリーフ {label}（対象: {manifest['since']}〜{manifest['until']} UTC、"
           f"{manifest['total']} 件、出来事 {len(events)} 件、つながり {len(links)} 件）", ""]
    ov = d / "overview.md"
    out += [ov.read_text(encoding="utf-8").strip(), ""] if ov.exists() else ["（overview.md がまだありません）", ""]

    # 主な出来事: 重要度順、同じ地域は TOP_PER_REGION まで、溢れた枠は他地域の上位で埋める
    top, per_region = [], Counter()
    for e in events:
        if len(top) >= TOP_N:
            break
        if per_region[e["region"]] < TOP_PER_REGION:
            top.append(e)
            per_region[e["region"]] += 1
    rel: dict[str, list[str]] = defaultdict(list)
    for ln in links:
        rel[ln["to"]].append(f"← {title_of(ln['from'])}（{ln['from']}）の{ln['type']}")
        rel[ln["from"]].append(f"→ {ln['type']}: {title_of(ln['to'])}（{ln['to']}）")
    out += ["## 主な出来事", ""]
    for n, e in enumerate(top, 1):
        regs = "・".join(r for r, _ in Counter(e["regions"]).most_common(3))
        out.append(f"{n}. **{e['title']}**（{e['id']}｜{' / '.join(e['categories'][:2])}｜{regs}｜"
                   f"報道 {e['n_reports']} 本・{len(e['media'])} 媒体・{len(e['countries'])} か国）")
        out.append(f"   {e['summary']} {media_str(e['media'])}")
        for r in rel.get(e["id"], [])[:3]:
            out.append(f"   - {r}")
    out.append("")

    out += ["## つながり", ""]
    if links:
        for ln in sorted(links, key=lambda l: -l["confidence"]):
            out.append(f"- {title_of(ln['from'])}（{ln['from']}）—**{ln['type']}**→ {title_of(ln['to'])}（{ln['to']}）"
                       f": {ln['reason']}（確からしさ {ln['confidence']:.1f}）")
    else:
        out.append("- なし")
    out.append("")

    out += ["## 地域別", ""]
    by_region: dict[str, list[dict]] = defaultdict(list)
    for e in region_events:
        by_region[e["region"]].append(e)
    for reg in REGION_ORDER:
        evs = by_region.get(reg)
        if not evs:
            continue
        out += [f"### {reg}（{manifest['regions'][reg]['count']} 件、出来事 {len(evs)} 件）"]
        if points.get(reg):
            out.append(f"**この地域の要点**: {points[reg]}")
        out.append("")
        by_cat: dict[str, list[dict]] = defaultdict(list)
        for e in evs:
            by_cat[e["categories"][0]].append(e)
        for cat in CATEGORIES:
            ce = sorted(by_cat.get(cat, []), key=lambda e: (-len(e["media"]), -len(e["report_ids"])))
            if not ce:
                continue
            out.append(f"#### {cat}")
            for e in ce[:PER_CATEGORY]:
                about = f"（{e['about']}）" if e["about"] else ""
                out.append(f"- {e['summary'] or e['title']}{media_str(e['media'])}{about}")
            if len(ce) > PER_CATEGORY:
                out.append("- ほか: " + "／".join(f"{e['title']}{media_str(e['media'], 2)}" for e in ce[PER_CATEGORY:]))
            out.append("")

    empty = [r for r in REGION_ORDER if not manifest["regions"].get(r, {}).get("count")]
    out += ["## この要約について", "",
            "- 地域ごとの件数: " + "、".join(f"{r} {v['count']}" for r, v in manifest["regions"].items() if v["count"]),
            f"- 空だった地域: {'、'.join(empty) or 'なし'}（情報源が無い、または収集の上限で取れていない）",
            "- 出来事・つながり・カテゴリは、見出しと抜粋だけから Claude がまとめたもの（パイプラインの B2・B4 の結果ではない）。"
            "事実関係は元記事で確認すること",
            "- 機械可読な結果: events.json（出来事と report id）、links.json（つながり）", ""]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["index", "finalize"])
    ap.add_argument("dir", help="ブリーフのディレクトリ（例 dist/briefs/2026-11-02）")
    ap.add_argument("--prev", help="index: 前のブリーフのディレクトリ（events.json があるもの）")
    a = ap.parse_args()
    d = Path(a.dir) if Path(a.dir).is_absolute() else ROOT / a.dir
    if a.cmd == "index":
        prev = (Path(a.prev) if Path(a.prev).is_absolute() else ROOT / a.prev) if a.prev else None
        cmd_index(d, prev)
    else:
        cmd_finalize(d)


if __name__ == "__main__":
    main()
