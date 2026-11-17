// Sign-in form: sends the email and password to /auth/login, which sets the session cookies, then goes back.
(function () {
  try { const t = localStorage.getItem('theme'); if (t) document.documentElement.dataset.theme = t; } catch (e) {}
  const f = document.getElementById('f'), go = document.getElementById('go'), err = document.getElementById('err');
  const next = (() => { const n = new URLSearchParams(location.search).get('next') || '/'; return n.startsWith('/') && !n.startsWith('//') ? n : '/'; })();
  f.addEventListener('submit', async e => {
    e.preventDefault(); err.textContent = ''; go.disabled = true; go.textContent = 'Signing in…';
    try {
      const r = await fetch('/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin',
        body: JSON.stringify({ email: f.email.value, password: f.password.value }) });
      const j = await r.json().catch(() => ({}));
      if (r.ok) { location.replace(next); return; }
      err.textContent = j.error || 'Could not sign in.';
    } catch (x) { err.textContent = 'No connection. Check your internet and try again.'; }
    go.disabled = false; go.textContent = 'Sign in';
  });
})();
