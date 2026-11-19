// Live prices for signed-in users: reads the `live` branch of the (private) repository with a read-only token kept
// on Cloudflare, so the browser never needs GitHub access. The middleware has already checked the sign-in.
//
// Environment: GH_READ_TOKEN (fine-grained token: this repository only, Contents: read-only)
//              GH_REPO       optional, default niranga123-T-NS/cse-advisor
//
// GitHub allows 5,000 requests an hour per token, so answers are cached at Cloudflare's edge: the 5-second and
// 1-minute files never change once written (cached for an hour), now.json (the newest reading) for 2 seconds
// (at most 1,800 requests an hour), the rest for 6 seconds, a "not yet" for 5 seconds.

export async function onRequestGet(ctx) {
  const { params, env } = ctx;
  const path = (Array.isArray(params.path) ? params.path : [params.path]).join('/');
  if (!/^(?:[tmb]\/)?[A-Za-z0-9_-]+\.json$/.test(path)) return new Response('Not found', { status: 404 });
  const cache = caches.default, key = new Request(`https://live-cache.internal/${path}`);
  const hit = await cache.match(key);
  if (hit) return withHeaders(hit);
  const repo = env.GH_REPO || 'niranga123-T-NS/cse-advisor';
  const headers = { 'User-Agent': 'cse-advisor', 'Accept': 'application/vnd.github.raw' };
  if (env.GH_READ_TOKEN) headers.Authorization = `Bearer ${env.GH_READ_TOKEN}`;
  const r = await fetch(`https://api.github.com/repos/${repo}/contents/${path}?ref=live`, { headers });
  const ttl = !r.ok ? 5 : /^[tmb]\//.test(path) ? 3600 : path === 'now.json' ? 2 : 6;
  const res = new Response(r.ok ? await r.arrayBuffer() : 'Not found', { status: r.ok ? 200 : 404,
    headers: { 'Content-Type': r.ok ? 'application/json; charset=utf-8' : 'text/plain', 'Cache-Control': `max-age=${ttl}` } });
  ctx.waitUntil(cache.put(key, res.clone()));
  return withHeaders(res);
}

function withHeaders(r) {
  const out = new Response(r.body, r);
  out.headers.set('Cache-Control', 'no-store');              // the browser always asks again; the edge cache answers
  out.headers.set('X-Content-Type-Options', 'nosniff');
  return out;
}
