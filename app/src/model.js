/* model.js — roster shape, positional need, and the value of waiting.

   The only interesting idea here is `survivors()`. Asking "who is still on the
   board at my next pick" is what makes a recommendation actionable, and the
   honest answer depends on what the teams picking in between actually need —
   not on ADP order alone. */

const FLEX = ['RB', 'WR', 'TE'];
const SF = ['QB', 'RB', 'WR', 'TE'];

/* Greedily seat players into dedicated slots, then FLEX, then SUPER_FLEX.
   Works unchanged on an opponent's roster, which is what makes the
   need-aware simulation below possible. */
function fill(list) {
  const slots = D.slots.map(s => ({ s, p: null }));
  const pool = [...list].sort((a, b) => a.a - b.a);
  const grab = t => { const i = pool.findIndex(p => t.includes(p.p)); return i < 0 ? null : pool.splice(i, 1)[0]; };
  for (const sl of slots) if (!['FLEX', 'SUPER_FLEX'].includes(sl.s)) sl.p = grab([sl.s]);
  for (const sl of slots) if (sl.s === 'FLEX') sl.p = grab(FLEX);
  for (const sl of slots) if (sl.s === 'SUPER_FLEX') sl.p = grab(SF);
  return { slots, bench: pool };
}

/* How badly a roster wants another player at `pos`. */
function needFor(roster, pos, picksLeft) {
  const { slots } = fill(roster);
  const open = s => slots.some(x => x.s === s && !x.p);
  if (pos === 'PK' || pos === 'DEF') {
    const slot = pos === 'PK' ? 'K' : 'DEF';
    return open(slot) ? (picksLeft <= 3 ? 1.3 : 0.02) : 0.01;
  }
  if (open(pos)) return 1;
  if (FLEX.includes(pos) && open('FLEX')) return 0.8;
  if (SF.includes(pos) && open('SUPER_FLEX')) return pos === 'QB' ? 0.95 : 0.7;
  const depth = roster.filter(p => p.p === pos).length;
  return depth >= 4 ? 0.12 : 0.34;
}

const needWeight = pos => needFor(mine(), pos, myPicks().filter(x => x >= S.pick).length);

/* Who is left when my next pick comes round.

   With live rosters we simulate: each intervening team takes the best of the
   top few available, tilted toward a position it still needs. Without them we
   fall back to straight ADP order, which is the same assumption the manual
   console made. */
function survivors() {
  const board = avail();
  const gap = horizon();
  if (gap <= 0 || gap >= 900) return board;
  // no live rosters means no need signal — fall back to straight ADP order
  if (typeof liveRosters !== 'function' || !liveRosters()) return board.slice(gap);

  const pool = [...board];
  const since = new Map();               // roster_id -> what it took inside this window
  const from = S.pick + (onClock() ? 1 : 0);
  for (let n = 0; n < gap && pool.length; n++) {
    const at = from + n;
    const rid = typeof ownerOfPick === 'function' ? ownerOfPick(at) : null;
    const roster = (rosterForPick(at) || []).concat(since.get(rid) || []);
    const left = Math.max(1, D.rounds - Math.ceil(at / D.teams) + 1);

    /* Compare the best player at each position rather than the first N on the
       board. In superflex the top of the board goes QB-heavy, so a fixed
       window can contain nothing but quarterbacks — and a team that is set at
       QB would then be modelled as taking one anyway. Decay is slow, so need
       carries unless the value gap is very large (~40 spots). */
    let best = 0, bestScore = -Infinity;
    const seen = new Set();
    for (let i = 0; i < pool.length && seen.size < 6; i++) {
      const pos = pool[i].p;
      if (seen.has(pos)) continue;
      seen.add(pos);
      const score = needFor(roster, pos, left) / (1 + i * 0.05);
      if (score > bestScore) { bestScore = score; best = i; }
    }
    const taken = pool.splice(best, 1)[0];
    if (rid != null) {
      if (!since.has(rid)) since.set(rid, []);
      since.get(rid).push(taken);
    }
  }
  return pool;
}

function vona() {
  const a = avail(), surv = survivors(), out = {};
  for (const pos of ['QB', 'RB', 'WR', 'TE', 'PK', 'DEF']) {
    const now = a.find(p => p.p === pos), nxt = surv.find(p => p.p === pos);
    if (!now) continue;
    out[pos] = { now, nxt, delta: nxt ? nxt.a - now.a : 120 };
  }
  return out;
}

function computeRecs() {
  const v = vona(), out = [];
  for (const pos in v) {
    const { now, nxt, delta } = v[pos], w = needWeight(pos), edge = S.pick - now.a;
    out.push({ p: now, pos, w, delta, edge, nxt, score: w * (0.6 * delta + 0.4 * edge) });
  }
  // a kicker or defense is never a real recommendation until the very end
  const live = out.filter(r => !(['PK', 'DEF'].includes(r.pos) && r.w < 0.5));
  const use = live.length ? live : out;
  use.sort((x, y) => y.score - x.score);
  return use.slice(0, 3);
}
