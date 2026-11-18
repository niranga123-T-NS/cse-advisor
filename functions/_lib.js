// Shared helpers for sign-in with email and password (Supabase Auth). Used by the middleware and /auth/* routes.
//
// Environment (Cloudflare Pages > Settings > Variables, type Secret):
//   SUPABASE_URL       https://<project>.supabase.co
//   SUPABASE_ANON_KEY  the project's publishable (anon) key - it identifies the project, it is not a password

const enc = new TextEncoder();
const b64url = s => Uint8Array.from(atob(s.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(s.length / 4) * 4, '=')), c => c.charCodeAt(0));
const part = s => JSON.parse(new TextDecoder().decode(b64url(s)));
let jwks = null, jwksAt = 0;

export function cookie(req, name) {
  const m = (req.headers.get('Cookie') || '').match(new RegExp('(?:^|;\\s*)' + name + '=([^;]+)'));
  return m ? decodeURIComponent(m[1]) : null;
}

export function sessionCookies(at, rt, maxAge) {
  const base = '; Path=/; Secure; HttpOnly; SameSite=Strict';
  return [`sb_at=${encodeURIComponent(at)}${base}; Max-Age=${maxAge}`, `sb_rt=${encodeURIComponent(rt)}${base}; Max-Age=${60 * 60 * 24 * 30}`];
}
export const clearCookies = () => ['sb_at=; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=0', 'sb_rt=; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=0'];

// Accepts the project address in the forms people tend to paste (dashboard link, bare ref, with /rest/v1 or
// /auth/v1 on the end, quotes or spaces) and returns https://<ref>.supabase.co. Custom domains are kept as given.
export function sbBase(env) {
  let u = String(env.SUPABASE_URL || '').trim().replace(/^["']|["']$/g, '').trim();
  const dash = u.match(/supabase\.com\/dashboard\/project\/([a-z0-9]{15,})/i);
  if (dash) return `https://${dash[1].toLowerCase()}.supabase.co`;
  if (/^[a-z0-9]{15,}$/i.test(u)) return `https://${u.toLowerCase()}.supabase.co`;
  if (!/^https?:\/\//i.test(u)) u = 'https://' + u;
  try { const x = new URL(u); return `https://${x.host.toLowerCase()}`; } catch (e) { return u.replace(/\/+$/, ''); }
}

export async function supabase(env, path, body, token) {
  const headers = { apikey: env.SUPABASE_ANON_KEY, 'Content-Type': 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;
  const r = await fetch(`${sbBase(env)}/auth/v1/${path}`, { method: body === undefined ? 'GET' : body === null ? 'POST' : (path === 'user' ? 'PUT' : 'POST'), headers, body: body ? JSON.stringify(body) : undefined });
  let j = null; try { j = await r.json(); } catch (e) {}
  return { ok: r.ok, status: r.status, j };
}

// Checks the access token: signature (the project's public keys; older projects are asked directly), issuer,
// audience and expiry. Returns the token's claims or null.
export async function verify(token, env) {
  const p = (token || '').split('.');
  if (p.length !== 3) return null;
  let head, body;
  try { head = part(p[0]); body = part(p[1]); } catch (e) { return null; }
  const iss = `${sbBase(env)}/auth/v1`, now = Math.floor(Date.now() / 1000);
  const aud = Array.isArray(body.aud) ? body.aud : [body.aud];
  if (body.iss !== iss || !aud.includes('authenticated') || !(body.exp > now)) return null;
  if (head.alg === 'ES256' || head.alg === 'RS256') {
    if (!jwks || Date.now() - jwksAt > 600e3) {
      const r = await fetch(`${iss}/.well-known/jwks.json`);
      if (!r.ok) return null;
      jwks = (await r.json()).keys || []; jwksAt = Date.now();
    }
    const jwk = jwks.find(k => k.kid === head.kid);
    if (!jwk) { jwks = null; return null; }
    const alg = head.alg === 'ES256' ? { name: 'ECDSA', namedCurve: 'P-256' } : { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' };
    const key = await crypto.subtle.importKey('jwk', jwk, alg, false, ['verify']);
    const ok = await crypto.subtle.verify(head.alg === 'ES256' ? { name: 'ECDSA', hash: 'SHA-256' } : alg, key, b64url(p[2]), enc.encode(p[0] + '.' + p[1]));
    return ok ? body : null;
  }
  if (head.alg === 'HS256') {                       // older projects: ask Supabase whether the token is good
    const r = await supabase(env, 'user', undefined, token);
    return r.ok ? body : null;
  }
  return null;
}
