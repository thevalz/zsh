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

/* Which draft slot picks at a given overall pick. */
function slotAt(n, draft) {
  const set = draft.settings || {};
  const teams = set.teams || D.teams;
  const round = Math.ceil(n / teams);
  const inRound = n - (round - 1) * teams;
  let reversed = draft.type === 'snake' && round % 2 === 0;
  // third-round reversal and friends flip the snake from a given round on
  const rev = set.reversal_round || 0;
  if (rev && round >= rev) reversed = !reversed;
  return { round, slot: reversed ? teams - inRound + 1 : inRound };
}

function buildOwnership(draft, traded) {
  const set = draft.settings || {};
  const teams = set.teams || D.teams, rounds = set.rounds || D.rounds;
  const slotToRoster = draft.slot_to_roster_id || {};
  const moved = new Map();
  for (const t of traded || []) moved.set(`${t.round}:${t.roster_id}`, t.owner_id);

  const own = new Map();
  for (let n = 1; n <= teams * rounds; n++) {
    const { round, slot } = slotAt(n, draft);
    // a mock has no roster ids, so fall back to the slot number itself
    const original = slotToRoster[slot] ?? slotToRoster[String(slot)] ?? slot;
    own.set(n, moved.get(`${round}:${original}`) ?? original);
  }
  return own;
}

/* Roster shape straight from the draft, so a mock with different settings is
   scored against its own lineup rather than our league's. */
function slotsFromDraft(set) {
  const out = [];
  const push = (n, label) => { for (let i = 0; i < (n || 0); i++) out.push(label); };
  push(set.slots_qb, 'QB'); push(set.slots_rb, 'RB'); push(set.slots_wr, 'WR');
  push(set.slots_te, 'TE'); push(set.slots_flex, 'FLEX');
  push(set.slots_super_flex, 'SUPER_FLEX'); push(set.slots_k, 'K'); push(set.slots_def, 'DEF');
  return out.length ? out : null;
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

/* `sticky` marks a banner that a healthy poll must not clear — the slot
   prompt is a question for the user, not a transient network complaint. */
function setBanner(html, sticky) {
  const b = el('syncBanner');
  if (!html) {
    if (SYNC.sticky) return;
    b.hidden = true; b.innerHTML = '';
    return;
  }
  SYNC.sticky = !!sticky;
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
      SYNC.sticky = false;
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

  /* Adopt the draft's own shape. A mock can have a different team count,
     round count and lineup than our league, and scoring it against our
     roster slots would give confidently wrong advice. */
  const set = SYNC.draft.settings || {};
  if (set.teams) D.teams = set.teams;
  if (set.rounds) D.rounds = set.rounds;
  const shape = slotsFromDraft(set);
  if (shape) { D.slots = shape; D.bench = set.slots_bn ?? D.bench; }

  /* Which team is ours. A league draft knows from draft_order; a mock you
     joined anonymously does not, so ?slot=N settles it. */
  const q = new URLSearchParams(location.search);
  const forced = parseInt(q.get('slot'), 10);
  const slotMap = SYNC.draft.slot_to_roster_id || {};
  let mySlot = (SYNC.draft.draft_order || {})[D.myUserId];
  if (!mySlot && forced >= 1 && forced <= (set.teams || D.teams)) mySlot = forced;
  SYNC.mySlot = mySlot || null;
  SYNC.myRosterId = mySlot ? (slotMap[mySlot] ?? slotMap[String(mySlot)] ?? mySlot) : null;

  if (SYNC.myRosterId != null) {
    SYNC.owned = [...SYNC.ownerOf.entries()]
      .filter(([, rid]) => rid === SYNC.myRosterId)
      .map(([n]) => n).sort((a, b) => a - b);
    SYNCED_PICKS = SYNC.owned;
  } else {
    setBanner('<b>Which team is yours?</b> This draft does not list your Sleeper account, '
      + 'which is normal for a mock. Pick your draft slot so the recommendations know when you are up — '
      + '<button id="bSlot" class="mini">choose slot</button>', true);
    const btn = el('bSlot');
    if (btn) btn.onclick = () => {
      const v = prompt(`Your draft slot, 1 to ${set.teams || D.teams}:`, '');
      const n = parseInt(v, 10);
      if (!(n >= 1)) return;
      const p = new URLSearchParams(location.search);
      p.set('slot', String(n));
      location.search = p.toString();
    };
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

function goToDraft(id) {
  const q = new URLSearchParams(location.search);
  if (id) q.set('draft', id); else q.delete('draft');
  q.delete('slot');                       // a different draft means a different seat
  location.search = q.toString();
}

/* Hunting a draft id out of a URL is a miserable way to start a mock, so ask
   Sleeper what drafts this account has and offer them as buttons. */
el('bSync').onclick = async () => {
  setBanner('<b>Looking for your drafts…</b>', true);
  let drafts = [];
  if (D.myUserId) {
    try {
      drafts = (await api(`/user/${D.myUserId}/drafts/nfl/${D.season || new Date().getFullYear()}`)).data || [];
    } catch (e) { /* fall through to manual entry */ }
  }
  const rows = drafts.map(d => {
    const m = d.metadata || {}, s = d.settings || {};
    const label = `${m.name || 'Mock draft'} · ${s.teams || '?'}-team ${m.scoring_type || ''} · ${d.status}`;
    return `<button class="mini" data-draft="${esc(d.draft_id)}">${esc(label)}</button>`;
  }).join(' ');
  setBanner(
    (rows ? `<b>Your drafts</b> — ${rows}<br>` : '<b>No drafts came back for this account.</b> ')
    + '<button id="bManual" class="mini">enter a draft ID</button> '
    + '<button id="bDismiss" class="mini">cancel</button>', true);

  el('bManual').onclick = () => {
    const v = prompt('Sleeper draft ID, or paste the draft URL:', SYNC.draftId || '');
    if (v === null) return;
    goToDraft((v.match(/(\d{6,})/) || [])[1]);
  };
  el('bDismiss').onclick = () => { SYNC.sticky = false; setBanner(''); };
};

addEventListener('click', e => {
  const t = e.target.closest('[data-draft]');
  if (t) goToDraft(t.dataset.draft);
});
