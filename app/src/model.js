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
   A tier is a run of players who produce about the same, so which one you get
   does not much matter — what matters is how many are left before the drop.

   These are tiers of POINT PRODUCTION, not of draft price. An earlier version
   broke on ADP gaps, which describes when a player will be taken rather than
   what he scores; two players a round apart in cost but identical in output
   are the same decision, and that is exactly the gap worth exploiting.

   Two details make the breaks meaningful:

   1. Only players above replacement are tiered. Everyone below is one
      undifferentiated bucket, because they are genuinely interchangeable.
      Tiering the whole pool put every break in the tail, where the
      interpolated values fall off a cliff, and left the draftable top as one
      useless 69-man tier.
   2. Breaks go at the largest drops (natural breaks), with the tier count
      scaled to how many players are actually in play. Thresholding on a fixed
      gap gave 14 tiers at QB and 4 at RB. */
const MIN_TIER = 4;      // a tier smaller than this is not a choice, it is a player
const TIERS = (() => {
  const out = new Map();
  const zs = arr => {
    const m = arr.reduce((a, b) => a + b, 0) / (arr.length || 1);
    const sd = Math.sqrt(arr.reduce((a, b) => a + (b - m) ** 2, 0) / (arr.length || 1)) || 1;
    return arr.map(x => (x - m) / sd);
  };
  for (const pos of ['QB', 'RB', 'WR', 'TE', 'PK', 'DEF']) {
    // ORDER BY ADP, so a tier is a band of the draft and tier numbers rise as
    // the rounds do. Production decides where the cuts fall, not the order.
    const all = P.filter(p => p.p === pos).sort((a, b) => a.a - b.a);
    /* How deep the position runs: as many players as clear replacement, but
       taken in ADP order so tier numbers stay monotone in the draft. Selecting
       the above-replacement set itself would break that — the two orderings
       disagree on a player or two, and a late name with a good projection
       would then carry a lower tier number than someone drafted before him.
       Round alignment is the point here, so it wins; the disagreement is still
       visible in the VOR column and in the need+value sort.

       Kickers sit entirely below replacement, so never take fewer than a
       round's worth or the position collapses into one tier. */
    const above = all.filter(p => (p.vor ?? -1) >= 0).length;
    const live = Math.min(all.length, Math.max(above, D.teams));
    const head = all.slice(0, live);
    const rest = all.slice(live);
    const k = Math.max(3, Math.min(6, Math.round(live / 7)));

    // a cut is worth making where production falls off, with a gap in draft
    // cost as a weaker second vote
    const drop = zs(head.slice(1).map((p, i) => Math.max(0, (head[i].vor ?? 0) - (p.vor ?? 0))));
    const cost = zs(head.slice(1).map((p, i) => Math.max(0, p.a - head[i].a)));
    const ranked = drop.map((d, i) => ({ s: d + 0.5 * cost[i], i: i + 1 }))
      .sort((a, b) => b.s - a.s);

    /* Take the best cuts that leave every tier at least MIN_TIER deep. Without
       this the top of a position fragments into singletons — the gap from the
       best back to the second is genuinely large, but "tier of one" tells you
       nothing you could act on. */
    const cuts = [];
    for (const { i } of ranked) {
      if (cuts.length >= k - 1) break;
      const pts = [0, ...cuts, i, live].sort((a, b) => a - b);
      if (pts.every((v, j) => j === 0 || v - pts[j - 1] >= MIN_TIER)) cuts.push(i);
    }
    cuts.sort((a, b) => a - b);

    let tier = 1, c = 0;
    head.forEach((p, i) => {
      if (c < cuts.length && i === cuts[c]) { tier++; c++; }
      out.set(p.n, tier);
    });
    // the tail past the startable range: one bucket, effectively interchangeable
    for (const p of rest) out.set(p.n, tier + 1);
  }
  return out;
})();
const roundOf = p => Math.max(1, Math.ceil(p.a / D.teams));
const tierOf = p => TIERS.get(p.n) || 0;

/* The shape of a position: every tier, who is left in it, and the drop in
   points to the tier below. This is the heads-up view — it answers "can I
   wait?" without reading a single player row. */
function tierShape(pos, board, surv) {
  const groups = new Map();
  for (const p of P.filter(x => x.p === pos)) {
    const t = tierOf(p);
    if (!groups.has(t)) groups.set(t, []);
    groups.get(t).push(p);
  }
  const survSet = new Set(surv || []);
  const rows = [...groups.entries()].sort((a, b) => a[0] - b[0]).map(([t, members]) => {
    const live = members.filter(p => !S.drafted[p.n]);
    const best = live.length ? Math.max(...live.map(p => p.vor ?? 0)) : null;
    const rs = members.map(roundOf);
    return {
      tier: t, members, live,
      r0: Math.min(...rs), r1: Math.max(...rs),
      left: live.length,
      survives: live.filter(p => survSet.has(p)).length,
      top: best,
      hi: Math.max(...members.map(p => p.vor ?? 0)),
      lo: Math.min(...members.map(p => p.vor ?? 0)),
      next: live.length ? live.reduce((a, b) => ((a.vor ?? 0) >= (b.vor ?? 0) ? a : b)) : null,
    };
  });
  // drop = what you give up by letting this tier empty out
  rows.forEach((r, i) => {
    const below = rows.slice(i + 1).find(x => x.left > 0);
    r.drop = r.top != null && below && below.top != null ? Math.round(r.top - below.top) : null;
  });
  return rows.filter(r => r.left > 0 || r.members.length);
}

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

/* ---- bye collisions -------------------------------------------------------
   The objective is points in each individual week, not points in total. Two
   rosters with the same season projection are not equally good if one of them
   starts four players who are all off in week 10 — that week is a loss no
   matter how good the roster reads on paper.

   Only players filling starting slots are counted. A backup sharing a bye with
   nobody costs you nothing, and a backup sharing a bye with your starter is
   the specific thing you were supposed to avoid. */
function byeLoad(roster) {
  const { slots } = fill(roster);
  const load = new Map();
  for (const s of slots) {
    if (!s.p || !s.p.b) continue;
    if (!['QB', 'RB', 'WR', 'TE', 'FLEX', 'SUPER_FLEX'].includes(s.s)) continue;
    load.set(s.p.b, (load.get(s.p.b) || 0) + 1);
  }
  return load;
}

/* How many starters you would have on this player's bye if you took him.
   Only counts him if he would actually start; a bench body is not a collision. */
function byeAfter(p, roster) {
  if (!p.b) return 0;
  const load = byeLoad(roster);
  const starts = fillsSlot(roster, p.p) !== null;
  return (load.get(p.b) || 0) + (starts ? 1 : 0);
}

/* A multiplier, never a veto. Losing one starter for a week is normal and
   every roster does it; three is a bad week; four is a forfeit. Capped so a
   genuinely better player still wins — this breaks ties, it does not draft. */
const BYE_OK = 2;
function byeRisk(p, roster) {
  const n = byeAfter(p, roster);
  if (n <= BYE_OK) return 1;
  return Math.max(0.7, 1 - 0.09 * (n - BYE_OK));
}

/* ---- fit: need x value ---------------------------------------------------
   The board used to be sorted by ADP alone, which answers "who is best" and
   not "who is best *for me*". Value here is points above replacement under
   this league's own scoring, so a quarterback is measured against the 24 who
   start in superflex rather than against the 12 who start elsewhere. Need
   squares, as in the recommendations, so a position you have filled has to be
   far better to outrank one you have not. */
const VOR_MAX = Math.max(1, ...P.map(p => p.vor || 0));
function fitScore(p, roster, left, surv) {
  const need = needFor(roster, p.p, left);
  const value = Math.max(0, p.vor || 0) / VOR_MAX;      // 0..1
  const gone = surv && !surv.includes(p) ? 1.25 : 1;     // won't last to your next pick
  const bz = 1 + 0.12 * buzz(p) / 100;
  return need * need * value * gone * bz * byeRisk(p, roster);
}

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
    /* Among the first few at this position, prefer one who does not stack a
       bye you are already heavy on. The penalty is bounded, so this only ever
       reorders players of near-equal value — it cannot promote a worse one. */
    const top = board.filter(p => p.p === pos).slice(0, 3);
    if (!top.length) continue;
    const now = top.reduce((a, b) =>
      (Math.max(a.vor ?? 0, 1) * byeRisk(a, roster) >= Math.max(b.vor ?? 0, 1) * byeRisk(b, roster) ? a : b));
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
    const bye = byeAfter(now, roster);
    const score = need * need * (2 + urgency + 0.5 * bargain + tierRisk)
      * (1 + 0.18 * bz / 100) * byeRisk(now, roster);
    out.push({
      p: now, pos, need, urgency, bargain, nxt, score, tier: ts, buzz: bz,
      bye, byeClash: bye > BYE_OK,
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
