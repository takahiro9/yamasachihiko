"""閲覧 UI（cloud/worker）・MCP・CLI が共有する契約（リポジトリ直下の contracts/）を読む。

- queries.sql: 名前付きの読み取りクエリ。D1 と Mac の SQLite の両方で同じ SQL を流す
- change_requests.json: 変更依頼の種類と payload の形。どの入口から来た依頼も同じ規則で検証する
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts"


@dataclass(frozen=True)
class Query:
    name: str
    sql: str
    params: tuple[str, ...] = ()
    json_cols: tuple[str, ...] = ()
    one: bool = False


def parse_queries(text: str) -> dict[str, Query]:
    out: dict[str, Query] = {}
    for block in re.split(r"^-- name:\s*", text, flags=re.M)[1:]:
        head, _, rest = block.partition("\n")
        name, params, json_cols, one, body = head.strip(), (), (), False, []
        for line in rest.splitlines():
            if m := re.match(r"^-- params:\s*(.*)$", line):
                params = tuple(p.strip() for p in m.group(1).split(",") if p.strip())
            elif m := re.match(r"^-- json:\s*(.*)$", line):
                json_cols = tuple(p.strip() for p in m.group(1).split(",") if p.strip())
            elif line.strip() == "-- one":
                one = True
            elif not line.startswith("--"):
                body.append(line)
        out[name] = Query(name, "\n".join(body).strip().rstrip(";"), params, json_cols, one)
    return out


@cache
def queries() -> dict[str, Query]:
    return parse_queries((CONTRACTS_DIR / "queries.sql").read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Kind:
    name: str
    label: str
    fields: dict[str, str] = field(default_factory=dict)


@cache
def change_request_kinds() -> dict[str, Kind]:
    raw = json.loads((CONTRACTS_DIR / "change_requests.json").read_text(encoding="utf-8"))["kinds"]
    return {k: Kind(k, v["label"], v["fields"]) for k, v in raw.items()}


def _type_ok(t: str, v) -> bool:
    if t == "string":
        return isinstance(v, str) and v != ""
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "string[]":
        return isinstance(v, list) and all(isinstance(x, str) for x in v)
    if t == "object":
        return isinstance(v, dict)
    raise ValueError(f"契約の型が不明: {t}")


def validate_change_request(kind: str, payload) -> dict:
    """形だけを見る（存在確認などの意味の検証は反映時に正本で行う）。余計な項目は落とす。"""
    k = change_request_kinds().get(kind)
    if not k:
        raise ValueError(f"未対応の依頼: {kind}")
    if not isinstance(payload, dict):
        raise ValueError("payload はオブジェクト")
    clean = {}
    for name, t in k.fields.items():
        optional = t.endswith("?")
        base = t.rstrip("?")
        v = payload.get(name)
        if v is None or v == "":
            if not optional:
                raise ValueError(f"{name} が必要")
            continue
        if not _type_ok(base, v):
            raise ValueError(f"{name} は {base}")
        clean[name] = v
    return clean
