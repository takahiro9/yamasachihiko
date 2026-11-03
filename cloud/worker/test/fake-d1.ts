// node:sqlite で D1 の prepare/bind/all/first/run を真似る。migrations/shared をそのまま流すので D1 と同じスキーマになる。
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";

export const MIGRATIONS = join(import.meta.dirname, "..", "..", "..", "migrations", "shared");

export function fakeD1() {
  const db = new DatabaseSync(":memory:");
  db.exec("PRAGMA foreign_keys = ON");
  for (const f of readdirSync(MIGRATIONS).filter((f) => f.endsWith(".sql")).sort()) db.exec(readFileSync(join(MIGRATIONS, f), "utf-8"));
  const prepare = (sql: string) => {
    let args: unknown[] = [];
    const stmt = {
      bind: (...a: unknown[]) => { args = a; return stmt; },
      all: async () => ({ results: db.prepare(sql).all(...(args as never[])).map((r) => ({ ...r })), success: true, meta: {} }),
      first: async () => (db.prepare(sql).get(...(args as never[])) ?? null),
      run: async () => { db.prepare(sql).run(...(args as never[])); return { success: true, meta: {} }; },
    };
    return stmt;
  };
  return { d1: { prepare } as unknown as D1Database, raw: db };
}
