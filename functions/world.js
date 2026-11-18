// World markets for the banner (signed-in users only, like everything else here): metals, oil and fuels, the rupee,
// major currencies and share indices, from Yahoo Finance's free chart data. Cached at Cloudflare's edge for
// 5 minutes, so Yahoo sees at most one round of requests per 5 minutes per edge location.
import LIST from '../world_symbols.json';

const TTL = 300;
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36';
const pct = (a, b) => (a && b ? Math.round((a / b - 1) * 10000) / 100 : null);
const r4 = x => Math.round(x * 10000) / 10000;

function summarise(res) {
  const meta = res.meta || {}, q = (((res.indicators || {}).quote || [{}])[0].close) || [], ts = res.timestamp || [];
  const pts = ts.map((t, i) => [t, q[i]]).filter(x => x[1]);
  const p = meta.regularMarketPrice || (pts.length ? pts[pts.length - 1][1] : null);
  if (!p || pts.length < 3) return null;
  const closes = pts.map(x => x[1]);
  const same = meta.regularMarketTime && Math.floor(pts[pts.length - 1][0] / 86400) === Math.floor(meta.regularMarketTime / 86400);
  const base = same ? closes.slice(0, -1) : closes, ago = n => (base.length >= n ? base[base.length - n] : null);
  return { p: r4(p), d1: pct(p, ago(1)), w1: pct(p, ago(5)), m1: pct(p, ago(21)), t: meta.regularMarketTime || pts[pts.length - 1][0],
           line: closes.slice(-22).map(r4) };
}

async function one(k) {
  try {
    const r = await fetch(`https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(k)}?range=3mo&interval=1d`,
      { headers: { 'User-Agent': UA, Accept: 'application/json' }, cf: { cacheTtl: TTL, cacheEverything: true } });
    if (!r.ok) return null;
    const j = await r.json();
    return summarise(j.chart.result[0]);
  } catch (e) { return null; }
}

export async function onRequestGet(ctx) {
  const cache = caches.default, key = new Request('https://world-cache.internal/v1');
  const hit = await cache.match(key);
  if (hit) return hit;
  const got = await Promise.all(LIST.items.map(it => one(it.k)));
  const items = LIST.items.map((it, i) => (got[i] ? { ...it, ...got[i] } : null)).filter(Boolean);
  const res = new Response(JSON.stringify({ updated: Math.floor(Date.now() / 1000), items }), {
    status: items.length ? 200 : 502,
    headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': `max-age=${items.length ? TTL : 30}` } });
  if (items.length) ctx.waitUntil(cache.put(key, res.clone()));
  return res;
}
