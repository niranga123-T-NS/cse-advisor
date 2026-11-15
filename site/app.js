/* CSE Advisor: the frame shared by every page: navigation, search, theme.
   Put <script src="app.js"></script> first inside <body class="app" data-page="..." data-title="...">.
   Pages talk to it through window.CSEApp. */
(function () {
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]));
  const svg = (p, fill) => `<svg viewBox="0 0 24 24" fill="${fill ? 'currentColor' : 'none'}" stroke="${fill ? 'none' : 'currentColor'}" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${p}</svg>`;
  const ICON = {
    mark: svg('<path d="M12 2.8 20.5 9 12 21.2 3.5 9z"/><path d="M3.5 9h17M8.5 9 12 2.8 15.5 9 12 21.2z"/>'),
    home: svg('<path d="M4 11.5 12 5l8 6.5"/><path d="M6.5 10v9h11v-9"/>'),
    shares: svg('<path d="M5 7h14M5 12h14M5 17h9"/>'),
    alerts: svg('<path d="M6.5 16.5v-5a5.5 5.5 0 0 1 11 0v5l1.5 2h-14z"/><path d="M10.2 20.5a2 2 0 0 0 3.6 0"/>'),
    trends: svg('<path d="M4 16.5l5-5 3.5 3.5L20 7.5"/><path d="M15 7.5h5v5"/>'),
    veterans: svg('<path d="M12 6.5c-2-1.3-4.5-1.6-7-1v12.5c2.5-.6 5-.3 7 1 2-1.3 4.5-1.6 7-1V5.5c-2.5-.6-5-.3-7 1z"/><path d="M12 6.5V19"/>'),
    market: svg('<path d="M5 19v-8M10 19V5M15 19v-6M20 19V8"/>'),
    accuracy: svg('<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r=".6"/>'),
    checks: svg('<path d="M12 3.5l7 2.5v5.5c0 4.2-2.8 7.4-7 9-4.2-1.6-7-4.8-7-9V6z"/><path d="M8.8 12.2l2.3 2.3 4.2-4.6"/>'),
    search: svg('<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/>'),
    more: svg('<circle cx="5.5" cy="12" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="18.5" cy="12" r="1.5"/>', true),
    sun: svg('<circle cx="12" cy="12" r="4"/><path d="M12 2.5V5M12 19v2.5M2.5 12H5M19 12h2.5M5.3 5.3l1.8 1.8M16.9 16.9l1.8 1.8M5.3 18.7l1.8-1.8M16.9 7.1l1.8-1.8"/>'),
    moon: svg('<path d="M19.5 14.5A8 8 0 0 1 9.5 4.5a8 8 0 1 0 10 10z"/>'),
    back: svg('<path d="M14.5 5.5 8 12l6.5 6.5"/>'),
    star: svg('<path d="M12 4l2.5 5.1 5.6.8-4 3.9.9 5.6-5-2.7-5 2.7.9-5.6-4-3.9 5.6-.8z"/>'),
    starOn: svg('<path d="M12 4l2.5 5.1 5.6.8-4 3.9.9 5.6-5-2.7-5 2.7.9-5.6-4-3.9 5.6-.8z"/>', true),
    plus: svg('<path d="M12 5v14M5 12h14"/>'),
    compare: svg('<path d="M3 17l5-6 4 3 4-6 5 4"/><path d="M3 9l5 3 4-4 4 5 5-3" opacity=".45"/>'),
    close: svg('<path d="M6 6l12 12M18 6L6 18"/>'),
    filter: svg('<path d="M4 6.5h16M7 12h10M10 17.5h4"/>'),
    chev: svg('<path d="M9.5 5.5 16 12l-6.5 6.5"/>'),
  };
  const body = document.body, page = body.dataset.page || '', onIndex = page === 'index';
  const NAV = [['home', 'Home', './#/home'], ['shares', 'Shares', './#/shares'], ['alerts', 'Alerts', './#/alerts'], ['trends', 'Trends', 'trends.html'],
               ['veterans', 'Veterans', 'veterans.html'], ['market', 'Market', 'market.html'], ['accuracy', 'Accuracy', 'accuracy.html'], ['checks', 'Checks', 'audit.html']];
  const link = ([k, label, href]) => `<a href="${href}" data-nav="${k}">${ICON[k]}<span>${label}</span></a>`;

  // ---------- theme: follows the device until you choose ----------
  const root = document.documentElement;
  const dark = () => root.dataset.theme ? root.dataset.theme === 'dark' : !matchMedia('(prefers-color-scheme: light)').matches;
  function setTheme(t) {
    root.dataset.theme = t;
    try { localStorage.setItem('theme', t); } catch (e) {}
    const m = document.querySelector('meta[name=theme-color]'); if (m) m.content = getComputedStyle(root).getPropertyValue('--ground').trim();
    document.querySelectorAll('[data-theme-btn]').forEach(b => { b.innerHTML = dark() ? ICON.sun : ICON.moon; b.title = dark() ? 'Use the light theme' : 'Use the dark theme'; });
    dispatchEvent(new Event('themechange'));
  }
  const css = name => getComputedStyle(root).getPropertyValue(name).trim();

  // ---------- the frame ----------
  body.insertAdjacentHTML('afterbegin', `
    <nav class="rail" aria-label="Sections"><a class="mark" href="./#/home" title="CSE Advisor">${ICON.mark}</a>${NAV.map(link).join('')}<span class="gap"></span>
      <button data-theme-btn aria-label="Change theme"></button></nav>
    <header class="top"><div class="where"><div class="title">${esc(body.dataset.title || 'CSE Advisor')}</div><div class="status" id="live"></div></div>
      <button class="searchbtn" data-search aria-label="Search">${ICON.search}<span>Search shares, screens, pages</span><kbd>/</kbd></button>
      <button class="iconbtn search" data-search aria-label="Search">${ICON.search}</button></header>
    <nav class="tabs" aria-label="Sections">${NAV.slice(0, 4).map(link).join('')}<button data-more>${ICON.more}<span>More</span></button></nav>`);
  function setActive(key) { document.querySelectorAll('[data-nav]').forEach(a => a.classList.toggle('on', a.dataset.nav === key));
    const inMore = NAV.slice(4).some(n => n[0] === key); const m = document.querySelector('[data-more]'); if (m) m.classList.toggle('on', inMore); }
  setActive({ audit: 'checks' }[page] || page);
  function badge(n) { document.querySelectorAll('[data-nav=alerts]').forEach(a => { let d = a.querySelector('.dot'); if (!n) { if (d) d.remove(); return; }
    if (!d) { d = document.createElement('i'); d.className = 'dot'; a.appendChild(d); } d.textContent = n > 99 ? '99+' : n; }); }

  // ---------- sheets ----------
  let closeSheet = null;
  function sheet(html, full) {
    if (closeSheet) closeSheet();
    const s = document.createElement('div'); s.className = 'scrim' + (full ? ' full' : ''); s.innerHTML = `<div class="sheet" role="dialog" aria-modal="true">${html}</div>`;
    const prev = document.activeElement;
    const close = () => { s.remove(); removeEventListener('keydown', key, true); closeSheet = null; if (prev && prev.focus) prev.focus(); };
    const key = e => { if (e.key === 'Escape') { e.preventDefault(); close(); } };
    s.addEventListener('click', e => { if (e.target === s) close(); });
    addEventListener('keydown', key, true);
    body.appendChild(s); closeSheet = close;
    return { el: s.firstElementChild, close };
  }
  const repo = (() => { const m = location.hostname.match(/^([^.]+)\.github\.io$/), r = location.pathname.split('/').filter(Boolean)[0]; return m && r ? `https://github.com/${m[1]}/${r}` : null; })();
  function openMore() {
    const rows = NAV.slice(4).map(([k, label, href]) => `<a class="pi" href="${href}">${ICON[k]}<span class="t">${{ veterans:"Veterans' checklists", market:'Market history', accuracy:'Accuracy of ratings and forecasts', checks:'Data checks' }[k]}</span>${ICON.chev}</a>`).join('');
    const sh = sheet(`<div class="list" style="padding:10px"><a class="pi" href="./#/compare">${ICON.compare}<span class="t">Compare shares</span><span class="s">up to four</span></a>${rows}
      ${repo ? `<a class="pi" href="${repo}/actions/workflows/update.yml" target="_blank" rel="noopener">${ICON.plus}<span class="t">Add shares or refresh the data</span><span class="s">on GitHub</span></a>` : ''}
      <button class="pi" data-theme-row>${dark() ? ICON.sun : ICON.moon}<span class="t">${dark() ? 'Use the light theme' : 'Use the dark theme'}</span></button></div>
      <div class="foot">Signals and checklists follow fixed rules. They are not financial advice.</div>`);
    sh.el.querySelector('[data-theme-row]').onclick = () => { setTheme(dark() ? 'light' : 'dark'); sh.close(); };
    sh.el.querySelectorAll('a.pi').forEach(a => a.addEventListener('click', () => sh.close()));      // a link within this page does not reload it
  }

  // ---------- search: shares, ready-made screens, pages ----------
  const SCREENS = [['invest', 'Investment picks'], ['tradewatch', 'Trading picks and watch list'], ['sb', 'Strong buy ratings'], ['BUY', 'Buy ratings'], ['SELL', 'Sell ratings'], ['up2', 'Rising in every time frame'],
    ['down2', 'Falling in every time frame'], ['acc', 'Quiet buying'], ['dist', 'Quiet selling'], ['value', 'Low price to earnings (under 10)'], ['div', 'Dividend yield 5% or more'],
    ['watch', 'My favourites'], ['graham', "Pass Graham's checklist"], ['buffett', "Pass Buffett's checklist"], ['minervini', "Pass Minervini's trend template"], ['unrated', 'Shares that are not rated'], ['issues', 'Shares with data issues']];
  let shares = null, loading = null;
  const getShares = () => shares ? Promise.resolve(shares) : (loading = loading || fetch('data/signals.json').then(r => r.json()).then(s => (shares = s)).catch(() => (shares = [])));
  const recent = { get() { try { return JSON.parse(localStorage.getItem('recent') || '[]'); } catch (e) { return []; } },
                   add(s) { try { localStorage.setItem('recent', JSON.stringify([s, ...this.get().filter(x => x !== s)].slice(0, 6))); } catch (e) {} } };
  const go = href => { if (onIndex && href.startsWith('./#')) location.hash = href.slice(2); else location.href = href; };
  function openSearch() {
    const sh = sheet(`<div class="field">${ICON.search}<input type="search" placeholder="Share, company, industry or screen" autocomplete="off" autocapitalize="off" spellcheck="false" aria-label="Search">
        <button class="chip" data-x>Close</button></div><div class="list" role="listbox"></div>`, true);
    const input = sh.el.querySelector('input'), list = sh.el.querySelector('.list');
    sh.el.querySelector('[data-x]').onclick = sh.close;
    let items = [], at = 0;
    const CMP = { href: './#/compare', icon: 'compare', html: 'Compare shares', side: 'up to four' };
    const row = (it, i) => `<a class="pi ${i === at ? 'on' : ''}" href="${esc(it.href)}" data-i="${i}">${ICON[it.icon] || ICON.chev}<span class="t">${it.html}</span><span class="s">${esc(it.side || '')}</span></a>`;
    function draw() {
      const q = input.value.trim().toLowerCase(), S = shares || [];
      const groups = [];
      if (!q) {
        const rec = recent.get().map(s => S.find(x => x.symbol === s)).filter(Boolean);
        if (rec.length) groups.push(['Recently opened', rec.map(shareItem)]);
        groups.push(['Screens', SCREENS.slice(0, 7).map(screenItem)]);
        groups.push(['Pages', NAV.map(pageItem).concat([CMP])]);
      } else {
        const sym = S.filter(x => x.symbol.toLowerCase().startsWith(q)), rest = S.filter(x => !sym.includes(x) && (x.symbol.toLowerCase().includes(q) || (x.name || '').toLowerCase().includes(q)));
        const found = sym.concat(rest).slice(0, 8);
        if (found.length) groups.push(['Shares', found.map(shareItem)]);
        if (found.length === 1 || (sym[0] && sym[0].symbol.split('.')[0].toLowerCase() === q))
          groups.push(['With this share', [{ icon:'veterans', html:`Check <b>${esc((sym[0] || found[0]).symbol)}</b> against the veterans' rules`, href:'veterans.html#' + encodeURIComponent((sym[0] || found[0]).symbol) }]]);
        const secs = [...new Set(S.map(x => x.sector).filter(Boolean))].filter(x => x.toLowerCase().includes(q)).slice(0, 4);
        if (secs.length) groups.push(['Industry groups', secs.map(x => ({ icon:'filter', html:esc(x), side:S.filter(y => y.sector === x).length + ' shares', href:'./#/shares/sector:' + encodeURIComponent(x) }))]);
        const scr = SCREENS.filter(x => x[1].toLowerCase().includes(q)); if (scr.length) groups.push(['Screens', scr.map(screenItem)]);
        const pg = NAV.filter(x => x[1].toLowerCase().includes(q)).map(pageItem); if ('compare shares'.includes(q)) pg.push(CMP); if (pg.length) groups.push(['Pages', pg]);
      }
      items = groups.flatMap(g => g[1]); at = Math.min(at, Math.max(0, items.length - 1));
      let i = 0;
      list.innerHTML = groups.map(g => `<div class="group">${g[0]}</div>` + g[1].map(it => row(it, i++)).join('')).join('')
        || `<div class="group">Nothing matches "${esc(input.value)}". Try a symbol such as JKH, a company name, or an industry such as Banks.</div>`;
    }
    const shareItem = x => ({ icon:'shares', html:`<b>${esc(x.symbol)}</b> <span class="mute">${esc(x.name || '')}</span>`, side:x.signal ? x.signal.toLowerCase() : '', href:'./#' + encodeURIComponent(x.symbol), sym:x.symbol });
    const screenItem = x => ({ icon:'filter', html:esc(x[1]), href:'./#/shares/' + x[0] });
    const pageItem = x => ({ icon:x[0], html:esc({ checks:'Data checks', veterans:"Veterans' checklists", market:'Market history', accuracy:'Accuracy of ratings and forecasts' }[x[0]] || x[1]), href:x[2] });
    const pick = it => { if (!it) return; if (it.sym) recent.add(it.sym); sh.close(); go(it.href); };
    input.addEventListener('input', () => { at = 0; draw(); });
    input.addEventListener('keydown', e => {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); at = (at + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % Math.max(1, items.length); draw();
        const on = list.querySelector('.pi.on'); if (on) on.scrollIntoView({ block:'nearest' }); }
      else if (e.key === 'Enter') { e.preventDefault(); pick(items[at]); }
    });
    list.addEventListener('click', e => { const a = e.target.closest('.pi'); if (a) { e.preventDefault(); pick(items[+a.dataset.i]); } });
    draw(); getShares().then(draw); input.focus();
  }

  document.addEventListener('click', e => {
    if (e.target.closest('[data-search]')) { openSearch(); return; }
    if (e.target.closest('[data-more]')) { openMore(); return; }
    if (e.target.closest('[data-theme-btn]')) setTheme(dark() ? 'light' : 'dark');
  });
  addEventListener('keydown', e => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target && e.target.tagName) || '');
    if ((e.key === 'k' && (e.metaKey || e.ctrlKey)) || (e.key === '/' && !typing && !closeSheet)) { e.preventDefault(); openSearch(); }
  });
  document.querySelectorAll('[data-theme-btn]').forEach(b => { b.innerHTML = dark() ? ICON.sun : ICON.moon; b.title = dark() ? 'Use the light theme' : 'Use the dark theme'; });
  const tc = document.querySelector('meta[name=theme-color]'); if (tc) tc.content = css('--ground');      // the browser's own bars take the page colour

  const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  // "2026-11-15 21:56" -> "15 Nov 2026, 21:56"
  const when = t => { const m = /^(\d{4})-(\d\d)-(\d\d)(?:[ T](\d\d:\d\d))?/.exec(t || ''); return m ? `${+m[3]} ${MON[m[2] - 1]} ${m[1]}${m[4] ? ', ' + m[4] : ''}` : esc(t || ''); };
  window.CSEApp = { icon: ICON, esc, setActive, badge, openSearch, sheet, css, dark, recent, when,
    setShares(s) { shares = s; }, setStatus(html) { const el = document.querySelector('.top .status'); if (el) el.innerHTML = html; },
    // colours for the charts, read from the theme in use
    chart() { return { bg: css('--ground'), panel: css('--surface-1'), text: css('--ink-3'), grid: css('--line-soft'), border: css('--line'), up: css('--emerald'), down: css('--ruby'),
                       accent: css('--sapphire'), topaz: css('--topaz'), ink: css('--ink'), mutebar: css('--surface-3') }; } };
})();
