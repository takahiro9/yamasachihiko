"""テーマの定義。範囲を書き換えると新しい版（theme_versions）ができる（§2.2 ④）。"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..db import tx
from ..timeutil import now_iso


class ThemeIn(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    kind: Literal["basic", "issue"]
    parent_id: str | None = None
    name: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    stance_axis: list[str] | None = None
    phases: list[str] | None = None

    @model_validator(mode="after")
    def _by_kind(self):
        if self.kind == "basic":
            if self.parent_id or self.stance_axis or self.phases:
                raise ValueError("基本テーマは親・論調の軸・フェーズを持たない")
        else:
            if not self.parent_id:
                raise ValueError("争点テーマには親の基本テーマが必要")
            if not self.stance_axis or not self.phases:
                raise ValueError("争点テーマには論調の軸とフェーズが必要")
        return self


def split_csv(s: str) -> list[str]:
    return [x.strip() for x in re.split(r"[,、]", s) if x.strip()]


def get_theme(conn: sqlite3.Connection, theme_id: str, version: int | None = None) -> dict | None:
    r = conn.execute(
        "SELECT t.id, t.kind, t.parent_id, t.is_fallback, t.current_version, v.version, v.name, v.scope,"
        " v.stance_axis, v.phases FROM themes t JOIN theme_versions v ON v.theme_id=t.id"
        " AND v.version = COALESCE(?, t.current_version) WHERE t.id=?",
        (version, theme_id),
    ).fetchone()
    if not r:
        return None
    d = dict(r)
    d["stance_axis"] = json.loads(d["stance_axis"]) if d["stance_axis"] else None
    d["phases"] = json.loads(d["phases"]) if d["phases"] else None
    return d


def list_themes(conn, kind: str | None = None) -> list[dict]:
    ids = [r["id"] for r in conn.execute("SELECT id FROM themes WHERE (? IS NULL OR kind=?) ORDER BY kind, id", (kind, kind))]
    return [get_theme(conn, i) for i in ids]


def add_theme(conn, t: ThemeIn, *, reason: str | None = None) -> dict:
    if get_theme(conn, t.id):
        raise ValueError(f"既に存在する: {t.id}")
    if t.kind == "issue":
        parent = get_theme(conn, t.parent_id)
        if not parent or parent["kind"] != "basic":
            raise ValueError("親は存在する基本テーマでなければならない")
    now = now_iso()
    with tx(conn):
        conn.execute("INSERT INTO themes (id,kind,parent_id,is_fallback,current_version,created_at,updated_at) VALUES (?,?,?,0,1,?,?)",
                     (t.id, t.kind, t.parent_id, now, now))
        _insert_version(conn, t.id, 1, t.name, t.scope, t.stance_axis, t.phases, reason or "add", now)
    return get_theme(conn, t.id)


def _insert_version(conn, theme_id, version, name, scope, stance_axis, phases, reason, now):
    conn.execute(
        "INSERT INTO theme_versions (theme_id,version,name,scope,stance_axis,phases,reason,created_at) VALUES (?,?,?,?,?,?,?,?)",
        (theme_id, version, name, scope,
         json.dumps(stance_axis, ensure_ascii=False) if stance_axis else None,
         json.dumps(phases, ensure_ascii=False) if phases else None, reason, now),
    )


def update_theme(conn, theme_id: str, *, name=None, scope=None, stance_axis=None, phases=None, reason=None) -> dict:
    cur = get_theme(conn, theme_id)
    if not cur:
        raise KeyError(theme_id)
    new = {
        "name": name or cur["name"], "scope": scope or cur["scope"],
        "stance_axis": stance_axis if stance_axis is not None else cur["stance_axis"],
        "phases": phases if phases is not None else cur["phases"],
    }
    if cur["kind"] == "basic" and (stance_axis or phases):
        raise ValueError("基本テーマに論調の軸・フェーズは付けられない")
    if all(new[k] == cur[k] for k in new):
        return cur
    v = cur["current_version"] + 1
    now = now_iso()
    with tx(conn):
        _insert_version(conn, theme_id, v, new["name"], new["scope"], new["stance_axis"], new["phases"], reason, now)
        conn.execute("UPDATE themes SET current_version=?, updated_at=? WHERE id=?", (v, now, theme_id))
    return get_theme(conn, theme_id)


def theme_history(conn, theme_id: str) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM theme_versions WHERE theme_id=? ORDER BY version", (theme_id,))]
