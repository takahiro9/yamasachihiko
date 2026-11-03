"""情報源（収集設定）。書き換えは必ず source_history を同時に書く（§3.5）。"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from ..db import tx
from ..timeutil import now_iso

Actor = Literal["cli", "web", "auto"]
SourceType = Literal["rss", "sitemap", "html_list", "gdelt_query"]

_COLUMNS = ("id", "type", "url", "media", "country", "language", "interval_days", "extractor", "query",
            "enabled", "disabled_reason", "note")


class SourceIn(BaseModel):
    id: str = Field(min_length=1)
    type: SourceType
    url: str = ""
    media: str = Field(min_length=1)
    country: str = Field(min_length=1)
    language: str = Field(min_length=1)
    interval_days: int = Field(default=1, ge=1)   # 1 日未満は入口でも DB でも弾く
    extractor: dict | None = None
    query: str | None = None
    note: str | None = None

    @field_validator("country")
    @classmethod
    def _country(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("language")
    @classmethod
    def _language(cls, v: str) -> str:
        return v.strip().lower()

    @model_validator(mode="after")
    def _by_type(self):
        if self.type == "gdelt_query":
            if not self.query:
                raise ValueError("gdelt_query には query が必要")
        elif not self.url:
            raise ValueError(f"{self.type} には url が必要")
        if self.type != "html_list" and self.extractor:
            raise ValueError("extractor は html_list のみ")
        if self.type == "html_list" and not (self.extractor and self.extractor.get("link_selector")):
            raise ValueError("html_list には extractor.link_selector が必要")
        return self


def _row(conn: sqlite3.Connection, source_id: str) -> dict | None:
    r = conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
    return dict(r) if r else None


def _history(conn, source_id, actor, action, before, after, reason):
    conn.execute(
        "INSERT INTO source_history (source_id, at, actor, action, before_json, after_json, reason) VALUES (?,?,?,?,?,?,?)",
        (source_id, now_iso(), actor, action,
         json.dumps(before, ensure_ascii=False) if before else None,
         json.dumps(after, ensure_ascii=False) if after else None, reason),
    )


def add_source(conn, src: SourceIn, *, actor: Actor = "cli", reason: str | None = None) -> dict:
    if _row(conn, src.id):
        raise ValueError(f"既に存在する: {src.id}")
    now = now_iso()
    with tx(conn):
        conn.execute(
            "INSERT INTO sources (id,type,url,media,country,language,interval_days,extractor,query,enabled,note,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,1,?,?,?)",
            (src.id, src.type, src.url, src.media, src.country, src.language, src.interval_days,
             json.dumps(src.extractor, ensure_ascii=False) if src.extractor else None, src.query, src.note, now, now),
        )
        conn.execute("INSERT INTO source_state (source_id) VALUES (?)", (src.id,))
        _history(conn, src.id, actor, "add", None, _row(conn, src.id), reason)
    return _row(conn, src.id)


_UPDATABLE = {"url", "media", "country", "language", "interval_days", "extractor", "query", "note"}


def update_source(conn, source_id: str, *, actor: Actor = "cli", reason: str | None = None, **changes) -> dict:
    before = _row(conn, source_id)
    if not before:
        raise KeyError(source_id)
    bad = set(changes) - _UPDATABLE
    if bad:
        raise ValueError(f"更新できない項目: {sorted(bad)}")
    if "interval_days" in changes and changes["interval_days"] < before["interval_days"]:
        raise ValueError("interval_days は延ばす方向にだけ変えられる")
    merged = {**before, **changes}
    if isinstance(merged.get("extractor"), str):
        merged["extractor"] = json.loads(merged["extractor"])
    SourceIn.model_validate({k: merged[k] for k in ("id", "type", "url", "media", "country", "language",
                                                     "interval_days", "extractor", "query", "note")})
    if isinstance(changes.get("extractor"), dict):
        changes["extractor"] = json.dumps(changes["extractor"], ensure_ascii=False)
    if "country" in changes:
        changes["country"] = changes["country"].strip().upper()
    if "language" in changes:
        changes["language"] = changes["language"].strip().lower()
    sets = ", ".join(f"{k}=?" for k in changes)
    with tx(conn):
        conn.execute(f"UPDATE sources SET {sets}, updated_at=? WHERE id=?", (*changes.values(), now_iso(), source_id))
        _history(conn, source_id, actor, "update", before, _row(conn, source_id), reason)
    return _row(conn, source_id)


def set_enabled(conn, source_id: str, enabled: bool, *, actor: Actor = "cli", reason: str | None = None) -> dict:
    before = _row(conn, source_id)
    if not before:
        raise KeyError(source_id)
    if bool(before["enabled"]) == enabled:
        return before
    with tx(conn):
        conn.execute(
            "UPDATE sources SET enabled=?, disabled_reason=?, updated_at=? WHERE id=?",
            (int(enabled), None if enabled else reason, now_iso(), source_id),
        )
        if enabled:
            conn.execute("UPDATE source_state SET consecutive_failures=0, last_error=NULL WHERE source_id=?", (source_id,))
        _history(conn, source_id, actor, "enable" if enabled else "disable", before, _row(conn, source_id), reason)
    return _row(conn, source_id)


def list_sources(conn, *, only_enabled: bool = False) -> list[dict]:
    q = ("SELECT s.*, st.last_fetched_at, st.consecutive_failures, st.last_item_count, st.feed_cap_hits "
         "FROM sources s LEFT JOIN source_state st ON st.source_id = s.id")
    if only_enabled:
        q += " WHERE s.enabled = 1"
    return [dict(r) for r in conn.execute(q + " ORDER BY s.country, s.media, s.id")]


def history(conn, source_id: str) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM source_history WHERE source_id=? ORDER BY id", (source_id,))]


def import_sources(conn, text: str, fmt: Literal["json", "csv"], *, actor: Actor = "cli") -> tuple[int, list[str]]:
    """まとめて取り込む。既存の id は無視（更新は update で）。(追加数, エラー) を返す。"""
    if fmt == "json":
        rows = json.loads(text)
    else:
        rows = list(csv.DictReader(io.StringIO(text)))
    added, errors = 0, []
    for i, r in enumerate(rows):
        r = {k: v for k, v in r.items() if v not in (None, "")}
        if isinstance(r.get("extractor"), str):
            r["extractor"] = json.loads(r["extractor"])
        try:
            add_source(conn, SourceIn.model_validate(r), actor=actor, reason="import")
            added += 1
        except Exception as e:  # 1 件の不備で全体は止めない
            errors.append(f"#{i} {r.get('id', '?')}: {e}")
    return added, errors


def export_sources(conn, fmt: Literal["json", "csv"]) -> str:
    rows = [{k: r[k] for k in _COLUMNS} for r in list_sources(conn)]
    if fmt == "json":
        return json.dumps(rows, ensure_ascii=False, indent=2)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=_COLUMNS)
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()
