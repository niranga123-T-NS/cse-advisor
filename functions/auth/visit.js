// POST {sid, path, page, ref} -> starts or extends this visit in the visit log (Supabase table "visits", see
// tools/supabase_visits.sql). Runs as the signed-in user, so they can only write their own visits.
import { rest } from '../_lib.js';

function device(ua, ref) {
  if (/android-app:\/\//.test(ref || '')) return 'Android app';
  const os = /iPad/.test(ua) ? 'iPad' : /iPhone/.test(ua) ? 'iPhone' : /Android/.test(ua) ? 'Android' : /Windows/.test(ua) ? 'Windows'
    : /Macintosh/.test(ua) ? 'Mac' : /Linux/.test(ua) ? 'Linux' : 'Other';
  const br = /Edg\//.test(ua) ? 'Edge' : /CriOS|Chrome\//.test(ua) ? 'Chrome' : /FxiOS|Firefox\//.test(ua) ? 'Firefox' : /Safari\//.test(ua) ? 'Safari' : '';
  return br ? `${os}, ${br}` : os;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

export async function onRequestPost({ request, env, data }) {
  let b = {};
  try { b = await request.json(); } catch (e) {}
  const sid = String(b.sid || '');
  if (!UUID.test(sid) || !data.token) return new Response(null, { status: 204 });
  const cf = request.cf || {};
  const where = [cf.city, cf.country].filter(Boolean).join(', ');
  const r = await rest(env, 'rpc/visit_ping', { method: 'POST', token: data.token, body: {
    p_sid: sid, p_path: String(b.path || '').slice(0, 200), p_page: !!b.page,
    p_device: device(request.headers.get('User-Agent') || '', String(b.ref || '')), p_country: where } });
  return new Response(null, { status: r.ok ? 204 : 202, headers: { 'Cache-Control': 'no-store' } });
}
