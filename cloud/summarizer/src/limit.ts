// 1 日の使用量の上限。粗い上限なので KV（結果整合）で足りる。日付は JST（パイプラインの「1 日」と合わせる）。

export function jstDate(now: Date): string {
  return new Date(now.getTime() + 9 * 3600 * 1000).toISOString().slice(0, 10);
}

export function usageKey(now: Date): string {
  return `usage:${jstDate(now)}`;
}

export async function usedToday(kv: KVNamespace, now: Date): Promise<number> {
  return Number((await kv.get(usageKey(now))) ?? 0);
}

export async function addUsage(kv: KVNamespace, now: Date, tokens: number): Promise<void> {
  if (tokens <= 0) return;
  const used = await usedToday(kv, now);
  await kv.put(usageKey(now), String(used + tokens), { expirationTtl: 3 * 86400 });
}

export async function overLimit(kv: KVNamespace, now: Date, limit: number): Promise<boolean> {
  return limit > 0 && (await usedToday(kv, now)) >= limit;
}
