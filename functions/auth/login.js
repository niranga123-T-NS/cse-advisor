// POST {email, password} -> session cookies. Supabase limits repeated attempts on its side.
import { supabase, sessionCookies } from '../_lib.js';

export async function onRequestPost({ request, env }) {
  if (!env.SUPABASE_URL || !env.SUPABASE_ANON_KEY) return Response.json({ error: 'Sign-in is not set up yet.' }, { status: 503 });
  if (request.headers.get('Origin') && new URL(request.headers.get('Origin')).host !== new URL(request.url).host) return Response.json({ error: 'Bad origin' }, { status: 403 });
  let b = {}; try { b = await request.json(); } catch (e) {}
  const email = String(b.email || '').trim().slice(0, 200), password = String(b.password || '').slice(0, 200);
  if (!email || !password) return Response.json({ error: 'Enter your email and password.' }, { status: 400 });
  const r = await supabase(env, 'token?grant_type=password', { email, password });
  if (!r.ok || !r.j || !r.j.access_token) {
    const many = r.status === 429;
    return Response.json({ error: many ? 'Too many attempts. Wait a few minutes and try again.' : 'Wrong email or password.' }, { status: many ? 429 : 401 });
  }
  const h = new Headers({ 'Cache-Control': 'no-store' });
  sessionCookies(r.j.access_token, r.j.refresh_token, r.j.expires_in || 3600).forEach(c => h.append('Set-Cookie', c));
  return Response.json({ ok: true }, { headers: h });
}
