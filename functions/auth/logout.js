// POST -> ends the session at Supabase and clears the cookies.
import { cookie, supabase, clearCookies } from '../_lib.js';

export async function onRequestPost({ request, env }) {
  const at = cookie(request, 'sb_at');
  if (at && env.SUPABASE_URL) { try { await supabase(env, 'logout', null, at); } catch (e) {} }
  const h = new Headers({ 'Cache-Control': 'no-store' });
  clearCookies().forEach(c => h.append('Set-Cookie', c));
  return Response.json({ ok: true }, { headers: h });
}
