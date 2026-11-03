import { Hono } from "hono";
import { accessVerifier, jwtEmail } from "../../shared/access.ts";
import { ContractError, runQuery, validateChangeRequest, type ContractKinds, type ContractQueries } from "./contracts.ts";
import { KINDS, QUERIES } from "./contracts.gen.ts";

// 閲覧用の Worker。D1（Mac の正本の公開用の写し）を読み、書くのは change_requests だけ。
// 読み取りは contracts/queries.sql の名前付きクエリだけを使う（Mac 側の CLI・MCP と同じものが見える）。

export interface Env {
  DB: D1Database;
  ASSETS: Fetcher;
  ACCESS_TEAM_DOMAIN: string;
  ACCESS_AUD: string;
  MAX_PENDING_REQUESTS?: string;
  ACCESS_DISABLED?: string; // wrangler dev 専用（.dev.vars）。本番では設定しない
}

type Vars = { email: string | null };

export interface AppDeps {
  queries?: ContractQueries;
  kinds?: ContractKinds;
  verify?: (token: string | null | undefined, team: string, aud: string) => Promise<boolean>;
  now?: () => Date;
  newId?: () => string;
}

const intParam = (v: string | undefined, max: number): number | undefined => {
  if (v === undefined || v === "") return undefined;
  const n = Number(v);
  return Number.isInteger(n) && n >= 0 ? Math.min(n, max) : undefined;
};
const strParam = (v: string | undefined): string | undefined => (v ? v.slice(0, 200) : undefined);

export function createApp(deps: AppDeps = {}) {
  const queries = deps.queries ?? QUERIES;
  const kinds = deps.kinds ?? KINDS;
  const verify = deps.verify ?? accessVerifier();
  const now = deps.now ?? (() => new Date());
  const newId = deps.newId ?? (() => `web-${crypto.randomUUID().replaceAll("-", "").slice(0, 12)}`);

  const app = new Hono<{ Bindings: Env; Variables: Vars }>();
  const q = (env: Env, name: string, params?: Record<string, unknown>) => runQuery(env.DB, queries, name, params);

  app.get("/healthz", (c) => c.text("ok"));

  // ① Cloudflare Access の JWT を検証（静的ファイルも含めすべて）。Access の設定漏れで丸見えになるのを防ぐ
  app.use("*", async (c, next) => {
    const token = c.req.header("Cf-Access-Jwt-Assertion");
    if (c.env.ACCESS_DISABLED === "1") {
      c.set("email", "dev@localhost");
      return next();
    }
    const ok = await verify(token, c.env.ACCESS_TEAM_DOMAIN, c.env.ACCESS_AUD).catch(() => false);
    if (!ok) return c.json({ error: "unauthorized" }, 401);
    c.set("email", jwtEmail(token));
    await next();
  });

  app.onError((e, c) => {
    if (e instanceof ContractError) return c.json({ error: e.message }, 400);
    console.error(e);
    return c.json({ error: "internal error" }, 500);
  });

  // ---- 読み取り ----
  app.get("/api/me", (c) => c.json({ email: c.get("email") }));

  app.get("/api/status", async (c) =>
    c.json({ overview: await q(c.env, "status.overview"), jobs: await q(c.env, "jobs.recent", { limit: 10 }) }));

  app.get("/api/digests", async (c) => c.json(await q(c.env, "digests.list", { limit: 90 })));
  app.get("/api/digests/:date", async (c) => {
    const date = c.req.param("date");
    if (date !== "latest" && !/^\d{4}-\d{2}-\d{2}$/.test(date)) return c.json({ error: "date は YYYY-MM-DD か latest" }, 400);
    const d = await q(c.env, "digests.get", { date: date === "latest" ? null : date });
    return d ? c.json(d) : c.json({ error: "not found" }, 404);
  });

  // ---- 簡易ブリーフ（news-brief）・テーマ整理（news-themes）。いまの主な見るもの ----
  const label = (v: string) => (v === "latest" ? null : /^[\d_-]{4,40}$/.test(v) ? v : undefined);
  const doc = async (env: Env, scope: string, lb: string, name: string) =>
    ((await q(env, "docs.get", { scope, label: lb, name })) as { body?: string } | null)?.body ?? null;

  app.get("/api/briefs", async (c) => c.json(await q(c.env, "briefs.list", { limit: 200 })));
  app.get("/api/briefs/:label", async (c) => {
    const lb = label(c.req.param("label"));
    if (lb === undefined) return c.json({ error: "label が不正" }, 400);
    const b = (await q(c.env, "briefs.get", { label: lb })) as Record<string, unknown> | null;
    if (!b) return c.json({ error: "not found" }, 404);
    const L = b.label as string;
    const [overview, top, links, facets] = await Promise.all([
      doc(c.env, "brief", L, "overview"), q(c.env, "brief_events.list", { label: L, limit: 40 }),
      q(c.env, "brief_links.list", { label: L }), q(c.env, "brief_events.facets", { label: L }),
    ]);
    // 旧形式（events.json の無いブリーフ）は brief.md をそのまま見せる
    const brief_md = b.event_count ? null : await doc(c.env, "brief", L, "brief");
    return c.json({ ...b, overview, brief_md, top, links, facets });
  });
  app.get("/api/brief-events", async (c) => {
    const r = c.req.query();
    const lb = r.label ? label(r.label) : null;
    if (lb === undefined) return c.json({ error: "label が不正" }, 400);
    return c.json(await q(c.env, "brief_events.list", {
      label: lb, category: strParam(r.category), region: strParam(r.region), q: strParam(r.q),
      limit: intParam(r.limit, 100) ?? 30, offset: intParam(r.offset, 100_000),
    }));
  });
  app.get("/api/brief-facets", async (c) => {
    const lb = c.req.query("label") ? label(c.req.query("label")!) : null;
    if (lb === undefined) return c.json({ error: "label が不正" }, 400);
    return c.json(await q(c.env, "brief_events.facets", { label: lb }));
  });
  app.get("/api/briefs/:label/events/:id", async (c) => {
    const lb = label(c.req.param("label"));
    const id = c.req.param("id");
    if (!lb || !/^E\d+$/.test(id)) return c.json({ error: "不正な指定" }, 400);
    const e = await q(c.env, "brief_events.get", { label: lb, id });
    if (!e) return c.json({ error: "not found" }, 404);
    const [reports, links] = await Promise.all([
      q(c.env, "brief_events.reports", { label: lb, id }), q(c.env, "brief_events.links", { label: lb, id }),
    ]);
    return c.json({ ...e, reports, links });
  });
  app.get("/api/theme-reviews", async (c) => c.json(await q(c.env, "theme_reviews.list")));
  app.get("/api/theme-reviews/:label", async (c) => {
    const lb = label(c.req.param("label"));
    if (lb === undefined) return c.json({ error: "label が不正" }, 400);
    const r = (await q(c.env, "theme_reviews.get", { label: lb })) as Record<string, unknown> | null;
    if (!r) return c.json({ error: "not found" }, 404);
    return c.json({ ...r, review: await doc(c.env, "themes", r.label as string, "themes") });
  });

  // ---- パイプライン（run-daily）の結果。いまは止めているが、動かせば同じ画面で見られる ----
  app.get("/api/events", async (c) => {
    const r = c.req.query();
    return c.json(await q(c.env, "events.list", {
      theme: strParam(r.theme), region: strParam(r.region), since: strParam(r.since), q: strParam(r.q),
      limit: intParam(r.limit, 100) ?? 30, offset: intParam(r.offset, 100_000),
    }));
  });
  app.get("/api/events/:id", async (c) => {
    const id = c.req.param("id");
    const e = await q(c.env, "events.get", { id });
    if (!e) return c.json({ error: "not found" }, 404);
    const [reports, themes, links] = await Promise.all([
      q(c.env, "events.reports", { id }), q(c.env, "events.themes", { id }), q(c.env, "events.links", { id }),
    ]);
    return c.json({ ...e, reports, themes, links });
  });
  app.get("/api/regions", async (c) => c.json(await q(c.env, "regions.list")));

  app.get("/api/themes", async (c) => c.json(await q(c.env, "themes.list")));
  app.get("/api/themes/:id", async (c) => {
    const id = c.req.param("id");
    const t = await q(c.env, "themes.get", { id });
    if (!t) return c.json({ error: "not found" }, 404);
    const [versions, snapshots, spikes, events] = await Promise.all([
      q(c.env, "themes.versions", { id }), q(c.env, "themes.snapshots", { id }),
      q(c.env, "spikes.recent", { theme: id }), q(c.env, "events.list", { theme: id, limit: 30 }),
    ]);
    return c.json({ ...t, versions, snapshots, spikes, events });
  });
  app.get("/api/spikes", async (c) =>
    c.json(await q(c.env, "spikes.recent", { since: strParam(c.req.query("since")), theme: strParam(c.req.query("theme")) })));

  app.get("/api/sources", async (c) => {
    const e = c.req.query("enabled");
    return c.json(await q(c.env, "sources.list", { enabled: e === "1" ? 1 : e === "0" ? 0 : null }));
  });
  app.get("/api/sources/:id/history", async (c) => c.json(await q(c.env, "sources.history", { id: c.req.param("id") })));

  // ---- 変更依頼（Cloudflare 側が書く唯一の表） ----
  app.get("/api/requests/kinds", (c) => c.json(kinds));
  app.get("/api/requests", async (c) =>
    c.json(await q(c.env, "requests.list", { status: strParam(c.req.query("status")), limit: 200 })));

  app.post("/api/requests", async (c) => {
    // CSRF 対策: JSON だけを受ける（別オリジンからは preflight が要り、CORS を許していないので通らない）+ Origin の一致
    if (!(c.req.header("Content-Type") ?? "").startsWith("application/json")) return c.json({ error: "JSON で送る" }, 415);
    const origin = c.req.header("Origin");
    if (origin && origin !== new URL(c.req.url).origin) return c.json({ error: "forbidden" }, 403);
    let body: { kind?: unknown; payload?: unknown };
    try {
      body = await c.req.json();
    } catch {
      return c.json({ error: "invalid JSON" }, 400);
    }
    const payload = validateChangeRequest(kinds, body.kind, body.payload);
    const pending = (await q(c.env, "status.overview")) as { pending_requests: number };
    if (pending.pending_requests >= Number(c.env.MAX_PENDING_REQUESTS ?? 200)) {
      return c.json({ error: "未反映の依頼が多すぎる（Mac 側の取り込みを待つ）" }, 429);
    }
    const id = newId();
    await c.env.DB.prepare(
      "INSERT INTO change_requests (id, kind, payload, status, created_at, origin) VALUES (?1, ?2, ?3, 'pending', ?4, 'web')",
    ).bind(id, body.kind, JSON.stringify(payload), now().toISOString().replace(/\.\d{3}Z$/, "Z")).run();
    return c.json({ id, kind: body.kind, payload, status: "pending" }, 201);
  });

  app.all("/api/*", (c) => c.json({ error: "not found" }, 404));

  // ② それ以外は画面（静的ファイル）
  app.all("*", (c) => c.env.ASSETS.fetch(c.req.raw));

  return app;
}

export default createApp();
