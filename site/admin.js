// Admin page: the visit log (Supabase table "visits") summarised per person, for admins only.
(function () {
  try { const t = localStorage.getItem('theme'); if (t) document.documentElement.dataset.theme = t; } catch (e) {}
  const $ = id => document.getElementById(id);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const tz = { timeZone: 'Asia/Colombo' };
  const when = d => new Date(d).toLocaleString('en-GB', { ...tz, day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
  const dayKey = d => new Date(d).toLocaleDateString('en-CA', tz);
  const dur = ms => { const m = Math.round(ms / 60000); return m < 1 ? 'under 1 min' : m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`; };
  const ago = ms => ms < 120000 ? 'now' : ms < 3600000 ? `${Math.round(ms / 60000)} min ago` : ms < 86400000 ? `${Math.round(ms / 3600000)} h ago` : `${Math.round(ms / 86400000)} days ago`;
  let V = [], NOW = Date.now(), days = 7;

  function render() {
    const from = NOW - days * 864e5, vs = V.filter(v => Date.parse(v.started) >= from);
    const len = v => Math.max(0, Date.parse(v.last_seen) - Date.parse(v.started));
    const people = {};
    vs.forEach(v => {
      const p = people[v.email] || (people[v.email] = { email: v.email, n: 0, ms: 0, pages: 0, days: new Set(), dev: {}, last: 0, where: {} });
      p.n++; p.ms += len(v); p.pages += v.pages || 0; p.days.add(dayKey(v.started));
      p.dev[v.device || 'Unknown'] = 1; if (v.country) p.where[v.country] = 1; p.last = Math.max(p.last, Date.parse(v.last_seen));
    });
    const list = Object.values(people).sort((a, b) => b.last - a.last);
    const onNow = list.filter(p => NOW - p.last < 150000).length, today = dayKey(NOW);
    const vt = vs.filter(v => dayKey(v.started) === today);
    $('tiles').innerHTML = [
      [list.length, `people in the last ${days === 1 ? 'day' : days + ' days'}`], [onNow, 'on the site now'],
      [vs.length, 'visits'], [dur(vs.reduce((a, v) => a + len(v), 0)), 'total time']
    ].map(([b, s]) => `<div class="tile"><b>${esc(b)}</b><span>${esc(s)}</span></div>`).join('');
    $('users').innerHTML = list.length ? `<thead><tr><th>Person</th><th class="num">Visits</th><th class="num">Days</th><th class="num">Time</th><th class="num hide-s">Average</th><th class="num hide-s">Pages</th><th class="num">Last seen</th><th class="hide-s">Devices</th><th class="hide-s">Where</th></tr></thead><tbody>${
      list.map(p => `<tr><td>${NOW - p.last < 150000 ? '<span class="live" title="On the site now"></span>' : ''}<b>${esc(p.email)}</b></td><td class="num">${p.n}</td><td class="num">${p.days.size}</td>
        <td class="num">${dur(p.ms)}</td><td class="num hide-s">${dur(p.ms / p.n)}</td><td class="num hide-s">${p.pages}</td><td class="num">${ago(NOW - p.last)}</td>
        <td class="hide-s small">${esc(Object.keys(p.dev).join(', '))}</td><td class="hide-s small">${esc(Object.keys(p.where).slice(0, 3).join('; '))}</td></tr>`).join('')}</tbody>`
      : '<tbody><tr><td class="mute">No visits in this period.</td></tr></tbody>';
    const recent = vs.slice(0, 100);
    $('visits').innerHTML = recent.length ? `<thead><tr><th>Person</th><th>Started</th><th class="num">Time</th><th class="num">Pages</th><th class="hide-s">Device</th><th class="hide-s">Where</th></tr></thead><tbody>${
      recent.map(v => `<tr><td>${esc(v.email)}</td><td>${when(v.started)}</td><td class="num">${dur(len(v))}</td><td class="num">${v.pages}</td><td class="hide-s small">${esc(v.device || '')}</td><td class="hide-s small">${esc(v.country || '')}</td></tr>`).join('')}</tbody>`
      : '<tbody><tr><td class="mute">None.</td></tr></tbody>';
    $('sub').textContent = `${vt.length} visit${vt.length === 1 ? '' : 's'} today. Updated ${when(NOW)}.`;
    $('per').innerHTML = [[1, 'Today and yesterday'], [7, '7 days'], [30, '30 days'], [90, '90 days']]
      .map(([d, l]) => `<button class="chip ${d === days ? 'on' : ''}" data-d="${d}">${l}</button>`).join('');
  }
  $('per').addEventListener('click', e => { const b = e.target.closest('[data-d]'); if (b) { days = +b.dataset.d; render(); } });

  async function load() {
    try {
      const r = await fetch('/auth/admin', { credentials: 'same-origin', cache: 'no-store' });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) { $('sub').textContent = j.error || 'Could not load the visit log.'; return; }
      V = j.visits || []; NOW = Date.parse(j.now) || Date.now(); render();
    } catch (e) { $('sub').textContent = 'Could not load the visit log.'; }
  }
  load(); setInterval(load, 60000);
})();
