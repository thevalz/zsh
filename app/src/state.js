/* state.js — data, persistence, and the arithmetic of whose pick it is.
   Concatenated first; model.js and ui.js depend on what it defines. */

const D = JSON.parse(document.getElementById('DATA').textContent);
const P = D.players.map((p, i) => ({ ...p, i }));
const byName = new Map(P.map(p => [p.n, p]));
const byId = new Map(Object.entries(D.idToName || {}));

/* A plain slot-N snake, used when we have no draft to sync against. */
const snakePicks = (slot, teams = D.teams, rounds = D.rounds) => {
  const a = [];
  for (let r = 1; r <= rounds; r++) a.push((r - 1) * teams + (r % 2 ? slot : teams - slot + 1));
  return a;
};
const MOCK_PICKS = snakePicks(D.mySlot);

const KEY = 'zebras-draft-v2';
let S = { drafted: {}, hist: [], pick: 1, mock: false, hide: true, filter: 'ALL', sort: 'fit' };
try { const r = localStorage.getItem(KEY); if (r) S = { ...S, ...JSON.parse(r) }; } catch (e) {}
const save = () => { try { localStorage.setItem(KEY, JSON.stringify(S)); } catch (e) {} };

/* Pick list: a synced draft supplies its own, otherwise fall back to config.
   Keepers occupy real pick slots, so an owned pick that has already been used
   is no longer a pick you get to make. */
let SYNCED_PICKS = null;   // every pick number you own
let SYNCED_MADE = null;    // Set of pick numbers already spent, keepers included
const myPicks = () => {
  if (SYNCED_PICKS) return SYNCED_MADE ? SYNCED_PICKS.filter(n => !SYNCED_MADE.has(n)) : SYNCED_PICKS;
  return S.mock ? MOCK_PICKS : D.myPicks;
};

const avail = () => P.filter(p => !S.drafted[p.n]).sort((a, b) => a.a - b.a);
const mine = () => P.filter(p => S.drafted[p.n] === 'me');

function nextPick() { return myPicks().find(x => x >= S.pick) ?? null; }
function onClock() { return myPicks().includes(S.pick); }

/* The pick your decision is measured against: if you're on the clock it's the
   one after this; if you're waiting it's the one you're waiting for. */
function target() {
  const n = nextPick();
  if (n === null) return null;
  return n === S.pick ? (myPicks().find(x => x > n) ?? null) : n;
}
/* How many picks other people make before you choose again. When you are on
   the clock your own pick sits at S.pick and must not be counted. */
function horizon() {
  const t = target();
  if (t === null) return 999;
  return Math.max(0, t - S.pick - (onClock() ? 1 : 0));
}

function mark(name, who) {
  // while a draft is synced the API owns the board; a hand mark would only be
  // undone by the next poll, so ignore it rather than flicker
  if (typeof syncAuthoritative === 'function' && syncAuthoritative()) return;
  if (S.drafted[name]) return;
  S.drafted[name] = who;
  S.hist.push({ n: name, w: who, pick: S.pick });
  S.pick++;
  save(); render();
}
function undo() {
  const h = S.hist.pop();
  if (!h) return;
  delete S.drafted[h.n];
  S.pick = h.pick;
  save(); render();
}

const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const el = id => document.getElementById(id);
