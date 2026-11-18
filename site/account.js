// Account page: who is signed in, change password, sign out.
(function () {
  try { const t = localStorage.getItem('theme'); if (t) document.documentElement.dataset.theme = t; } catch (e) {}
  const $ = id => document.getElementById(id), msg = $('msg');
  fetch('/auth/me', { credentials: 'same-origin' }).then(r => r.json()).then(j => { $('me').textContent = j.email || 'unknown'; }).catch(() => { $('me').textContent = 'unknown'; });
  $('pw').addEventListener('submit', async e => {
    e.preventDefault(); msg.style.color = ''; msg.textContent = '';
    if ($('p1').value !== $('p2').value) { msg.style.color = 'var(--ruby)'; msg.textContent = 'The two passwords do not match.'; return; }
    const r = await fetch('/auth/password', { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', body: JSON.stringify({ password: $('p1').value }) });
    const j = await r.json().catch(() => ({}));
    msg.style.color = r.ok ? 'var(--emerald)' : 'var(--ruby)';
    msg.textContent = r.ok ? 'Password changed.' : (j.error || 'Could not change the password.');
    if (r.ok) $('pw').reset();
  });
  $('out').addEventListener('click', async () => {
    try { await fetch('/auth/logout', { method: 'POST', credentials: 'same-origin' }); } catch (e) {}
    location.replace('/login');
  });
})();

// Android app download: shown once a build exists.
fetch('/app/version.json', { credentials: 'same-origin', cache: 'no-store' })
  .then(r => r.ok ? r.json() : null)
  .then(v => { if (!v) return; document.getElementById('apkv').textContent = 'Version ' + v.version + ', ' + v.built; document.getElementById('android').hidden = false; })
  .catch(() => {});
