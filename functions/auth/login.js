// POST {email, password} -> session cookies. Supabase limits repeated attempts on its side.
import { supabase, sessionCookies } from '../_lib.js';

export async function onRequestPost({ request, env }) {
  if (!env.SUPABASE_URL || !env.SUPABASE_ANON_KEY) return Response.json({ error: 'Sign-in is not set up yet.' }, { status: 503 });
  if (request.headers.get('Origin') && new URL(request.headers.get('Origin')).host !== new URL(request.url).host) return Response.json({ error: 'Bad origin' }, { status: 403 });
  let b = {}; try { b = await request.json(); } catch (e) {}
  const email = String(b.email || '').trim().slice(0, 200), password = String(b.password || '').slice(0, 200);
  if (!email || !password) return Response.json({ error: 'Enter your email and password.' }, { status: 400 });
  let r;
  try { r = await supabase(env, 'token?grant_type=password', { email, password }); }
  catch (e) { return Response.json({ error: 'Setup problem: SUPABASE_URL cannot be reached. Check it is https://<project>.supabase.co' }, { status: 502 }); }
  if (!r.ok || !r.j || !r.j.access_token) {
    const code = (r.j && (r.j.error_code || r.j.error)) || '', msg = String((r.j && (r.j.msg || r.j.message || r.j.error_description)) || '');
    let error = 'Wrong email or password.', status = 401;
    if (r.status === 429) { error = 'Too many attempts. Wait a few minutes and try again.'; status = 429; }
    else if (code === 'email_not_confirmed' || /not confirmed/i.test(msg)) error = 'This account is not confirmed yet. In Supabase, open the user and confirm it (or recreate it with Auto Confirm ticked).';
    else if (/api key|apikey|jwt/i.test(msg) || (r.status === 401 && code !== 'invalid_credentials')) { error = 'Setup problem: SUPABASE_ANON_KEY is not accepted by Supabase.'; status = 502; }
    else if (r.status === 404 || !r.j) { error = 'Setup problem: SUPABASE_URL does not point at a Supabase project.'; status = 502; }
    else if (code && code !== 'invalid_credentials' && code !== 'invalid_grant') error = 'Sign-in failed (' + String(code).slice(0, 40) + ').';
    return Response.json({ error }, { status });
  }
  const h = new Headers({ 'Cache-Control': 'no-store' });
  sessionCookies(r.j.access_token, r.j.refresh_token, r.j.expires_in || 3600).forEach(c => h.append('Set-Cookie', c));
  return Response.json({ ok: true }, { headers: h });
}
