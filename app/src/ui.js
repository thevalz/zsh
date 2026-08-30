/* ui.js — render and events. Concatenated last; boots the app at the bottom. */

const TIER = pos => (pos === 'QB' ? 45 : pos === 'TE' ? 110 : 90);

function renderRecs() {
  const R = computeRecs();
  el('recs').innerHTML = R.map((r, i) => {
    const why = r.fills
      ? `fills your <b>${r.fills === 'K' ? 'kicker' : r.fills.replace('_', ' ').toLowerCase()}</b>`
      : '<span class="depth">bench depth</span>';
    const t = r.tier ? `<span class="tierchip">${r.pos} tier ${r.tier.tier}</span>` : '';
    const bz = r.buzz >= 25 ? `<span class="buzzchip">rising ${r.buzz}</span>` : '';
    const rk = isRookie(r.p) ? '<span class="rookiechip">rookie</span>' : '';
    const wait = !r.nxt ? `<b>nobody left at ${r.pos}</b> after this run`
      : r.tier && r.tier.survives === 0
        ? `<b class="warnTxt">tier ${r.tier.tier} is gone</b> before your next pick`
      : r.nxt.n === r.p.n ? 'should survive to your next pick — <b>no rush</b>'
      : `wait and you drop to <b>${esc(r.nxt.n)}</b> — <b>${r.urgency.toFixed(0)} spots</b>`;
    const val = r.bargain > 0 ? `<span class="edge-pos">${r.bargain.toFixed(0)} spots of value</span>`
      : `<span class="edge-neg">reaching ${(r.p.a - S.pick).toFixed(0)}</span>`;
    return `<div class="rec${i ? '' : ' top'}"><div class="rank">${i + 1}</div><div class="rec-main">
      <div class="rec-name"><span class="badge b-${r.p.p}">${r.p.p}</span>${esc(r.p.n)} ${t}${rk}${bz}</div>
      <div class="rec-why">${why} · ADP ${r.p.a.toFixed(0)} · ${val}</div>
      <div class="rec-why">${wait}</div></div>
      <button class="take mini" data-take="${esc(r.p.n)}">Draft</button></div>`;
  }).join('') || '<div class="rec"><div class="rec-main"><div class="rec-why">Board is empty.</div></div></div>';
}

function renderRunway(tgt) {
  const board = avail(), surv = survivors();
  el('scar').innerHTML = ['QB', 'RB', 'WR', 'TE'].map(pos => {
    const ts = tierState(pos, board, surv);
    if (!ts) return `<div class="scar"><div class="scar-hd"><span><span class="badge b-${pos}">${pos}</span> none left</span></div></div>`;
    const col = ts.survives === 0 ? 'var(--neg)' : ts.survives === 1 ? 'var(--hot)' : 'var(--pos)';
    const pct = Math.min(100, ts.survives / Math.max(1, ts.left) * 100);
    const who = ts.members.slice(0, 4).map(p => esc(p.n.split(' ').slice(-1)[0])).join(', ')
      + (ts.left > 4 ? ` +${ts.left - 4}` : '');
    const note = ts.survives === 0
      ? `<span class="warnTxt">All gone before pick ${tgt ?? '—'}.</span> Next is tier ${ts.tier + 1}.`
      : ts.survives === 1 ? `Only one reaches pick ${tgt ?? '—'}.`
      : `${ts.survives} of them reach pick ${tgt ?? '—'}.`;
    return `<div class="scar"><div class="scar-hd">
        <span><span class="badge b-${pos}">${pos}</span> <b>tier ${ts.tier}</b> · ${ts.left} left</span>
        <b style="color:${col}">${ts.survives} survive</b></div>
      <div class="meter"><i style="width:${pct}%;background:${col}"></i></div>
      <div class="scar-note">${who}</div>
      <div class="scar-note">${note}</div></div>`;
  }).join('');
}

const line = p => `<span class="badge b-${p.p}">${p.p}</span><span>${esc(p.n)}</span>`
  + `<span class="spacer"></span><span class="mono" style="color:var(--faint);font-size:11px">${p.a.toFixed(0)}</span>`;

function renderRoster() {
  const { slots, bench } = fill(mine());
  el('roster').innerHTML = slots.map(s =>
    `<div class="slotrow"><span class="slotlab">${s.s}</span>${s.p ? line(s.p) : '<span class="empty">open</span>'}</div>`).join('');
  el('bench').innerHTML = bench.length
    ? bench.map(p => `<div class="slotrow">${line(p)}</div>`).join('')
    : `<div class="slotrow"><span class="empty">empty — ${D.bench} spots</span></div>`;
}

function renderBoard() {
  el('filters').innerHTML = ['ALL', 'QB', 'RB', 'WR', 'TE', 'PK', 'DEF'].map(x =>
    `<button class="mini${S.filter === x ? ' on' : ''}" data-f="${x}">${x}</button>`).join(' ')
    + ` <button class="mini${S.sort === 'fit' ? ' on' : ''}" data-sort="fit">need+value</button>`
    + `<button class="mini${S.sort === 'adp' ? ' on' : ''}" data-sort="adp">ADP</button>`;

  const q = el('q').value.trim().toLowerCase();
  const roster = mine(), left = myPicks().filter(x => x >= S.pick).length;
  const surv = S.sort === 'fit' ? survivors() : null;
  const rows = P.filter(p => {
    if (S.hide && S.drafted[p.n]) return false;
    if (S.filter !== 'ALL' && p.p !== S.filter) return false;
    if (q && !p.n.toLowerCase().includes(q) && !p.t.toLowerCase().includes(q)) return false;
    return true;
  });
  if (S.sort === 'fit') {
    rows.forEach(p => { p._fit = fitScore(p, roster, left, surv); });
    rows.sort((a, b) => b._fit - a._fit || a.a - b.a);
  } else {
    rows.sort((a, b) => a.a - b.a);
  }
  const top = rows.length ? Math.max(...rows.slice(0, 60).map(p => p._fit || 0)) : 1;

  el('tb').innerHTML = rows.slice(0, 300).map(p => {
    const st = S.drafted[p.n], e = S.pick - p.a;
    const fitPct = top > 0 ? Math.round(100 * (p._fit || 0) / top) : 0;
    const fitCell = S.sort === 'fit'
      ? `<td><span class="fitbar"><i style="width:${fitPct}%"></i></span></td>` : '<td></td>';
    return `<tr class="row ${st === 'me' ? 'mineRow' : ''} ${st ? 'gone' : ''}" data-n="${esc(p.n)}">
      <td><span class="badge b-${p.p}">${p.p}</span>${esc(p.n)}${isRookie(p) ? '<span class="rookiechip">R</span>' : ''}</td>
      <td>${p.t}</td><td>${p.b ?? '—'}</td><td>${p.a.toFixed(1)}${p.est ? '~' : ''}</td>
      <td>${p.vor != null ? (p.vor >= 0 ? '+' : '') + p.vor.toFixed(0) + (p.vest ? '~' : '') : '—'}</td>
      <td class="${e >= 0 ? 'edge-pos' : 'edge-neg'}">${e >= 0 ? '+' : ''}${e.toFixed(0)}</td>
      ${fitCell}
      <td>${st ? (st === 'me' ? 'yours' : 'gone') : `<button class="mini" data-me="${esc(p.n)}">mine</button>`}</td></tr>`;
  }).join('');
}

/* Who picks before you do, and what they still need — the same signal the
   simulation uses, shown so you can sanity-check what it is telling you. */
function renderOpponents() {
  const card = el('oppCard');
  if (typeof liveRosters !== 'function' || !liveRosters()) { card.hidden = true; return; }
  card.hidden = false;

  const tgt = target();
  const nextFor = new Map();          // roster_id -> its next pick number
  for (const [n, rid] of SYNC.ownerOf) {
    if (n < S.pick || (SYNC.made && SYNC.made.has(n))) continue;
    if (!nextFor.has(rid) || n < nextFor.get(rid)) nextFor.set(rid, n);
  }

  const ids = [...new Set(SYNC.ownerOf.values())].filter(x => x != null);
  ids.sort((a, b) => (nextFor.get(a) ?? 1e9) - (nextFor.get(b) ?? 1e9));

  el('opps').innerHTML = ids.map(rid => {
    const roster = SYNC.rosters.get(rid) || [];
    const at = nextFor.get(rid);
    const soon = at != null && tgt != null && at < tgt;
    const isMe = rid === SYNC.myRosterId;
    const { slots } = fill(roster);
    const open = slots.filter(s => !s.p && !['K', 'DEF'].includes(s.s)).map(s => s.s);
    const counts = ['QB', 'RB', 'WR', 'TE'].map(p => {
      const n = roster.filter(x => x.p === p).length;
      return `<span class="cnt b-${p}${n ? '' : ' zero'}">${p} ${n}</span>`;
    }).join('');
    const name = SYNC.teamName.get(rid) || `Roster ${rid}`;
    return `<div class="opp${soon ? ' soon' : ''}${isMe ? ' me' : ''}">
      <div class="opp-hd"><span class="opp-name">${esc(isMe ? name + ' (you)' : name)}</span>
        <span class="opp-when">${at != null ? 'pick ' + at : 'done'}</span></div>
      <div class="opp-counts">${counts}</div>
      <div class="opp-need">${open.length ? 'needs <b>' + esc([...new Set(open)].join(', ')) + '</b>' : 'starters full'}</div>
    </div>`;
  }).join('');
}

/* Players the market is moving on right now, still on the board. Separate
   from the recommendations on purpose — this is a watchlist, not advice. */
function renderRising() {
  const rows = avail().map(p => ({ p, b: buzz(p) }))
    .filter(x => x.b >= 25)
    .sort((a, x) => x.b - a.b || a.p.a - x.p.a)
    .slice(0, 8);
  el('risingCard').hidden = rows.length === 0;
  el('rising').innerHTML = rows.map(({ p, b }) => {
    const why = [];
    if (p.vel > 0) why.push(`up ${p.vel.toFixed(0)} spots`);
    if (p.add) why.push(`${(p.add / 1000).toFixed(0)}k adds`);
    return `<div class="risebar" data-me="${esc(p.n)}" title="click to draft">
      <div class="rise-hd"><span><span class="badge b-${p.p}">${p.p}</span>${esc(p.n)}
        ${isRookie(p) ? '<span class="rookiechip">rookie</span>' : ''}</span>
        <b class="mono">${b}</b></div>
      <div class="meter"><i style="width:${b}%;background:var(--rise)"></i></div>
      <div class="scar-note">ADP ${p.a.toFixed(0)}${p.est ? '~' : ''} · ${why.join(' · ') || 'trending'}</div>
    </div>`;
  }).join('');
}

function render() {
  const n = nextPick(), tgt = target(), clock = onClock();
  el('sPick').textContent = S.pick;
  el('sNext').textContent = n ?? '—';
  el('sGap').textContent = clock ? 'now' : (n ? n - S.pick : '—');
  el('sLeft').textContent = myPicks().filter(x => x >= S.pick).length;
  el('bar').classList.toggle('clock', clock);
  el('clockMsg').textContent = clock ? "YOU'RE ON THE CLOCK" : '';
  el('bMock').classList.toggle('on', S.mock);
  el('bMock').textContent = S.mock ? 'Mock mode: ON' : 'Mock mode';
  el('bHide').classList.toggle('on', S.hide);

  renderRecs();
  renderRunway(tgt);
  renderRoster();
  renderBoard();
  renderRising();
  renderOpponents();
  if (typeof renderSync === 'function') renderSync();

  el('foot').innerHTML =
    'Click a row to mark a player gone. <kbd>Enter</kbd> in search marks the top hit gone; '
    + '<kbd>Shift</kbd>+<kbd>Enter</kbd> drafts him to you. '
    + `Values are ${esc(D.meta.src)}. State saves in this browser.`;
}

/* ---- events ---- */
addEventListener('click', e => {
  const t = e.target.closest('[data-take]') || e.target.closest('[data-me]');
  if (t) { mark(t.dataset.take || t.dataset.me, 'me'); e.stopPropagation(); return; }
  const f = e.target.closest('[data-f]');
  if (f) { S.filter = f.dataset.f; save(); render(); return; }
  const so = e.target.closest('[data-sort]');
  if (so) { S.sort = so.dataset.sort; save(); render(); return; }
  const r = e.target.closest('tr.row');
  if (r && !S.drafted[r.dataset.n]) mark(r.dataset.n, 'them');
});
el('q').addEventListener('input', render);
el('q').addEventListener('keydown', e => {
  if (e.key !== 'Enter') return;
  const first = document.querySelector('#tb tr.row:not(.gone)');
  if (!first) return;
  mark(first.dataset.n, e.shiftKey ? 'me' : 'them');
  el('q').value = '';
  render();
});
el('bUndo').onclick = undo;
el('bHide').onclick = () => { S.hide = !S.hide; save(); render(); };
el('bMock').onclick = () => { S.mock = !S.mock; S.drafted = {}; S.hist = []; S.pick = 1; save(); render(); };
el('bReset').onclick = () => {
  if (confirm('Clear the whole board and start over?')) {
    S.drafted = {}; S.hist = []; S.pick = 1; save(); render();
  }
};
/* Modelled keepers, for use before the real ones are declared. Once a draft is
   synced the keepers arrive as picks and this button hides itself. */
el('bKeep').onclick = () => {
  (D.keepers || []).forEach(k => { if (byName.has(k.n) && !S.drafted[k.n]) S.drafted[k.n] = 'them'; });
  (D.mine || []).forEach(n => { if (byName.has(n)) S.drafted[n] = 'me'; });
  save(); render();
};

render();
if (typeof startSync === 'function') startSync();
