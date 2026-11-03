from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))


def now_utc() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_iso() -> str:
    return iso(now_utc())


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)


def jst_date(s: str | datetime) -> str:
    """「1 日」の区切りは日本時間の 0 時。UTC の時刻を JST の日付にする。"""
    dt = parse_iso(s) if isinstance(s, str) else s
    return dt.astimezone(JST).strftime("%Y-%m-%d")


def jst_month(s: str | datetime) -> str:
    return jst_date(s)[:7]
