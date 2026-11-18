// Runs in front of every request to the site on Cloudflare Pages: only signed-in users get the app and its data.
// Sign-in is email + password (Supabase Auth); the session lives in two HttpOnly cookies the page's scripts cannot
// read. An expired session is renewed with the refresh token; anything else goes to the sign-in page. Fails closed.
import { cookie, verify, supabase, sessionCookies, clearCookies } from './_lib.js';

const PUBLIC = [/^\/login(\.html)?$/, /^\/auth\/(login|logout)$/, /^\/\.well-known\/assetlinks\.json$/,
                /^\/(icon-[\w-]+\.png|favicon\.ico|manifest\.webmanifest|login\.js|app\.css)$/];

export async function onRequest({ request, env, next }) {
  const url = new URL(request.url);
  if (PUBLIC.some(r => r.test(url.pathname))) return next();
  if (!env.SUPABASE_URL || !env.SUPABASE_ANON_KEY) return new Response('Sign-in is not set up yet.', { status: 503 });
  const deny = () => {
    const html = (request.headers.get('Accept') || '').includes('text/html');
    const h = new Headers({ 'Cache-Control': 'no-store' });
    clearCookies().forEach(c => h.append('Set-Cookie', c));
    if (html) { h.set('Location', '/login?next=' + encodeURIComponent(url.pathname + url.search + url.hash)); return new Response(null, { status: 302, headers: h }); }
    return new Response('Please sign in.', { status: 401, headers: h });
  };
  let claims = null;
  try { claims = await verify(cookie(request, 'sb_at'), env); } catch (e) { claims = null; }
  if (claims) return next();
  const rt = cookie(request, 'sb_rt');                 // renew a lapsed session
  if (!rt) return deny();
  const r = await supabase(env, 'token?grant_type=refresh_token', { refresh_token: rt });
  if (!r.ok || !r.j || !r.j.access_token) return deny();
  const res = await next();
  const out = new Response(res.body, res);
  sessionCookies(r.j.access_token, r.j.refresh_token, r.j.expires_in || 3600).forEach(c => out.headers.append('Set-Cookie', c));
  return out;
}
