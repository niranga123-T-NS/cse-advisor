// POST {password} -> changes the signed-in user's password (the middleware has already checked the session).
import { cookie, supabase } from '../_lib.js';

export async function onRequestPost({ request, env }) {
  if (request.headers.get('Origin') && new URL(request.headers.get('Origin')).host !== new URL(request.url).host) return Response.json({ error: 'Bad origin' }, { status: 403 });
  let b = {}; try { b = await request.json(); } catch (e) {}
  const pw = String(b.password || '');
  if (pw.length < 10) return Response.json({ error: 'Use at least 10 characters.' }, { status: 400 });
  const r = await supabase(env, 'user', { password: pw }, cookie(request, 'sb_at'));
  return r.ok ? Response.json({ ok: true }) : Response.json({ error: (r.j && (r.j.msg || r.j.message)) || 'Could not change the password.' }, { status: 400 });
}
