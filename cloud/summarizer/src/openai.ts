// OpenAI 互換の形の変換だけ。プロンプトも、JSON の中身の検証・照合も持たない（Python 側にある）。

export interface ChatRequest {
  model?: string;
  messages: { role: string; content: string }[];
  response_format?: unknown;
  temperature?: number;
  max_tokens?: number;
}

export class BadRequest extends Error {}

const ROLES = new Set(["system", "user", "assistant"]);

/** 受け付けるのは messages・response_format・temperature・max_tokens だけ。それ以外は黙って捨てる。 */
export function parseRequest(body: unknown): ChatRequest {
  const b = body as Record<string, unknown> | null;
  if (!b || typeof b !== "object") throw new BadRequest("body must be a JSON object");
  const msgs = b.messages;
  if (!Array.isArray(msgs) || msgs.length === 0) throw new BadRequest("messages is required");
  const messages = msgs.map((m: any) => {
    if (!m || !ROLES.has(m.role) || typeof m.content !== "string") throw new BadRequest("invalid message");
    return { role: m.role as string, content: m.content as string };
  });
  const out: ChatRequest = { messages };
  if (b.response_format !== undefined) out.response_format = b.response_format;
  if (typeof b.temperature === "number") out.temperature = b.temperature;
  if (typeof b.max_tokens === "number") out.max_tokens = Math.min(Math.max(1, Math.floor(b.max_tokens)), 4096);
  return out;
}

/** Workers AI へ渡す入力。response_format は JSON スキーマの強制に使う。 */
export function toAiInput(req: ChatRequest): Record<string, unknown> {
  const input: Record<string, unknown> = { messages: req.messages };
  if (req.response_format !== undefined) input.response_format = req.response_format;
  if (req.temperature !== undefined) input.temperature = req.temperature;
  if (req.max_tokens !== undefined) input.max_tokens = req.max_tokens;
  return input;
}

/** Workers AI の応答（形が 2 通りある）を OpenAI 互換の chat.completion に詰め直す。usage も返す。 */
export function toOpenAIResponse(ai: any, model: string, now: number = Math.floor(Date.now() / 1000)) {
  const choice = ai?.choices?.[0]?.message?.content;
  let content: unknown = choice ?? ai?.response ?? "";
  // JSON モードでは response がオブジェクトで返ることがある。Python 側が文字列の JSON を期待するので直列化する
  if (content !== null && typeof content === "object") content = JSON.stringify(content);
  const usage = ai?.usage ?? {};
  const prompt = Number(usage.prompt_tokens ?? 0);
  const completion = Number(usage.completion_tokens ?? 0);
  return {
    id: `chatcmpl-${now}`,
    object: "chat.completion",
    created: now,
    model, // 実際に使ったモデル ID。キャッシュに記録される
    choices: [{ index: 0, finish_reason: "stop", message: { role: "assistant", content: String(content) } }],
    usage: { prompt_tokens: prompt, completion_tokens: completion, total_tokens: prompt + completion },
  };
}
