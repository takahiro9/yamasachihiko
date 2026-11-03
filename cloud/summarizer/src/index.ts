import { Hono } from "hono";
import { fetchKeys, verifyAccessJwt, type Jwk } from "./access.ts";
import { addUsage, overLimit } from "./limit.ts";
import { BadRequest, parseRequest, toAiInput, toOpenAIResponse } from "./openai.ts";

export interface Env {
  AI: Ai;
  USAGE: KVNamespace;
  SUMMARIZER_MODEL: string;
  DAILY_TOKEN_LIMIT: string;
  ACCESS_TEAM_DOMAIN: string;
  ACCESS_AUD: string;
}

const app = new Hono<{ Bindings: Env }>();

let keyCache: { keys: Jwk[]; at: number } | null = null;
async function accessKeys(env: Env): Promise<Jwk[]> {
  if (!keyCache || Date.now() - keyCache.at > 3600_000) keyCache = { keys: await fetchKeys(env.ACCESS_TEAM_DOMAIN), at: Date.now() };
  return keyCache.keys;
}

// ① Access のトークンを検証
app.use("/v1/*", async (c, next) => {
  const ok = await verifyAccessJwt(c.req.header("Cf-Access-Jwt-Assertion"), {
    issuer: c.env.ACCESS_TEAM_DOMAIN, aud: c.env.ACCESS_AUD, keys: await accessKeys(c.env),
  }).catch(() => false);
  if (!ok) return c.json({ error: { message: "unauthorized" } }, 401);
  await next();
});

app.post("/v1/chat/completions", async (c) => {
  let req;
  try {
    req = parseRequest(await c.req.json());
  } catch (e) {
    if (e instanceof BadRequest) return c.json({ error: { message: e.message } }, 400);
    return c.json({ error: { message: "invalid JSON" } }, 400);
  }
  // ⑤ 1 日の上限（超えたら 429。パイプラインは再試行したのち翌晩に持ち越す）
  const now = new Date();
  if (await overLimit(c.env.USAGE, now, Number(c.env.DAILY_TOKEN_LIMIT))) {
    return c.json({ error: { message: "daily token limit reached" } }, 429, { "Retry-After": "3600" });
  }
  // ② 論理モデル名（req.model）は無視して SUMMARIZER_MODEL に変換 ③ 推論
  const model = c.env.SUMMARIZER_MODEL;
  let ai: unknown;
  try {
    ai = await c.env.AI.run(model as keyof AiModels, toAiInput(req) as never);
  } catch (e) {
    return c.json({ error: { message: `workers ai: ${(e as Error).message}` } }, 502);
  }
  // ④ OpenAI 互換の形に詰め直す（usage も返す）
  const body = toOpenAIResponse(ai, model);
  c.executionCtx.waitUntil(addUsage(c.env.USAGE, now, body.usage.total_tokens));
  return c.json(body);
});

app.get("/healthz", (c) => c.text("ok"));

export default app;
