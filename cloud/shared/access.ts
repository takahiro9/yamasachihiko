// Cloudflare Access が付ける JWT（Cf-Access-Jwt-Assertion）を Worker でも検証する。summarizer と閲覧用の worker で共通。
// Access のポリシーを外し忘れた・workers.dev に直接届いた、という事故で無認証のまま公開されるのを防ぐ。

function b64urlToBytes(s: string): Uint8Array {
  const pad = "=".repeat((4 - (s.length % 4)) % 4);
  const bin = atob(s.replace(/-/g, "+").replace(/_/g, "/") + pad);
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}

const dec = new TextDecoder();

export interface Jwk { kid: string; kty: string; n: string; e: string; alg?: string }

export async function verifyAccessJwt(
  token: string | null | undefined,
  opts: { issuer: string; aud: string; keys: Jwk[]; now?: number },
): Promise<boolean> {
  if (!token) return false;
  const parts = token.split(".");
  if (parts.length !== 3) return false;
  try {
    const header = JSON.parse(dec.decode(b64urlToBytes(parts[0])));
    const payload = JSON.parse(dec.decode(b64urlToBytes(parts[1])));
    if (header.alg !== "RS256") return false;
    const jwk = opts.keys.find((k) => k.kid === header.kid);
    if (!jwk) return false;
    const key = await crypto.subtle.importKey(
      "jwk", { kty: jwk.kty, n: jwk.n, e: jwk.e, alg: "RS256", ext: true },
      { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"],
    );
    const ok = await crypto.subtle.verify(
      "RSASSA-PKCS1-v1_5", key, b64urlToBytes(parts[2]), new TextEncoder().encode(`${parts[0]}.${parts[1]}`),
    );
    if (!ok) return false;
    const now = opts.now ?? Math.floor(Date.now() / 1000);
    const aud = Array.isArray(payload.aud) ? payload.aud : [payload.aud];
    return payload.iss === opts.issuer && aud.includes(opts.aud) && typeof payload.exp === "number" && payload.exp > now;
  } catch {
    return false;
  }
}

export async function fetchKeys(teamDomain: string): Promise<Jwk[]> {
  const r = await fetch(`${teamDomain}/cdn-cgi/access/certs`);
  if (!r.ok) throw new Error(`certs ${r.status}`);
  return ((await r.json()) as { keys: Jwk[] }).keys;
}

/** 公開鍵を 1 時間キャッシュして検証する（isolate ごと）。 */
export function accessVerifier() {
  let cache: { keys: Jwk[]; at: number; team: string } | null = null;
  return async (token: string | null | undefined, team: string, aud: string): Promise<boolean> => {
    if (!cache || cache.team !== team || Date.now() - cache.at > 3600_000) {
      cache = { keys: await fetchKeys(team), at: Date.now(), team };
    }
    return verifyAccessJwt(token, { issuer: team, aud, keys: cache.keys });
  };
}

/** JWT の payload（検証済みのものに対してだけ使う）。画面に「誰が見ているか」を出すため。 */
export function jwtEmail(token: string | null | undefined): string | null {
  try {
    const p = JSON.parse(dec.decode(b64urlToBytes(token!.split(".")[1])));
    return typeof p.email === "string" ? p.email : null;
  } catch {
    return null;
  }
}
