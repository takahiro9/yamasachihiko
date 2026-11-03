import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { render, OUT } from "../scripts/gen-contracts.mjs";
import { validateChangeRequest, ContractError } from "../src/contracts.ts";
import { KINDS, QUERIES } from "../src/contracts.gen.ts";
import { createApp, type Env } from "../src/index.ts";
import { fakeD1 } from "./fake-d1.ts";

const T = "2026-11-01T00:00:00Z";

function setup(opts: { auth?: boolean; maxPending?: string } = {}) {
  const { d1, raw } = fakeD1();
  raw.exec(`
    INSERT INTO sources (id,type,url,media,country,language,created_at,updated_at) VALUES ('nhk','rss','https://x/rss','NHK','JP','ja','${T}','${T}');
    INSERT INTO events (id,headline,summary,importance,report_count,country_count,region,first_seen_at,updated_at,actors)
      VALUES ('e1','見出し1','要約1',5,3,2,'東アジア','${T}','${T}','["A"]'),('e2','見出し2','',1,1,1,'欧州','${T}','${T}',NULL);
    INSERT INTO reports (id,url,content_hash,media,country,language,title,fetched_at) VALUES ('r1','https://n/1','h','NHK','JP','ja','t','${T}');
    INSERT INTO event_reports VALUES ('e1','r1',0,'${T}');
    INSERT INTO event_themes VALUES ('e1','economy',1,0.9,0);
    INSERT INTO links VALUES ('l1','e2','e1','続報','根拠',0.8,'pending_review',0,'${T}');
    INSERT INTO digests VALUES ('2026-11-01','${T}','{"date":"2026-11-01","top":[],"sections":[],"spikes":[],"notices":[]}');
  `);
  const env: Env = {
    DB: d1, ASSETS: { fetch: async () => new Response("<html>") } as unknown as Fetcher,
    ACCESS_TEAM_DOMAIN: "https://t", ACCESS_AUD: "aud", MAX_PENDING_REQUESTS: opts.maxPending,
  };
  const app = createApp({ verify: async (tok) => opts.auth !== false && tok === "ok", newId: () => "web-1", now: () => new Date(T) });
  const req = (path: string, init: RequestInit = {}) =>
    app.request(path, { ...init, headers: { "Cf-Access-Jwt-Assertion": "ok", ...(init.headers ?? {}) } }, env);
  return { app, env, raw, req };
}

test("generated contracts are up to date", () => {
  assert.equal(readFileSync(OUT, "utf-8"), render(), "contracts/ を変えたら npm run gen");
});

test("every contract query runs on the D1 schema", async () => {
  const { env } = setup();
  for (const [name, q] of Object.entries(QUERIES)) {
    const r = await env.DB.prepare(q.sql).bind(...q.params.map(() => null)).all();
    assert.ok(Array.isArray(r.results), name);
  }
});

test("requires Access JWT (assets too), healthz is open", async () => {
  const { app, env } = setup({ auth: false });
  assert.equal((await app.request("/api/status", {}, env)).status, 401);
  assert.equal((await app.request("/", {}, env)).status, 401);
  assert.equal((await app.request("/healthz", {}, env)).status, 200);
});

test("ACCESS_DISABLED only bypasses for dev", async () => {
  const { app, env } = setup({ auth: false });
  assert.equal((await app.request("/api/me", {}, { ...env, ACCESS_DISABLED: "1" })).status, 200);
});

test("event list and detail", async () => {
  const { req } = setup();
  const list = await (await req("/api/events?limit=10")).json() as { id: string; theme_ids: string | null }[];
  assert.deepEqual(list.map((e) => e.id), ["e1", "e2"]);
  assert.equal(list[0].theme_ids, "economy");
  const filtered = await (await req("/api/events?theme=economy")).json() as unknown[];
  assert.equal(filtered.length, 1);
  const d = await (await req("/api/events/e1")).json() as Record<string, any>;
  assert.deepEqual(d.actors, ["A"]);
  assert.equal(d.reports[0].url, "https://n/1");
  assert.equal(d.themes[0].name, "経済・市場");
  assert.equal(d.links[0].other_id, "e2");
  assert.equal((await req("/api/events/nope")).status, 404);
});

test("digest latest decodes body", async () => {
  const { req } = setup();
  const d = await (await req("/api/digests/latest")).json() as Record<string, any>;
  assert.equal(d.body.date, "2026-11-01");
  assert.equal((await req("/api/digests/bad")).status, 400);
});

test("themes, sources, status", async () => {
  const { req } = setup();
  const themes = await (await req("/api/themes")).json() as { id: string; event_count: number }[];
  assert.equal(themes.find((t) => t.id === "economy")!.event_count, 1);
  const t = await (await req("/api/themes/economy")).json() as Record<string, any>;
  assert.equal(t.events.length, 1);
  const s = await (await req("/api/sources?enabled=1")).json() as unknown[];
  assert.equal(s.length, 1);
  const st = await (await req("/api/status")).json() as Record<string, any>;
  assert.equal(st.overview.event_count, 2);
});

test("post change request validates and stores", async () => {
  const { req, raw } = setup();
  const post = (body: unknown, headers: Record<string, string> = {}) =>
    req("/api/requests", { method: "POST", body: JSON.stringify(body), headers: { "Content-Type": "application/json", ...headers } });
  const ok = await post({ kind: "link.reject", payload: { id: "l1", reason: "誤り", junk: 1 } });
  assert.equal(ok.status, 201);
  const row = raw.prepare("SELECT * FROM change_requests").get() as Record<string, string>;
  assert.deepEqual([row.id, row.kind, row.origin, row.status, JSON.parse(row.payload)], ["web-1", "link.reject", "web", "pending", { id: "l1", reason: "誤り" }]);
  assert.equal((await post({ kind: "nope", payload: {} })).status, 400);
  assert.equal((await post({ kind: "source.disable", payload: { id: "nhk" } })).status, 400);
  assert.equal((await post({ kind: "link.confirm", payload: { id: "l1" } }, { Origin: "https://evil.example" })).status, 403);
  assert.equal((await req("/api/requests", { method: "POST", body: "{}" })).status, 415);
  const listed = await (await req("/api/requests?status=pending")).json() as { payload: unknown }[];
  assert.deepEqual(listed[0].payload, { id: "l1", reason: "誤り" });
});

test("pending cap", async () => {
  const { req } = setup({ maxPending: "0" });
  const r = await req("/api/requests", { method: "POST", body: JSON.stringify({ kind: "link.confirm", payload: { id: "l1" } }),
    headers: { "Content-Type": "application/json" } });
  assert.equal(r.status, 429);
});

test("validateChangeRequest types", () => {
  assert.throws(() => validateChangeRequest(KINDS, "event.confirm_theme", { event_id: "e", theme_id: "t", theme_version: "1" }), ContractError);
  assert.deepEqual(validateChangeRequest(KINDS, "theme.update", { id: "x", reason: "r", phases: ["a"] }), { id: "x", reason: "r", phases: ["a"] });
});

test("briefs: latest, events, detail, links, docs", async () => {
  const { req, raw } = setup();
  raw.exec(`
    INSERT INTO briefs VALUES ('2026-11-02','2026-11-01T15:00:00Z','2026-11-02T15:00:00Z',3,2,1,'{"北米":"要点"}','${T}');
    INSERT INTO brief_events (label,id,title,summary,category,categories,region,regions,n_reports,media,countries,importance)
      VALUES ('2026-11-02','E0001','出来事1','要約','経済・市場','["経済・市場","政治・選挙"]','北米','{"北米":1}',1,'["NHK"]','["US"]',3),
             ('2026-11-02','E0002','出来事2','','スポーツ','["スポーツ"]','欧州','{}',1,'[]','[]',1);
    INSERT INTO brief_event_reports VALUES ('2026-11-02','E0001','r1',0);
    INSERT INTO brief_links VALUES ('2026-11-02',0,'2026-11-02','E0001','2026-11-02','E0002','反応','根拠',0.7);
    INSERT INTO docs VALUES ('brief','2026-11-02','overview',0,'## 全体'),('brief','2026-11-02','overview',1,'の要点');
    INSERT INTO theme_reviews VALUES ('2026-11-02','2026-11-01','2026-11-02','["2026-11-02"]',2,'[]','[]','${T}');
    INSERT INTO docs VALUES ('themes','2026-11-02','themes',0,'# テーマ');
  `);
  const b = await (await req("/api/briefs/latest")).json() as Record<string, any>;
  assert.equal(b.overview, "## 全体の要点");
  assert.deepEqual(b.region_points, { 北米: "要点" });
  assert.equal(b.top.length, 2);
  assert.equal(b.links[0].to_title, "出来事2");
  assert.equal(b.brief_md, null);
  const byCat = await (await req("/api/brief-events?category=" + encodeURIComponent("政治・選挙"))).json() as unknown[];
  assert.equal(byCat.length, 1);
  const e = await (await req("/api/briefs/2026-11-02/events/E0001")).json() as Record<string, any>;
  assert.equal(e.reports[0].media, "NHK");
  assert.equal(e.links[0].other_title, "出来事2");
  assert.equal((await req("/api/briefs/bad!/events/E1")).status, 400);
  assert.equal((await req("/api/briefs/2026-11-02/events/E9999")).status, 404);
  const tr = await (await req("/api/theme-reviews/latest")).json() as Record<string, any>;
  assert.equal(tr.review, "# テーマ");
  const facets = await (await req("/api/brief-facets")).json() as { facet: string }[];
  assert.ok(facets.some((f) => f.facet === "region"));
});

test("legacy brief returns brief.md", async () => {
  const { req, raw } = setup();
  raw.exec(`INSERT INTO briefs VALUES ('2026-11-02','a','b',3,0,0,NULL,'${T}');
            INSERT INTO docs VALUES ('brief','2026-11-02','brief',0,'# 旧');`);
  const b = await (await req("/api/briefs/2026-11-02")).json() as Record<string, any>;
  assert.equal(b.brief_md, "# 旧");
});
