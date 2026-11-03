// contracts/ の読み方（Python 側の src/world_events/contracts.py と同じ規則）。
export interface ContractQuery { sql: string; params: string[]; json: string[]; one: boolean }
export type ContractQueries = Record<string, ContractQuery>;
export interface ContractKind { label: string; fields: Record<string, string> }
export type ContractKinds = Record<string, ContractKind>;

export class ContractError extends Error {}

function typeOk(t: string, v: unknown): boolean {
  switch (t) {
    case "string": return typeof v === "string" && v !== "";
    case "integer": return Number.isInteger(v);
    case "string[]": return Array.isArray(v) && v.every((x) => typeof x === "string");
    case "object": return typeof v === "object" && v !== null && !Array.isArray(v);
    default: throw new ContractError(`契約の型が不明: ${t}`);
  }
}

/** 形だけを見る（存在確認などは Mac 側の取り込み時に正本で行う）。余計な項目は落とす。 */
export function validateChangeRequest(kinds: ContractKinds, kind: unknown, payload: unknown): Record<string, unknown> {
  if (typeof kind !== "string" || !Object.hasOwn(kinds, kind)) throw new ContractError(`未対応の依頼: ${String(kind)}`);
  if (typeof payload !== "object" || payload === null || Array.isArray(payload)) throw new ContractError("payload はオブジェクト");
  const p = payload as Record<string, unknown>;
  const clean: Record<string, unknown> = {};
  for (const [name, t] of Object.entries(kinds[kind].fields)) {
    const optional = t.endsWith("?");
    const base = t.replace(/\?$/, "");
    const v = p[name];
    if (v === undefined || v === null || v === "") {
      if (!optional) throw new ContractError(`${name} が必要`);
      continue;
    }
    if (!typeOk(base, v)) throw new ContractError(`${name} は ${base}`);
    clean[name] = v;
  }
  return clean;
}

/** 名前付きクエリを D1 に流す。引数は名前で受け、契約の順に ?1, ?2… に入れる。 */
export async function runQuery(db: D1Database, queries: ContractQueries, name: string, params: Record<string, unknown> = {}) {
  const q = queries[name];
  if (!q) throw new ContractError(`クエリが無い: ${name}`);
  const unknown = Object.keys(params).filter((k) => !q.params.includes(k));
  if (unknown.length) throw new ContractError(`${name} に無い引数: ${unknown.join(", ")}`);
  const stmt = db.prepare(q.sql).bind(...q.params.map((p) => params[p] ?? null));
  const { results } = await stmt.all<Record<string, unknown>>();
  const rows = results.map((r) => decode(q, r));
  return q.one ? (rows[0] ?? null) : rows;
}

function decode(q: ContractQuery, row: Record<string, unknown>) {
  for (const c of q.json) {
    const v = row[c];
    if (typeof v === "string") {
      try { row[c] = JSON.parse(v); } catch { /* そのまま */ }
    }
  }
  return row;
}
