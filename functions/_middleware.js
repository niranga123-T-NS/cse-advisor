// Runs in front of every request to the site on Cloudflare Pages.
// Defence in depth behind Cloudflare Access: even if the Access policy were removed or misconfigured, nothing is
// served unless the request carries a valid Access token for this application. Fails closed.
//
// Environment (Pages project > Settings > Variables):
//   ACCESS_TEAM_DOMAIN  e.g. yourteam.cloudflareaccess.com
//   ACCESS_AUD          the Application Audience (AUD) tag of the Access application

const PUBLIC = new Set(['/.well-known/assetlinks.json']);   // Android checks this file without signing in
let keys = null, keysAt = 0;

const b64url = s => Uint8Array.from(atob(s.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(s.length / 4) * 4, '=')), c => c.charCodeAt(0));
const json = s => JSON.parse(new TextDecoder().decode(b64url(s)));

async function accessKeys(team) {
  if (keys && Date.now() - keysAt < 3600e3) return keys;
  const r = await fetch(`https://${team}/cdn-cgi/access/certs`);
  if (!r.ok) throw new Error('certs');
  keys = (await r.json()).keys; keysAt = Date.now();
  return keys;
}

async function valid(token, env) {
  const parts = (token || '').split('.');
  if (parts.length !== 3) return false;
  const head = json(parts[0]), body = json(parts[1]);
  if (head.alg !== 'RS256') return false;
  const now = Math.floor(Date.now() / 1000);
  const aud = Array.isArray(body.aud) ? body.aud : [body.aud];
  if (!aud.includes(env.ACCESS_AUD) || body.iss !== `https://${env.ACCESS_TEAM_DOMAIN}` || !(body.exp > now) || (body.nbf && body.nbf > now + 60)) return false;
  const jwk = (await accessKeys(env.ACCESS_TEAM_DOMAIN)).find(k => k.kid === head.kid);
  if (!jwk) return false;
  const key = await crypto.subtle.importKey('jwk', jwk, { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' }, false, ['verify']);
  return crypto.subtle.verify('RSASSA-PKCS1-v1_5', key, b64url(parts[2]), new TextEncoder().encode(parts[0] + '.' + parts[1]));
}

function cookie(req, name) {
  const m = (req.headers.get('Cookie') || '').match(new RegExp('(?:^|;\\s*)' + name + '=([^;]+)'));
  return m ? m[1] : null;
}

export async function onRequest(ctx) {
  const { request, env, next } = ctx;
  const url = new URL(request.url);
  if (PUBLIC.has(url.pathname)) return next();
  if (!env.ACCESS_TEAM_DOMAIN || !env.ACCESS_AUD) return new Response('Sign-in is not set up yet.', { status: 503 });
  let ok = false;
  try { ok = await valid(request.headers.get('Cf-Access-Jwt-Assertion') || cookie(request, 'CF_Authorization'), env); } catch (e) { ok = false; }
  if (!ok) return new Response('Please sign in.', { status: 403, headers: { 'Cache-Control': 'no-store' } });
  return next();
}
