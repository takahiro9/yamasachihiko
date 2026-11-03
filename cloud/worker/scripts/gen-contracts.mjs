// contracts/（リポジトリ直下）から src/contracts.gen.ts を作る。Python 側（src/world_events/contracts.py）と同じ規則で読む。
// 生成物は git に入れる（deploy に余計な手順を要らなくするため）。差分がないかはテストで確かめる。
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..", "..", "..", "contracts");
export const OUT = join(here, "..", "src", "contracts.gen.ts");

export function parseQueries(text) {
  const out = {};
  for (const block of text.split(/^-- name:\s*/m).slice(1)) {
    const [head, ...lines] = block.split("\n");
    const q = { sql: "", params: [], json: [], one: false };
    const body = [];
    for (const line of lines) {
      let m;
      if ((m = line.match(/^-- params:\s*(.*)$/))) q.params = m[1].split(",").map((s) => s.trim()).filter(Boolean);
      else if ((m = line.match(/^-- json:\s*(.*)$/))) q.json = m[1].split(",").map((s) => s.trim()).filter(Boolean);
      else if (line.trim() === "-- one") q.one = true;
      else if (!line.startsWith("--")) body.push(line);
    }
    q.sql = body.join("\n").trim().replace(/;$/, "");
    out[head.trim()] = q;
  }
  return out;
}

export function render() {
  const queries = parseQueries(readFileSync(join(root, "queries.sql"), "utf-8"));
  const kinds = JSON.parse(readFileSync(join(root, "change_requests.json"), "utf-8")).kinds;
  return [
    "// 生成物。直接編集しない（contracts/ を直して npm run gen）",
    "import type { ContractKinds, ContractQueries } from \"./contracts.ts\";",
    `export const QUERIES: ContractQueries = ${JSON.stringify(queries, null, 2)};`,
    `export const KINDS: ContractKinds = ${JSON.stringify(kinds, null, 2)};`,
    "",
  ].join("\n");
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  writeFileSync(OUT, render());
  console.log(`wrote ${OUT}`);
}
