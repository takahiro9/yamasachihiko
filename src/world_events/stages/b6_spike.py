"""B6 急な動きを検知する。テーマごとの 1 日あたりの報道量が直近の平均を大きく超えたら記録する。"""
from __future__ import annotations

from datetime import date, timedelta

from .. import config

# 報道の日付は取得した時刻（fetched_at）の JST 日付で数える
_DAILY = ("SELECT et.theme_id, {region} AS region, date(r.fetched_at, '+9 hours') AS d, COUNT(*) AS n"
          " FROM event_themes et JOIN themes t ON t.id=et.theme_id AND t.is_fallback=0"
          " JOIN events e ON e.id=et.event_id JOIN event_reports er ON er.event_id=e.id JOIN reports r ON r.id=er.report_id"
          " WHERE date(r.fetched_at, '+9 hours') BETWEEN ? AND ? {extra} GROUP BY 1, 2, 3")


def detect(conn, target_date: str) -> list[dict]:
    d = date.fromisoformat(target_date)
    start = (d - timedelta(days=config.SPIKE_WINDOW_DAYS)).isoformat()
    first = conn.execute("SELECT MIN(date(fetched_at, '+9 hours')) FROM reports").fetchone()[0]
    # 履歴が 7 日に満たないうちは平常時の量が分からないので検知しない
    if not first or date.fromisoformat(first) > d - timedelta(days=7):
        return []
    kinds = {t["id"]: t["kind"] for t in conn.execute("SELECT id, kind FROM themes")}
    series: dict[tuple[str, str], dict[str, int]] = {}
    for sql in (_DAILY.format(region="''", extra=""),
                _DAILY.format(region="COALESCE(e.region, 'その他')", extra="AND t.kind='basic'")):
        for r in conn.execute(sql, (start, target_date)):
            series.setdefault((r["theme_id"], r["region"]), {})[r["d"]] = r["n"]
    out = []
    for (theme_id, region), days in series.items():
        today = days.get(target_date, 0)
        base = sum(days.get((d - timedelta(days=i)).isoformat(), 0) for i in range(1, config.SPIKE_WINDOW_DAYS + 1)) / config.SPIKE_WINDOW_DAYS
        ratio = config.SPIKE_RATIO_BASIC if kinds[theme_id] == "basic" else config.SPIKE_RATIO_ISSUE
        if today >= config.SPIKE_MIN_COUNT and today > ratio * max(base, 1.0):
            out.append({"date": target_date, "theme_id": theme_id, "region": region, "count": today, "baseline": round(base, 2)})
    return sorted(out, key=lambda s: s["count"] / max(s["baseline"], 1.0), reverse=True)


def run(conn, target_date: str) -> dict:
    spikes = detect(conn, target_date)
    conn.execute("BEGIN")
    conn.execute("DELETE FROM spikes WHERE date=?", (target_date,))
    for s in spikes:
        conn.execute("INSERT INTO spikes VALUES (:date,:theme_id,:region,:count,:baseline)", s)
    conn.execute("COMMIT")
    return {"spikes": len(spikes)}
