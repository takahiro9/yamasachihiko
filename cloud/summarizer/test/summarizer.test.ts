import assert from "node:assert/strict";
import { test } from "node:test";
import { verifyAccessJwt } from "../src/access.ts";
import { addUsage, jstDate, overLimit, usageKey } from "../src/limit.ts";
import { BadRequest, parseRequest, toAiInput, toOpenAIResponse } from "../src/openai.ts";

test("parseRequest keeps only the supported fields", () => {
  const r = parseRequest({ model: "summarizer", messages: [{ role: "user", content: "hi" }], temperature: 0.1, max_tokens: 99999, stream: true, tools: [] });
  assert.deepEqual(Object.keys(r).sort(), ["max_tokens", "messages", "temperature"]);
  assert.equal(r.max_tokens, 4096);
});

test("parseRequest rejects bad input", () => {
  assert.throws(() => parseRequest({}), BadRequest);
  assert.throws(() => parseRequest({ messages: [{ role: "tool", content: "x" }] }), BadRequest);
  assert.throws(() => parseRequest({ messages: [{ role: "user", content: 1 }] }), BadRequest);
});

test("toAiInput passes response_format through", () => {
  const rf = { type: "json_schema", json_schema: { name: "l1", schema: {} } };
  assert.deepEqual(toAiInput({ messages: [], response_format: rf, temperature: 0 }).response_format, rf);
});

test("toOpenAIResponse handles both Workers AI shapes and serializes object output", () => {
  const a = toOpenAIResponse({ choices: [{ message: { content: '{"a":1}' } }], usage: { prompt_tokens: 5, completion_tokens: 2 } }, "@cf/m", 1);
  assert.equal(a.choices[0].message.content, '{"a":1}');
  assert.deepEqual(a.usage, { prompt_tokens: 5, completion_tokens: 2, total_tokens: 7 });
  assert.equal(a.model, "@cf/m");
  const b = toOpenAIResponse({ response: { a: 1 } }, "@cf/m", 1);
  assert.equal(b.choices[0].message.content, '{"a":1}');
  assert.equal(b.usage.total_tokens, 0);
});

class MemKV {
  m = new Map<string, string>();
  async get(k: string) { return this.m.get(k) ?? null; }
  async put(k: string, v: string) { this.m.set(k, v); }
}

test("daily limit uses the JST date and trips at the limit", async () => {
  const kv = new MemKV() as unknown as KVNamespace;
  const t = new Date("2026-10-25T16:00:00Z"); // JST 10/26 01:00
  assert.equal(jstDate(t), "2026-10-26");
  assert.equal(usageKey(t), "usage:2026-10-26");
  assert.equal(await overLimit(kv, t, 100), false);
  await addUsage(kv, t, 60);
  await addUsage(kv, t, 50);
  assert.equal(await overLimit(kv, t, 100), true);
  assert.equal(await overLimit(kv, t, 0), false); // 0 は無制限
  assert.equal(await overLimit(kv, new Date("2026-10-26T16:00:00Z"), 100), false); // 翌日は 0 から
});

function b64url(buf: ArrayBuffer | Uint8Array | string): string {
  const bytes = typeof buf === "string" ? new TextEncoder().encode(buf) : new Uint8Array(buf);
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function sign(payload: object, kid = "k1") {
  const pair = await crypto.subtle.generateKey(
    { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign", "verify"]);
  const jwk = (await crypto.subtle.exportKey("jwk", pair.publicKey)) as { n: string; e: string; kty: string };
  const head = b64url(JSON.stringify({ alg: "RS256", kid }));
  const body = b64url(JSON.stringify(payload));
  const sig = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", pair.privateKey, new TextEncoder().encode(`${head}.${body}`));
  return { token: `${head}.${body}.${b64url(sig)}`, keys: [{ kid, kty: jwk.kty, n: jwk.n, e: jwk.e }] };
}

test("verifyAccessJwt accepts a valid token and rejects wrong aud / issuer / expiry / signature", async () => {
  const iss = "https://t.cloudflareaccess.com";
  const { token, keys } = await sign({ iss, aud: ["AUD"], exp: 2000 });
  const base = { issuer: iss, aud: "AUD", keys, now: 1000 };
  assert.equal(await verifyAccessJwt(token, base), true);
  assert.equal(await verifyAccessJwt(token, { ...base, aud: "OTHER" }), false);
  assert.equal(await verifyAccessJwt(token, { ...base, issuer: "https://evil" }), false);
  assert.equal(await verifyAccessJwt(token, { ...base, now: 3000 }), false);
  const other = await sign({ iss, aud: ["AUD"], exp: 2000 });
  assert.equal(await verifyAccessJwt(token, { ...base, keys: other.keys }), false); // 別の鍵
  assert.equal(await verifyAccessJwt(null, base), false);
  assert.equal(await verifyAccessJwt("a.b", base), false);
});
