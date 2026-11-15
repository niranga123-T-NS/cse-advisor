// Compare up to four shares in a pop-up: price history against the ASPI, figures side by side, how closely they
// move together, and what that suggests. Everything is worked out in the browser from the files the pages already
// use (data/long/<SYMBOL>.json, data/view/<SYMBOL>.json, data/market.json, data/signals.json).
// The suggestions are fixed rules applied to those figures. They are readings, not advice.
(() => {
  const MAX = 4;
  const RANGES = [[26, '6 months'], [52, '1 year'], [156, '3 years'], [260, '5 years'], [520, '10 years']];
  const WINDOWS = [[52, '1 year'], [156, '3 years'], [260, '5 years']];
  let set = (() => { try { return JSON.parse(localStorage.getItem('compare') || '[]').slice(0, MAX); } catch (e) { return []; } })();
  let data = {}, market = null, range = 52, win = 0, chart = null, token = 0, full = '';
  const save = () => { try { localStorage.setItem('compare', JSON.stringify(set)); } catch (e) {} count(); };
  const el = id => document.getElementById(id);
  function count() { const b = el('cmpBtn'); if (b) b.textContent = set.length ? `Compare (${set.length})` : 'Compare'; }

  // ---------- weekly figures ----------
  // Shares do not all trade on the same day of a week, so everything is lined up by week number (weeks start on Monday).
  const widx = d => Math.floor((Date.UTC(+d.slice(0, 4), +d.slice(5, 7) - 1, +d.slice(8, 10)) / 864e5 + 3) / 7);
  function series(weekly) {
    const px = new Map(), ret = new Map(); let last = -1;
    (weekly || []).forEach(([d, v]) => { const w = widx(d); px.set(w, [d, v]); if (w > last) last = w; });
    px.forEach((x, w) => { const p = px.get(w - 1); if (p && p[1] > 0) ret.set(w, x[1] / p[1] - 1); });      // a week's change needs the week before it
    return { px, ret, last };
  }
  const at = (s, w, back = 3) => { for (let k = 0; k <= back; k++) { const p = s.px.get(w - k); if (p) return p; } return null; };
  const chg = (s, weeks, last) => { const a = at(s, last), b = at(s, last - weeks); return a && b && b[1] > 0 ? (a[1] / b[1] - 1) * 100 : null; };
  // how two sets of weekly changes relate over the last `weeks`: correlation, and how far a moves for each 1% of b
  function rel(a, b, weeks, last) {
    const x = [], y = [];
    for (let w = last - weeks + 1; w <= last; w++) if (a.ret.has(w) && b.ret.has(w)) { x.push(a.ret.get(w)); y.push(b.ret.get(w)); }
    const n = x.length; if (n < Math.max(26, weeks * 0.5)) return null;
    const mx = x.reduce((p, q) => p + q, 0) / n, my = y.reduce((p, q) => p + q, 0) / n;
    let sxy = 0, sxx = 0, syy = 0;
    for (let i = 0; i < n; i++) { sxy += (x[i] - mx) * (y[i] - my); sxx += (x[i] - mx) ** 2; syy += (y[i] - my) ** 2; }
    return sxx && syy ? { r: sxy / Math.sqrt(sxx * syy), beta: sxy / syy, n } : null;
  }

  async function get(sym) {
    if (data[sym]) return data[sym];
    const u = encodeURIComponent(sym);
    const [L, V] = await Promise.all(['long', 'view'].map(k => fetch(`data/${k}/${u}.json`).then(r => r.ok ? r.json() : null).catch(() => null)));
    return data[sym] = { L, V, sr: L && L.weekly ? series(L.weekly) : null };
  }
  async function getMarket() {
    if (!market) { const M = await fetch('data/market.json').then(r => r.json()).catch(() => null); market = M && M.weekly ? series(M.weekly) : { px: new Map(), ret: new Map(), last: -1 }; }
    return market;
  }

  // ---------- opening and closing (the pop-up has its own address, #/compare, so Back closes it) ----------
  window.openCompare = sym => { if (sym && !set.includes(sym) && set.length < MAX) { set.push(sym); save(); } else if (sym && !set.includes(sym)) full = sym; nav('#/compare'); };
  window.closeCompare = () => { if (steps > 0) { steps--; history.back(); } else nav('#/' + view); };
  window.hideCompare = () => { const c = el('cmp'); if (!c || c.hidden) return; c.hidden = true; document.documentElement.style.overflow = ''; if (chart) { try { chart.remove(); } catch (e) {} chart = null; } };
  window.showCompare = async arg => {
    if (arg) { const want = arg.split(',').map(s => s.trim().toUpperCase()).filter(s => all.some(x => x.symbol === s)).slice(0, MAX); if (want.length) { set = want; save(); } }
    set = set.filter(s => !all.length || all.some(x => x.symbol === s));
    el('cmp').hidden = false; document.documentElement.style.overflow = 'hidden';
    const mine = ++token;
    if (!el('cmpbody').innerHTML) el('cmpbody').innerHTML = '<p class="mute">Loading…</p>';
    await Promise.all([getMarket(), ...set.map(get)]);
    if (mine === token && !el('cmp').hidden) render();
  };
  count();
  document.addEventListener('DOMContentLoaded', count);
  addEventListener('keydown', e => { const c = el('cmp'); if (e.key === 'Escape' && c && !c.hidden && !document.querySelector('.scrim')) closeCompare(); });
  addEventListener('themechange', () => { const c = el('cmp'); if (c && !c.hidden) render(); });

  function add(q) {
    q = (q || '').trim().toUpperCase(); if (!q || set.length >= MAX) return;
    const hit = all.find(x => x.symbol === q) || all.find(x => x.symbol.split('.')[0] === q) || all.find(x => x.symbol.startsWith(q)) || all.find(x => (x.name || '').toUpperCase().includes(q));
    if (hit && !set.includes(hit.symbol)) { set.push(hit.symbol); save(); full = ''; showCompare(); }
  }
  document.addEventListener('click', e => {
    const c = el('cmp'); if (!c || c.hidden) return;
    if (e.target === c) { closeCompare(); return; }
    const rm = e.target.closest('[data-crm]'); if (rm) { set = set.filter(s => s !== rm.dataset.crm); save(); full = ''; render(); return; }
    const ad = e.target.closest('[data-cadd]'); if (ad) { add(ad.dataset.cadd); return; }
    const rg = e.target.closest('[data-crange]'); if (rg) { range = +rg.dataset.crange; render(); return; }
    const wn = e.target.closest('[data-cwin]'); if (wn) { win = +wn.dataset.cwin; render(); return; }
    const op = e.target.closest('[data-copen]'); if (op) { hideCompare(); openShare(op.dataset.copen); }
  });
  document.addEventListener('change', e => { if (e.target.id === 'cmpadd') add(e.target.value); });
  document.addEventListener('keydown', e => { if (e.target.id === 'cmpadd' && e.key === 'Enter') add(e.target.value); });

  // ---------- drawing ----------
  const sg = (v, n = 1) => v == null || isNaN(v) ? '<span class="mute">–</span>' : `<span class="${v > 0 ? 'up' : v < 0 ? 'down' : ''}">${v > 0 ? '+' : ''}${(+v).toFixed(Math.abs(v) >= 100 ? 0 : n)}%</span>`;
  const plain = (v, n = 1) => `${v > 0 ? '+' : ''}${(+v).toFixed(n)}%`;
  const big = v => v >= 1e9 ? (v / 1e9).toFixed(v >= 1e10 ? 0 : 1) + ' bn' : (v / 1e6).toFixed(v >= 1e7 ? 0 : 1) + ' m';
  const join = a => a.length < 2 ? a.join('') : a.slice(0, -1).join(', ') + ' and ' + a[a.length - 1];
  const how = r => r >= 0.7 ? 'moved closely together' : r >= 0.45 ? 'often moved together' : r >= 0.2 ? 'been loosely linked' : r > -0.2 ? 'moved largely independently' : 'tended to move in opposite directions';

  function render() {
    const box = el('cmpbody'), M = market || { px: new Map(), ret: new Map(), last: -1 };
    const S = set.map((s, i) => ({ s, i, row: all.find(x => x.symbol === s) || { symbol: s }, d: data[s] || {}, col: `var(--c${i + 1})` }));
    const last = Math.max(M.last, ...S.map(x => x.d.sr ? x.d.sr.last : -1));
    const tk = x => short(x.s);
    // who else might be worth adding: favourites, then the most traded shares of the first share's industry group
    const sug = [...stars].filter(s => !set.includes(s) && all.some(x => x.symbol === s));
    const g = S[0] && typeof peers !== 'undefined' && peers && S[0].row.sector && peers[S[0].row.sector];
    if (g) g.members.forEach(m => { if (!set.includes(m.s) && !sug.includes(m.s)) sug.push(m.s); });
    let h = `<div class="cmpsel">${S.map(x => `<span class="cchip" style="--c:${x.col}"><i></i><b>${tk(x)}</b><button data-crm="${esc(x.s)}" aria-label="Remove ${esc(x.s)}">${A.icon.close || '×'}</button></span>`).join('')}
      ${set.length < MAX ? `<input id="cmpadd" list="cmpsyms" placeholder="${set.length ? 'Add another share' : 'Symbol or company name'}" autocomplete="off" autocapitalize="characters" aria-label="Add a share to compare">` : ''}</div>
      <datalist id="cmpsyms">${all.filter(x => !set.includes(x.symbol)).map(x => `<option value="${esc(x.symbol)}">${esc(x.name || '')}</option>`).join('')}</datalist>
      ${full ? `<p class="small warn" style="margin:8px 0 0">Four shares are already chosen. Remove one to add ${esc(full)}.</p>` : ''}
      ${set.length < MAX && sug.length ? `<div class="cmpsug"><span class="small">${set.length ? 'Add' : 'Start with'}</span>${sug.slice(0, 6).map(s => `<button class="chip" data-cadd="${esc(s)}">${short(s)}</button>`).join('')}</div>` : ''}`;
    if (!S.length) { box.innerHTML = h + `<p class="say" style="margin-top:18px">Choose up to four shares. You will see them charted against the ASPI, their checked figures side by side, how closely they have moved together, and what that suggests.</p>`; focusAdd(); return; }

    // --- price history against the ASPI ---
    const ranges = RANGES.filter(([w]) => w <= 52 || S.some(x => x.d.sr && x.d.sr.px.size > w * 0.6));
    if (!ranges.some(r => r[0] === range)) range = 52;
    const lines = S.map(x => ({ x, pts: x.d.sr ? window_(x.d.sr, range, last) : [] })), mline = window_(M, range, last);
    h += `<h3>Price history against the ASPI</h3>
      <div class="chips">${ranges.map(([w, t]) => `<button class="chip ${w === range ? 'on' : ''}" data-crange="${w}">${t}</button>`).join('')}</div>
      <div id="cmpchart"></div>
      <div class="cleg">${lines.map(o => `<span><i style="background:${o.x.col}"></i><b>${tk(o.x)}</b> ${o.pts.length > 1 ? sg((o.pts[o.pts.length - 1].value - 100)) : '<span class="mute">no prices</span>'}</span>`).join('')}
        <span><i class="dash"></i>ASPI ${mline.length > 1 ? sg(mline[mline.length - 1].value - 100) : ''}</span></div>
      <p class="small" style="margin:6px 0 0">Every line starts at 100, so the lines show growth, not price. Weekly closes on today's share basis; dividends are not included.
        A line that starts late has a shorter history. Before the past year the ASPI line is this site's rebuilt index (see Market history).</p>`;

    // --- figures side by side ---
    const per = (x, weeks) => x.d.sr && x.d.sr.px.size > weeks * 0.6 ? chg(x.d.sr, weeks, last) : null;      // weekly closes, the same basis as the ASPI column and the chart
    const tr = x => (x.d.V && x.d.V.trend) || {}, st = x => (x.d.L && x.d.L.stats) || {};
    const mk = S.map(x => x.d.sr ? rel(x.d.sr, M, 156, last) || rel(x.d.sr, M, 52, last) : null);
    const fits = x => x.d.V ? x.d.V.lenses.filter(l => l.verdict === 'fits').length : null;
    const num = (fn, fmt, better) => ({ fn, fmt, better });
    const ROWS = [
      ['Rating', { html: x => x.row.signal ? `<span class="sig ${sigClass(x.row.signal)}">${sigWord(x.row.signal)}</span>` : '–' }],
      ['Rating score, 1 to 99', num(x => x.row.score, v => v, 'high')],
      ['Price', num(x => x.row.close, v => v.toFixed(2))],
      ['Market value, LKR', num(x => x.row.mcap, big)],
      ['#Price change, on weekly closes'],
      ['1 month', num(x => per(x, 4), v => sg(v), 'high'), chg(M, 4, last)],
      ['3 months', num(x => per(x, 13), v => sg(v), 'high'), chg(M, 13, last)],
      ['1 year', num(x => per(x, 52), v => sg(v), 'high'), chg(M, 52, last)],
      ['3 years', num(x => per(x, 156), v => sg(v), 'high'), chg(M, 156, last)],
      ['5 years', num(x => per(x, 260), v => sg(v), 'high'), chg(M, 260, last)],
      ['#Company figures, checked only'],
      ['Price to earnings', num(x => x.row.pe > 0 ? x.row.pe : null, v => v, 'low')],
      ['Price to net assets', num(x => x.row.pbv_ok && x.row.pbv > 0 ? x.row.pbv : null, v => v, 'low')],
      ['Return on equity', num(x => x.row.roe, v => (+v).toFixed(1) + '%', 'high')],
      ['Dividend yield', num(x => x.row.div_yield, v => (+v).toFixed(1) + '%', 'high')],
      ['Profit, latest report on a year earlier', num(x => tr(x).earnings ? tr(x).earnings.latest : null, v => sg(v), 'high')],
      ['Reports with rising profit', { html: x => tr(x).earnings ? `${tr(x).earnings.up} of ${tr(x).earnings.n}` : '<span class="mute">–</span>' }],
      ['#Risk and trading'],
      ['Typical swing in a year', num(x => st(x).volatility, v => '±' + v + '%', 'low')],
      ['Largest fall on record', num(x => st(x).drawdown ? st(x).drawdown.pct : null, v => sg(v, 0), 'high')],
      ['Move for each 1% in the ASPI', num(x => mk[x.i] ? mk[x.i].beta : null, v => v.toFixed(1) + '%')],
      ['Traded a day, LKR', num(x => x.row.tov, big, 'high')],
      ['#This site\'s tests'],
      ['Investing test', { html: x => x.row.invest ? '<span class="up">✓ passes</span>' : '<span class="mute">no</span>' }],
      ['Veterans\' checklists fitted, of 10', num(fits, v => v, 'high')],
      ['Trend', { html: x => `<span class="small">${esc(x.row.trh || tr(x).headline || '–')}</span>` }],
    ];
    h += `<h3>Side by side</h3><div class="tw"><table class="cmpt"><tr><th></th>${S.map(x => `<th class="num"><button class="chead" data-copen="${esc(x.s)}" style="--c:${x.col}" title="Open ${esc(x.s)}">${tk(x)}</button></th>`).join('')}<th class="num x">ASPI</th></tr>
      ${ROWS.map(([label, spec, aspi]) => {
        if (label[0] === '#') return `<tr class="grp"><td colspan="${S.length + 1}">${label.slice(1)}</td><td class="x"></td></tr>`;
        if (spec.html) return `<tr><td>${label}</td>${S.map(x => `<td class="num">${spec.html(x)}</td>`).join('')}<td class="num x"></td></tr>`;
        const vals = S.map(x => { const v = spec.fn(x); return v == null || isNaN(v) ? null : +v; }), have = vals.filter(v => v != null);
        const best = spec.better && have.length > 1 && new Set(have).size > 1 ? (spec.better === 'high' ? Math.max(...have) : Math.min(...have)) : null;
        return `<tr><td>${label}</td>${vals.map(v => `<td class="num ${v != null && v === best ? 'best' : ''}">${v == null ? '<span class="mute">–</span>' : spec.fmt(v)}</td>`).join('')}<td class="num x">${aspi != null ? sg(aspi) : ''}</td></tr>`; }).join('')}</table></div>
      <p class="small" style="margin:6px 0 0">The best figure in a row is marked where "better" is clear. A dash means there is no checked figure. Tap a share's name to open its page.</p>`;

    // --- how closely they move together ---
    const wins = WINDOWS.filter(([w]) => S.every(x => x.d.sr && rel(x.d.sr, M, w, last)));
    if (!wins.some(w => w[0] === win)) win = wins.length ? (wins.find(w => w[0] === 156) || wins[wins.length - 1])[0] : 0;
    const E = S.map(x => ({ name: tk(x), sr: x.d.sr, col: x.col })).concat([{ name: 'ASPI', sr: M, col: 'var(--ink-3)' }]);
    const R = win ? E.map(a => E.map(b => a === b ? null : (a.sr && b.sr ? rel(a.sr, b.sr, win, last) : null))) : null;
    const rgb = hex(A.css('--sapphire')), neg = hex(A.css('--topaz'));
    const cell = c => c == null ? '<td class="num mute">–</td>' : `<td class="num" style="background:rgba(${(c.r >= 0 ? rgb : neg).join(',')},${(Math.min(1, Math.abs(c.r)) * 0.5).toFixed(2)})" title="${c.n} weeks">${c.r.toFixed(2)}</td>`;
    h += `<h3>How closely they move together</h3>`;
    if (!R) h += `<p class="mute">At least half a year of weekly prices is needed for every share chosen. One of these has too few.</p>`;
    else h += `<div class="chips">${wins.map(([w, t]) => `<button class="chip ${w === win ? 'on' : ''}" data-cwin="${w}">${t}</button>`).join('')}</div>
      <div class="tw" style="margin-top:10px"><table class="cmpt corr"><tr><th></th>${E.map(e => `<th class="num">${e.name}</th>`).join('')}</tr>
        ${E.map((a, i) => `<tr><td><b>${a.name}</b></td>${E.map((b, j) => i === j ? '<td class="num mute">1</td>' : cell(R[i][j])).join('')}</tr>`).join('')}</table></div>
      <p class="small" style="margin:6px 0 0">Correlation of weekly price changes over the last ${WINDOWS.find(w => w[0] === win)[1]}: 1 means they always moved together, 0 that they moved independently, below 0 that they tended to move opposite ways.</p>`;

    // --- what this suggests ---
    const tips = suggest(S, M, E, R, last);
    h += `<h3>What this suggests</h3><ul class="cmptips">${tips.map(t => `<li>${t}</li>`).join('')}</ul>
      <p class="small" style="margin:8px 0 0">These sentences are put together by fixed rules from the figures above. Correlations are rough guides (good to about ±0.15 on three years of weekly figures and ±0.25 on one year), they change over time, and in sharp falls most shares drop together whatever their past link. Nothing here is financial advice.</p>`;
    box.innerHTML = h;

    // the chart
    if (chart) { try { chart.remove(); } catch (e) {} chart = null; }
    try {
      chart = mkChart(el('cmpchart'), el('cmpchart').clientHeight || 300);
      if (mline.length > 1) chart.addLineSeries({ color: A.css('--ink-3'), lineWidth: 2, lineStyle: 2, title: 'ASPI', priceLineVisible: false, lastValueVisible: false }).setData(mline);
      lines.forEach(o => { if (o.pts.length > 1) chart.addLineSeries({ color: A.css(`--c${o.x.i + 1}`), lineWidth: 2, title: o.x.s.split('.')[0], priceLineVisible: false }).setData(o.pts); });
      chart.timeScale().fitContent();
    } catch (e) { el('cmpchart').innerHTML = '<p class="mute">The chart could not be drawn.</p>'; }
    if (!S.length) focusAdd();
  }
  function focusAdd() { const i = el('cmpadd'); if (i && matchMedia('(hover:hover)').matches) i.focus(); }
  // the last `weeks` of a series, rebased so its first point is 100
  function window_(s, weeks, last) {
    const pts = []; let base = null;
    for (let w = last - weeks; w <= last; w++) { const p = s.px.get(w); if (!p) continue; if (base == null) base = p[1]; pts.push({ time: p[0], value: +(p[1] / base * 100).toFixed(2) }); }
    return pts;
  }

  // ---------- the reading: fixed rules over the figures in the pop-up ----------
  function suggest(S, M, E, R, last) {
    const out = [], nm = x => `<b>${short(x.s)}</b>`, low = x => sigWord(x.row.signal || '').toLowerCase();
    const wl = win ? WINDOWS.find(w => w[0] === win)[1] : '';
    if (S.length === 1) out.push(`Add a second share to compare it with ${nm(S[0])}.`);
    // rating
    const GR = ['STRONG SELL', 'SELL', 'HOLD', 'BUY', 'STRONG BUY'], gi = x => GR.indexOf(x.row.signal);       // the rating shown first, then the score behind it
    const rated = S.filter(x => x.row.score != null).sort((a, b) => (gi(b) - gi(a)) || (b.row.score - a.row.score)), unr = S.filter(x => x.row.signal && x.row.score == null);
    if (rated.length >= 2) { const a = rated[0], z = rated[rated.length - 1];
      out.push((gi(a) === gi(z) ? `${rated.length === S.length ? 'These are all' : 'The rated shares here are all'} rated ${low(a)}; ${nm(a)} has the highest rating score (${a.row.score}, against ${z.row.score} for ${nm(z)}).`
        : `${nm(a)} has the strongest rating of these (${low(a)}) and ${nm(z)} the weakest (${low(z)}).`) + ' In the test since 2010, higher-rated shares did better on average over three months or more, by a modest margin.'); }
    else if (rated.length === 1) out.push(`${nm(rated[0])} is rated ${low(rated[0])}: ahead of ${rated[0].row.score}% of rated shares on price and volume measures.`);
    if (unr.length) out.push(`${join(unr.map(nm))} ${unr.length > 1 ? 'are' : 'is'} not rated, because ${unr.length > 1 ? 'they trade too little, are priced under LKR 5 or have under a year of prices' : esc(unr[0].row.why || 'it trades too little')}. Treat ${unr.length > 1 ? 'their' : 'its'} price figures with more care.`);
    // value
    const pes = S.filter(x => x.row.pe > 0).sort((a, b) => a.row.pe - b.row.pe), roe = S.filter(x => x.row.roe != null).sort((a, b) => b.row.roe - a.row.roe);
    if (pes.length >= 2 && pes[0].row.pe !== pes[pes.length - 1].row.pe) { const c = pes[0], d = pes[pes.length - 1];
      out.push(`On checked figures ${nm(c)} is the cheapest on earnings (${c.row.pe} times) and ${nm(d)} the dearest (${d.row.pe} times)` +
        (roe.length >= 2 ? (roe[0] === c ? `; ${nm(c)} also earns the highest return on equity (${(+roe[0].row.roe).toFixed(1)}%).` : `; ${nm(roe[0])} earns the highest return on equity (${(+roe[0].row.roe).toFixed(1)}%).`) : '.') +
        ' A low price to earnings can mean good value or a business the market expects to weaken.'); }
    else if (S.length > 1 && pes.length < 2) out.push('Fewer than two of these have a checked price to earnings figure, so they cannot be compared on value.');
    const dy = S.filter(x => x.row.div_yield >= 3).sort((a, b) => b.row.div_yield - a.row.div_yield);
    if (dy.length && S.length > 1) out.push(`${nm(dy[0])} has the highest checked dividend yield (${(+dy[0].row.div_yield).toFixed(1)}%).`);
    // against the index over a year
    const m1 = chg(M, 52, last), yr = S.map(x => ({ x, v: x.d.sr ? chg(x.d.sr, 52, last) : null })).filter(o => o.v != null);
    if (m1 != null && yr.length) { const beat = yr.filter(o => o.v > m1), lag = yr.filter(o => o.v <= m1), f = o => `${nm(o.x)} (${plain(o.v)})`;
      out.push(`Over the past year the ASPI changed ${plain(m1)}. ` + (beat.length ? `${join(beat.map(f))} did better than that` : 'None of these did better') + (lag.length ? (beat.length ? `; ${join(lag.map(f))} did worse.` : `: ${join(lag.map(f))}.`) : '.')); }
    if (R) {
      // pairs of shares
      const pairs = []; S.forEach((a, i) => S.forEach((b, j) => { if (i < j && R[i][j]) pairs.push({ a, b, r: R[i][j].r }); }));
      if (pairs.length) {
        pairs.sort((p, q) => q.r - p.r); const hi = pairs[0], lo = pairs[pairs.length - 1];
        const same = p => p.a.row.sector && p.a.row.sector === p.b.row.sector;
        if (pairs.length === 1 && hi.r < 0.45) out.push(`${nm(hi.a)} and ${nm(hi.b)} have ${how(hi.r)} (correlation ${hi.r.toFixed(2)} over ${wl})${hi.r < 0.3 ? ', so holding both spreads risk' : ''}.`);
        if (hi.r >= 0.45) out.push(`${nm(hi.a)} and ${nm(hi.b)} have ${how(hi.r)} (correlation ${hi.r.toFixed(2)} over ${wl})${same(hi) ? `, and both are in ${esc(hi.a.row.sector)}` : ''}. Holding both spreads risk less than two unrelated shares would.`);
        if (lo !== hi && lo.r < 0.3) out.push(`${nm(lo.a)} and ${nm(lo.b)} have ${how(lo.r)} (${lo.r.toFixed(2)}). As a pair they spread risk better than the others here.`);
        if (pairs.length > 1 && lo.r >= 0.45) out.push('All of these have tended to rise and fall together, so as a group they offer little spread of risk.');
        if (pairs.length > 1 && hi.r < 0.3) out.push('None of these has been closely linked to another, so as a group they spread risk well.');
      }
      // each share and the market
      const im = E.length - 1, mk = S.map((x, i) => ({ x, c: R[i][im] })).filter(o => o.c);
      if (mk.length) {
        const byB = [...mk].sort((p, q) => q.c.beta - p.c.beta), byR = [...mk].sort((p, q) => p.c.r - q.c.r);
        if (byB[0].c.beta >= 1.2) out.push(`${nm(byB[0].x)} has swung more than the market: about ${byB[0].c.beta.toFixed(1)}% for each 1% move in the ASPI. It is likely to gain more in a rising market and lose more in a falling one.`);
        if (byR[0].c.r < 0.3) out.push(`${nm(byR[0].x)} has moved mostly on its own news rather than with the market (correlation ${byR[0].c.r.toFixed(2)} with the ASPI).`);
        else { const z = byB[byB.length - 1]; if (mk.length > 1 && z.c.beta <= 0.8 && z !== byB[0]) out.push(`${nm(z.x)} has been the calmest against the market: about ${z.c.beta.toFixed(1)}% for each 1% move in the ASPI.`); }
      }
      // taken together
      if (rated.length >= 2) {
        const top = rated[0], why = [`the highest rating`], g = top.row.signal || '';
        if (top.row.invest) why.push('it passes the investing test');
        if (pes.length >= 2 && pes[0] === top) why.push('it is the cheapest on earnings');
        let t = g.includes('BUY') ? `Taken together, ${nm(top)} comes out ahead on this site's measures: ${join(why)}.`
              : g === 'HOLD' ? `Taken together, none of these is rated a buy; ${nm(top)} has the best rating of the group (hold).`
              : `Taken together, every rated share here is rated sell, so none stands out on this site's measures.`;
        if (S.length >= 3) {
          const avg = S.map((x, i) => { const v = S.map((y, j) => i !== j && R[i][j] ? R[i][j].r : null).filter(v => v != null); return { x, v: v.length ? v.reduce((p, q) => p + q, 0) / v.length : null }; }).filter(o => o.v != null).sort((p, q) => p.v - q.v);
          if (avg.length >= 3 && avg[0].v < avg[avg.length - 1].v - 0.1) t += ` ${nm(avg[0].x)} is the least like the others (average correlation ${avg[0].v.toFixed(2)}), so ${avg[0].x === top && g.includes('BUY') ? 'it also' : 'it'} adds the most variety to the group.`;
        }
        out.push(t);
      }
    }
    return out;
  }
})();
