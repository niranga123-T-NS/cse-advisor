// GET -> the signed-in user's email (for the account page).
import { cookie } from '../_lib.js';
export async function onRequestGet({ request }) {
  const at = cookie(request, 'sb_at') || '';
  let email = null;
  try { email = JSON.parse(atob(at.split('.')[1].replace(/-/g, '+').replace(/_/g, '/'))).email; } catch (e) {}
  return Response.json({ email }, { headers: { 'Cache-Control': 'no-store' } });
}
