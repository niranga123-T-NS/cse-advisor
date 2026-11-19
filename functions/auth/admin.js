// GET -> the last 90 days of the visit log, for the admin page. The middleware already allows only admins here
// (Supabase table public.admins), and Supabase's row security lets only admins read other people's visits.
import { rest, isAdmin } from '../_lib.js';

export async function onRequestGet({ env, data }) {
  if (!(await isAdmin(env, data.claims, data.token))) return new Response('Not found', { status: 404 });
  const since = new Date(Date.now() - 90 * 864e5).toISOString();
  const r = await rest(env, `visits?select=email,started,last_seen,pages,device,country,first_path&started=gte.${encodeURIComponent(since)}&order=started.desc&limit=10000`,
    { token: data.token });
  const h = { 'Cache-Control': 'no-store' };
  if (!r.ok) return Response.json({ error: 'Could not read the visit log (' + r.status + '). Has tools/supabase_visits.sql been run in Supabase?' }, { status: 502, headers: h });
  return Response.json({ since, now: new Date().toISOString(), visits: r.j || [] }, { headers: h });
}
