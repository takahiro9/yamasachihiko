from __future__ import annotations

import sqlite3
from pathlib import Path

from .timeutil import now_iso


def connect(path: str | Path) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None)  # 明示的に BEGIN する
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def migrate(conn: sqlite3.Connection, migrations_dir: Path, scopes: tuple[str, ...] = ("shared", "local")) -> list[str]:
    """番号付き SQL を順に適用する。適用済みは飛ばす。適用した名前を返す。"""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " scope TEXT NOT NULL, name TEXT NOT NULL, applied_at TEXT NOT NULL, PRIMARY KEY (scope, name))"
    )
    applied: list[str] = []
    for scope in scopes:
        for f in sorted((migrations_dir / scope).glob("*.sql")):
            if conn.execute("SELECT 1 FROM schema_migrations WHERE scope=? AND name=?", (scope, f.name)).fetchone():
                continue
            conn.executescript("BEGIN;\n" + f.read_text(encoding="utf-8") + "\nCOMMIT;")
            conn.execute("INSERT INTO schema_migrations VALUES (?,?,?)", (scope, f.name, now_iso()))
            applied.append(f"{scope}/{f.name}")
    return applied


class Tx:
    """with db.tx(conn): ... で 1 つのトランザクションにまとめる。"""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("BEGIN")
        return self.conn

    def __exit__(self, exc_type, *_):
        self.conn.execute("ROLLBACK" if exc_type else "COMMIT")
        return False


def tx(conn: sqlite3.Connection) -> Tx:
    return Tx(conn)
