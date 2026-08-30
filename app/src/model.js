/* model.js — roster shape, positional need, tiers, and what to take next.

   Two ideas carry the whole file:

   1. `survivors()` — who is still on the board when you next pick. The honest
      answer depends on what the teams picking in between need, not on ADP
      order alone, so we simulate them from their live rosters.

   2. Every term in the recommendation score is non-negative. An earlier
      version scored `need x (0.6*delta + 0.4*edge)`, where `edge` was
      `pick - adp`. Mid-draft that bracket goes negative, and multiplying a
      negative by a *larger* need makes it *smaller* — so need inverted and
      positions you had already filled floated to the top exactly when the
      board thinned. Nothing below may go negative. */

const FLEX = ['RB', 'WR', 'TE'];
const SF = ['QB', 'RB', 'WR', 'TE'];

/* Greedily seat players into dedicated slots, then FLEX, then SUPER_FLEX.
   Works unchanged on an opponent's roster, which is what makes the
   need-aware simulation possible. */
function fill(list) {
  const slots = D.slots.map(s => ({ s, p: null }));
  const pool = [...list].sort((a, b) => a.a - b.a);
  const grab = t => { const i = pool.findIndex(p => t.includes(p.p)); return i < 0 ? null : pool.splice(i, 1)[0]; };
  // the lineup calls the slot K; the ADP feed calls the position PK. Without
  // this the kicker slot never fills and the model keeps asking for another.
  const eligible = s => (s === 'K' ? ['K', 'PK'] : [s]);
  for (const sl of slots) if (!['FLEX', 'SUPER_FLEX'].includes(sl.s)) sl.p = grab(eligible(sl.s));
  for (const sl of slots) if (sl.s === 'FLEX') sl.p = grab(FLEX);
  for (const sl of slots) if (sl.s === 'SUPER_FLEX') sl.p = grab(SF);
  return { slots, bench: pool };
}

/* How many lineup slots a position could ever occupy. Two quarterbacks fill
   QB and SUPER_FLEX in this league, and a third starts nowhere. */
function startableSlots(pos) {
  return D.slots.filter(s =>
    s === pos
    || (s === 'K' && pos === 'PK')
    || (s === 'FLEX' && FLEX.includes(pos))
    || (s === 'SUPER_FLEX' && SF.includes(pos))).length;
}

/* Need is a positive weight in [0,1]. Anything at or above STARTER_NEED fills
   a hole in the starting lineup; below it the pick is bench depth. */
const STARTER_NEED = 0.5;

function needFor(roster, pos, picksLeft) {
  const { slots } = fill(roster);
  const open = s => slots.some(x => x.s === s && !x.p);
  if (pos === 'PK' || pos === 'DEF') {
    const slot = pos === 'PK' ? 'K' : 'DEF';
    // a kicker is only ever the last thing you do
    return open(slot) ? (picksLeft <= 3 ? 1 : 0.02) : 0.01;
  }
  if (open(pos)) return 1;
  if (FLEX.includes(pos) && open('FLEX')) return 0.85;
  if (SF.includes(pos) && open('SUPER_FLEX')) return pos === 'QB' ? 0.95 : 0.75;

  // every slot this position can start in is taken; from here it is depth
  const depth = roster.filter(p => p.p === pos).length;
  const surplus = Math.max(0, depth - startableSlots(pos));
  return Math.max(0.02, 0.14 / (1 + surplus));
}

const needWeight = pos => needFor(mine(), pos, myPicks().filter(x => x >= S.pick).length);

/* Which lineup slot this position would actually fill, or null for depth.
   Shown on every recommendation so the reasoning is visible. */
function fillsSlot(roster, pos) {
  const { slots } = fill(roster);
  const open = s => slots.some(x => x.s === s && !x.p);
  if (pos === 'PK') return open('K') ? 'K' : null;
  if (pos === 'DEF') return open('DEF') ? 'DEF' : null;
  if (open(pos)) return pos;
  if (FLEX.includes(pos) && open('FLEX')) return 'FLEX';
  if (SF.includes(pos) && open('SUPER_FLEX')) return 'SUPER_FLEX';
  return null;
}

/* ---- tiers ---------------------------------------------------------------
   A tier is a run of players the market treats as interchangeable. Break the
   run where the gap to the next player is large relative to how precisely the
   market has ranked them — the stdev of their ADP is exactly that measure, and
   FFC gives it to us per player. */
const TIERS = (() => {
  const out = new Map();
  for (const pos of ['QB', 'RB', 'WR', 'TE', 'PK', 'DEF']) {
    const list = P.filter(p => p.p === pos).sort((a, b) => a.a - b.a);
    let tier = 1;
    list.forEach((p, i) => {
      if (i > 0) {
        const prev = list[i - 1];
        const spread = ((prev.sd || 6) + (p.sd || 6)) / 2;
        if (p.a - prev.a > Math.max(4, spread * 0.9)) tier++;
      }
      out.set(p.n, tier);
    });
  }
  return out;
})();
const tierOf = p => TIERS.get(p.n) || 0;

/* ---- buzz ----------------------------------------------------------------
   ADP is a lagging average — by the time a breakout shows up in it, the price
   has already moved. Two independent live signals catch the move in progress:
   how fast a player's ADP is climbing, and how hard he is being added across
   Sleeper. Neither is trustworthy alone; both pointing the same way is.

   This is deliberately NOT allowed to drive a pick. It breaks ties inside a
   tier and it fills the Rising panel. Need and scarcity still decide. */
const ADD_MAX = Math.max(1, ...P.map(p => p.add || 0));
function buzz(p) {
  const climb = Math.min(1, Math.max(0, p.vel || 0) / 18);   // 18 spots ~ maxed
  const adds = Math.min(1, (p.add || 0) / ADD_MAX);
  if (!climb && !adds) return 0;
  // reward corroboration: agreeing signals score far above one loud one
  const hi = Math.max(climb, adds), lo = Math.min(climb, adds);
  return Math.round(Math.min(100, 100 * (0.6 * hi + 0.4 * lo)));
}
const isRookie = p => !!p.rk;

/* The tier the next player at this position belongs to, and what is left of
   it — "3 left in QB tier 4" is the thing you can actually act on. */
function tierState(pos, board, surv) {
  const next = board.find(p => p.p === pos);
  if (!next) return null;
  const t = tierOf(next);
  const inTier = board.filter(p => p.p === pos && tierOf(p) === t);
  const survives = surv.filter(p => p.p === pos && tierOf(p) === t).length;
  return { tier: t, next, left: inTier.length, survives, members: inTier };
}

/* ---- who is left when you next pick -------------------------------------- */

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
       QB would then be modelled as taking one anyway. */
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

/* ---- recommendations ----------------------------------------------------- */

function computeRecs() {
  const board = avail(), surv = survivors();
  const left = myPicks().filter(x => x >= S.pick).length;
  const roster = mine();
  const out = [];

  for (const pos of ['QB', 'RB', 'WR', 'TE', 'PK', 'DEF']) {
    const now = board.find(p => p.p === pos);
    if (!now) continue;
    const nxt = surv.find(p => p.p === pos);
    const need = needFor(roster, pos, left);
    const ts = tierState(pos, board, surv);

    // urgency: how far you fall by waiting. Never negative.
    const urgency = nxt ? Math.max(0, nxt.a - now.a) : 140;
    // bargain: only counts when he is genuinely late for his price. Never negative.
    const bargain = Math.max(0, S.pick - now.a);
    // a whole tier about to vanish is the sharpest signal there is
    const tierRisk = ts && ts.survives === 0 ? 14 : ts && ts.survives === 1 ? 6 : 0;

    // squared so need dominates: a filled position has to be enormously more
    // urgent to outrank an open one, rather than merely somewhat more urgent
    // buzz nudges by at most 18% — enough to separate two similar players,
    // never enough to pull a position you do not need to the top
    const bz = buzz(now);
    const score = need * need * (2 + urgency + 0.5 * bargain + tierRisk) * (1 + 0.18 * bz / 100);
    out.push({
      p: now, pos, need, urgency, bargain, nxt, score, tier: ts, buzz: bz,
      fills: fillsSlot(roster, pos),
      starter: need >= STARTER_NEED,
    });
  }

  out.sort((a, b) => b.score - a.score || a.p.a - b.p.a);

  /* Hard rule, not a weighting: while any position still fills a hole in the
     starting lineup, depth picks are not recommendations. This is what stops
     a third quarterback being suggested all afternoon. */
  const starters = out.filter(r => r.starter);
  return (starters.length ? starters : out).slice(0, 3);
}
