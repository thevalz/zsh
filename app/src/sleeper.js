/* sleeper.js — read the draft board straight from Sleeper.

   Sleeper serves this API with `access-control-allow-origin: *`, so the browser
   can talk to it directly and the app needs no backend at all. Two wrinkles:

   - Responses sit behind a 30-second CDN cache (`s-maxage=30`), which is too
     stale to draft against. A nonce in the query string forces a cache MISS.
   - That would make every poll a full download, so we pair it with
     `If-None-Match`. Unchanged boards come back as a 304 with no body.

   With a 90-second pick timer, polling every 4-10s is responsive and costs
   well under Sleeper's documented 1000 requests/minute. */

const API = 'https://api.sleeper.app/v1';

const SYNC = {
  on: false, degraded: false, draftId: null, draft: null,
  etag: null, picks: [], err: 0, timer: null, lastAt: 0,
  myRosterId: null,
  ownerOf: new Map(),   // overall pick -> roster_id that owns it
  rosters: new Map(),   // roster_id -> [player objects drafted so far]
  teamName: new Map(),
  note: ''
};

/* sync is the source of truth unless it has fallen over */
const syncAuthoritative = () => SYNC.on && !SYNC.degraded;
const liveRosters = () => SYNC.on && SYNC.ownerOf.size > 0;

const TIMEOUT_MS = 8000;

/* A request that never settles is worse than one that fails: without a
   deadline the app sits on "offline" forever and never says why. */
async function api(path, opts = {}) {
  const nonce = Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
  const url = `${API}${path}${path.includes('?') ? '&' : '?'}_=${nonce}`;
  const headers = opts.etag ? { 'If-None-Match': opts.etag } : {};
  const ctrl = new AbortController();
  const bell = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  let r;
  try {
    r = await fetch(url, { headers, signal: ctrl.signal });
  } catch (e) {
    throw new Error(e.name === 'AbortError'
      ? `${path.split('?')[0]} timed out after ${TIMEOUT_MS / 1000}s`
      : `${path.split('?')[0]} unreachable`);
  } finally {
    clearTimeout(bell);
  }
  if (r.status === 304) return { status: 304 };
  if (!r.ok) throw new Error(`${path.split('?')[0]} → HTTP ${r.status}`);
  return { status: r.status, etag: r.headers.get('etag'), data: await r.json() };
}

/* ---- discovery ---------------------------------------------------------- */

async function discoverDraft() {
  const q = new URLSearchParams(location.search);
  if (q.get('draft')) return q.get('draft');
  const league = q.get('league') || D.leagueId;
  if (league) {
    try {
      const drafts = (await api(`/league/${league}/drafts`)).data || [];
      const live = drafts.find(d => d.status === 'drafting') || drafts[0];
      if (live) return live.draft_id;
    } catch (e) { /* fall through to the configured draft */ }
  }
  return D.draftId || null;
}

/* ---- who owns which pick ------------------------------------------------ */

function buildOwnership(draft, traded) {
  const set = draft.settings || {};
  const teams = set.teams || D.teams, rounds = set.rounds || D.rounds;
  const slotToRoster = draft.slot_to_roster_id || {};
  const moved = new Map();
  for (const t of traded || []) moved.set(`${t.round}:${t.roster_id}`, t.owner_id);

  const own = new Map();
  for (let n = 1; n <= teams * rounds; n++) {
    const round = Math.ceil(n / teams);
    const inRound = n - (round - 1) * teams;
    const reversed = draft.type === 'snake' && round % 2 === 0;
    const slot = reversed ? teams - inRound + 1 : inRound;
    const original = slotToRoster[slot] ?? slotToRoster[String(slot)];
    own.set(n, moved.get(`${round}:${original}`) ?? original);
  }
  return own;
}

/* Roster a team holds at the moment — used by the need-aware simulation. */
const ownerOfPick = n => SYNC.ownerOf.get(n) ?? null;
function rosterForPick(n) {
  const rid = ownerOfPick(n);
  return rid == null ? null : (SYNC.rosters.get(rid) || []);
}

/* ---- applying picks ----------------------------------------------------- */

function pickName(pk) {
  const byIdName = byId.get(String(pk.player_id));
  if (byIdName) return byIdName;
  const m = pk.metadata || {};
  const joined = [m.first_name, m.last_name].filter(Boolean).join(' ').trim();
  return joined || null;
}

/* Rebuilt from scratch each poll rather than accumulated, so a dropped poll or
   a reverted pick can never leave the board out of step with Sleeper. */
function applyPicks(picks) {
  SYNC.picks = picks;
  SYNC.rosters = new Map();
  S.drafted = {};
  S.hist = [];
  let unknown = 0;
  const made = new Set();

  for (const pk of picks) {
    if (pk.pick_no) made.add(pk.pick_no);
    const rid = pk.roster_id ?? SYNC.ownerOf.get(pk.pick_no);
    const name = pickName(pk);
    if (!name || !byName.has(name)) { unknown++; continue; }
    S.drafted[name] = rid === SYNC.myRosterId ? 'me' : 'them';
    if (!SYNC.rosters.has(rid)) SYNC.rosters.set(rid, []);
    SYNC.rosters.get(rid).push(byName.get(name));
  }

  // Keepers are pre-placed at their own pick numbers, so the board is not
  // necessarily contiguous — the live pick is the first slot nobody has used.
  let n = 1;
  while (made.has(n)) n++;
  S.pick = n;
  SYNC.made = made;
  SYNCED_MADE = made;
  SYNC.note = unknown ? `${unknown} pick${unknown > 1 ? 's' : ''} outside the ranked pool` : '';
  save();
}

/* ---- polling ------------------------------------------------------------ */

function setBanner(html) {
  const b = el('syncBanner');
  if (!html) { b.hidden = true; b.innerHTML = ''; return; }
  b.hidden = false;
  b.innerHTML = html;
}

async function pollOnce() {
  try {
    const r = await api(`/draft/${SYNC.draftId}/picks`, { etag: SYNC.etag });
    SYNC.err = 0;
    SYNC.degraded = false;
    SYNC.lastAt = Date.now();
    setBanner('');
    if (r.status === 304) { renderSync(); return false; }
    SYNC.etag = r.etag;
    applyPicks(r.data || []);
    render();
    return true;
  } catch (e) {
    SYNC.err++;
    if (SYNC.err >= 3 && !SYNC.degraded) {
      SYNC.degraded = true;
      setBanner(`<b>Lost contact with Sleeper</b> — ${esc(e.message)}. `
        + 'Marking picks by hand works again in the meantime; the board re-syncs on its own when the connection comes back.');
    }
    renderSync();
    return false;
  }
}

function pollDelay() {
  if (SYNC.err) return Math.min(60000, 4000 * 2 ** (SYNC.err - 1));
  if (SYNC.draft && SYNC.draft.status === 'complete') return null;
  const away = (nextPick() ?? 999) - S.pick;
  return away <= 6 ? 4000 : 10000;
}

function loop() {
  clearTimeout(SYNC.timer);
  const d = pollDelay();
  if (d === null) return;
  SYNC.timer = setTimeout(async () => { await pollOnce(); loop(); }, d);
}

/* ---- boot --------------------------------------------------------------- */

async function startSync() {
  let id;
  try { id = await discoverDraft(); } catch (e) { id = null; }
  if (!id) { renderSync(); return; }
  SYNC.draftId = id;

  try {
    SYNC.draft = (await api(`/draft/${id}`)).data;
  } catch (e) {
    setBanner(`<b>Could not open draft ${esc(id)}</b> — ${esc(e.message)}. Marking picks by hand still works.`);
    renderSync();
    return;
  }

  let traded = [];
  try { traded = (await api(`/draft/${id}/traded_picks`)).data || []; } catch (e) {}
  SYNC.ownerOf = buildOwnership(SYNC.draft, traded);

  const mySlot = (SYNC.draft.draft_order || {})[D.myUserId];
  const slotMap = SYNC.draft.slot_to_roster_id || {};
  SYNC.myRosterId = mySlot ? (slotMap[mySlot] ?? slotMap[String(mySlot)]) : null;

  if (SYNC.myRosterId != null) {
    SYNC.owned = [...SYNC.ownerOf.entries()]
      .filter(([, rid]) => rid === SYNC.myRosterId)
      .map(([n]) => n).sort((a, b) => a - b);
    SYNCED_PICKS = SYNC.owned;
  }

  // team names are a nicety and absent from standalone mocks
  const lid = SYNC.draft.league_id;
  if (lid) {
    try {
      const users = (await api(`/league/${lid}/users`)).data || [];
      const rosters = (await api(`/league/${lid}/rosters`)).data || [];
      const uname = new Map(users.map(u => [u.user_id, (u.metadata || {}).team_name || u.display_name]));
      rosters.forEach(r => SYNC.teamName.set(r.roster_id, uname.get(r.owner_id) || `Roster ${r.roster_id}`));
    } catch (e) {}
  }

  SYNC.on = true;
  el('bKeep').hidden = true;   // keepers arrive as picks now; no need to model them
  el('bMock').hidden = true;   // pick list comes from the draft itself
  await pollOnce();
  loop();
}

function renderSync() {
  const pill = el('syncPill');
  if (!SYNC.on) { pill.className = 'pill off'; pill.textContent = 'offline'; return; }
  if (SYNC.degraded) { pill.className = 'pill warn'; pill.textContent = 'retrying'; return; }
  const status = (SYNC.draft && SYNC.draft.status) || '';
  pill.className = 'pill live';
  pill.textContent = status === 'complete' ? 'draft complete'
    : `live · ${SYNC.picks.length} picks${SYNC.note ? ' · ' + SYNC.note : ''}`;
}

el('bSync').onclick = () => {
  const cur = new URLSearchParams(location.search).get('draft') || SYNC.draftId || '';
  const v = prompt('Sleeper draft ID (or a sleeper.com draft URL) — blank to use the league default:', cur);
  if (v === null) return;
  const id = (v.match(/(\d{6,})/) || [])[1];
  const q = new URLSearchParams(location.search);
  if (id) q.set('draft', id); else q.delete('draft');
  location.search = q.toString();
};
