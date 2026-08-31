/* Regression tests for the recommendation model.

   The bug these exist for: the score used to be
   `need x (0.6*delta + 0.4*(pick - adp))`. Mid-draft that bracket turns
   negative, and multiplying a negative by a *larger* need makes it *smaller* —
   so positions you had already filled outranked positions you still needed,
   and it recommended a third quarterback all afternoon.

   node app/test/model.mjs
*/
import { chromium } from 'playwright';
import { fileURLToPath } from 'url';
import { dirname, resolve } from 'path';

const HERE = dirname(fileURLToPath(import.meta.url));
const APP = resolve(HERE, '..', 'dist', 'draft-live.html');

let fails = 0;
const check = (label, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) fails++;
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${label}`);
  if (!ok) console.log(`        got  ${JSON.stringify(got)}\n        want ${JSON.stringify(want)}`);
};

const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });
const pg = await b.newPage();
const errs = [];
pg.on('pageerror', e => errs.push(String(e)));
await pg.goto('file://' + APP);
await pg.evaluate(() => localStorage.clear());
await pg.reload();
await pg.waitForTimeout(400);

console.log('\ntwo quarterbacks already rostered');
const qb = await pg.evaluate(() => {
  S.drafted = {}; S.hist = [];
  avail().filter(p => p.p === 'QB').slice(0, 2).forEach(p => { S.drafted[p.n] = 'me'; });
  S.pick = 60;
  const { slots } = fill(mine());
  const recs = computeRecs();
  return {
    qbSlotFilled: !slots.some(s => s.s === 'QB' && !s.p),
    sfSlotFilled: !slots.some(s => s.s === 'SUPER_FLEX' && !s.p),
    needQB: +needWeight('QB').toFixed(2),
    qbIsDepth: needWeight('QB') < 0.5,
    recPositions: recs.map(r => r.pos),
    allFillStarters: recs.every(r => r.fills),
  };
});
check('QB and SUPER_FLEX are both seated', [qb.qbSlotFilled, qb.sfSlotFilled], [true, true]);
check('a third QB reads as depth, not need', qb.qbIsDepth, true);
check('no QB recommended while starter holes remain', qb.recPositions.includes('QB'), false);
check('every recommendation names the slot it fills', qb.allFillStarters, true);
console.log(`  info  need(QB) = ${qb.needQB}, recs = ${qb.recPositions.join(', ')}`);

console.log('\nscores stay non-negative all draft');
const neg = await pg.evaluate(() => {
  const bad = [];
  S.drafted = {}; S.hist = [];
  const mySet = new Set(D.myPicks);
  for (let n = 1; n <= 192; n++) {
    S.pick = n;
    const a = avail();
    if (!a.length) break;
    if (mySet.has(n)) {
      const r = computeRecs();
      for (const x of r) {
        if (!(x.score >= 0)) bad.push(`pick ${n}: ${x.pos} ${x.p.n} score=${x.score}`);
        if (x.urgency < 0 || x.bargain < 0) bad.push(`pick ${n}: ${x.pos} negative term`);
      }
      if (r[0]) S.drafted[r[0].p.n] = 'me';
    } else {
      S.drafted[a[0].n] = 'them';
    }
  }
  const counts = {};
  mine().forEach(p => { counts[p.p] = (counts[p.p] || 0) + 1; });
  return { bad, counts, startable: { QB: startableSlots('QB'), K: startableSlots('PK') } };
});
check('no negative scores across a whole draft', neg.bad, []);
// QB and SUPER_FLEX are the only slots a QB can start in, so three is waste
check('never drafts more QBs than can start', neg.counts.QB <= neg.startable.QB, true);
check('exactly one kicker', neg.counts.PK, 1);
check('exactly one defense', neg.counts.DEF, 1);
console.log(`  info  final roster ${JSON.stringify(neg.counts)}`);

console.log('\ntiers');
const tiers = await pg.evaluate(() => {
  S.drafted = {}; S.hist = []; S.pick = 1;
  const qbs = avail().filter(p => p.p === 'QB').slice(0, 12).map(p => `${p.n.split(' ').pop()}:t${tierOf(p)}`);
  const ts = tierState('QB', avail(), survivors());
  const allTiered = P.every(p => tierOf(p) >= 1);
  /* Tiers run in DRAFT order: a tier is a band of rounds, so tier numbers must
     rise with ADP and never fall. Production decides where the breaks go, not
     the ordering — that trade is deliberate, because a tier you cannot map to
     a round is not something you can act on while you are on the clock. */
  const monotone = (() => {
    for (const pos of ['QB', 'RB', 'WR', 'TE']) {
      const l = P.filter(p => p.p === pos).sort((a, b) => a.a - b.a).map(tierOf);
      for (let i = 1; i < l.length; i++) if (l[i] < l[i - 1]) return false;
    }
    return true;
  })();
  // no tier may be so small it is a player rather than a choice
  const sizes = {};
  for (const pos of ['QB', 'RB', 'WR', 'TE']) {
    const byTier = {};
    for (const p of P.filter(x => x.p === pos)) byTier[tierOf(p)] = (byTier[tierOf(p)] || 0) + 1;
    const maxT = Math.max(...Object.keys(byTier).map(Number));
    // the replacement bucket is exempt — it is a remainder, not a tier
    sizes[pos] = Object.entries(byTier).filter(([t]) => +t < maxT).map(([, n]) => n);
  }
  const smallest = Math.min(...Object.values(sizes).flat());
  /* The bottom bucket is the tail past the startable range, taken in ADP order
     so tier numbers stay monotone. It is not exactly the below-replacement set
     — the two orderings disagree on a player or two — but it should be
     overwhelmingly made of them, or the cut is in the wrong place. */
  const replClean = ['QB', 'RB', 'WR', 'TE'].every(pos => {
    const l = P.filter(p => p.p === pos);
    const maxT = Math.max(...l.map(tierOf));
    const tail = l.filter(p => tierOf(p) === maxT);
    // an absolute bound, not a ratio — tails differ in size and a ratio lets a
    // small one (TE) fail on the same two players a large one absorbs
    return tail.filter(p => (p.vor ?? -1) >= 0).length <= 3;
  });
  // every tier's round band must start no earlier than the one before it
  const roundsRise = ['QB', 'RB', 'WR', 'TE'].every(pos => {
    const rows = tierShape(pos, avail(), []);
    for (let i = 1; i < rows.length; i++) if (rows[i].r0 < rows[i - 1].r0) return false;
    return true;
  });
  return { qbs, tier: ts.tier, left: ts.left, members: ts.members.length, allTiered,
           monotone, sizes, smallest, replClean, roundsRise };
});
check('every player lands in a tier', tiers.allTiered, true);
check('tier numbers rise with the draft, never fall', tiers.monotone, true);
check('no tier is smaller than a real choice', tiers.smallest >= 4, true);
check('the bottom bucket is almost entirely below replacement', tiers.replClean, true);
check('each tier starts no earlier in the draft than the one before', tiers.roundsRise, true);
check('tier state reports its own members', tiers.left, tiers.members);
console.log(`  info  top QBs — ${tiers.qbs.join(' ')}`);
console.log(`  info  tier sizes — ${Object.entries(tiers.sizes).map(([k, v]) => `${k} ${v.join('/')}`).join(', ')}`);

console.log('\nvalue over replacement');
const vor = await pg.evaluate(() => {
  const has = P.filter(p => p.vor != null);
  const real = has.filter(p => !p.vest);
  const byVor = [...has].sort((a, b) => b.vor - a.vor);
  const byAdp = [...P].sort((a, b) => a.a - b.a);
  const rank = n => byVor.findIndex(p => p.n === n) + 1;
  const topQb = byAdp.find(p => p.p === 'QB');
  return {
    coverage: has.length, projected: real.length, pool: P.length,
    // superflex starts 24 QBs, so replacement level at QB is high and the
    // best quarterback is worth far less over it than his ADP suggests
    topQbAdpRank: byAdp.findIndex(p => p.n === topQb.n) + 1,
    topQbVorRank: rank(topQb.n),
    allFinite: has.every(p => Number.isFinite(p.vor)),
  };
});
check('most of the pool carries a value', vor.coverage > vor.pool * 0.75, true);
check('every value is a real number', vor.allFinite, true);
check('the top QB is not the top value in superflex', vor.topQbVorRank > vor.topQbAdpRank, true);
console.log(`  info  ${vor.projected} projected + ${vor.coverage - vor.projected} interpolated; `
  + `top QB ADP #${vor.topQbAdpRank} -> VOR #${vor.topQbVorRank}`);

console.log('\nboard ranks by need as well as value');
const fitrank = await pg.evaluate(() => {
  const snap = () => {
    const roster = mine(), left = myPicks().filter(x => x >= S.pick).length;
    const surv = survivors();
    return avail().map(p => ({ p, f: fitScore(p, roster, left, surv) }))
      .sort((a, b) => b.f - a.f).slice(0, 6).map(x => x.p.p);
  };
  S.drafted = {}; S.hist = []; S.pick = 20;
  const empty = snap();

  // seat two QBs and both RBs, leaving WR/TE/FLEX open
  const take = (pos, k) => avail().filter(p => p.p === pos).slice(0, k)
    .forEach(p => { S.drafted[p.n] = 'me'; });
  take('QB', 2); take('RB', 2);
  const filled = snap();
  const { slots } = fill(mine());
  return {
    empty, filled,
    open: slots.filter(s => !s.p).map(s => s.s),
    everyScorePositive: avail().every(p =>
      fitScore(p, mine(), 10, null) >= 0),
  };
});
check('fit never goes negative', fitrank.everyScorePositive, true);
check('a filled position drops off the top of the board',
  fitrank.filled.filter(x => x === 'QB' || x === 'RB').length < fitrank.empty.filter(x => x === 'QB' || x === 'RB').length, true);
check('the board leads with what is still open',
  fitrank.filled.slice(0, 3).every(x => fitrank.open.includes(x) || ['RB', 'WR', 'TE'].includes(x)), true);
console.log(`  info  empty -> ${fitrank.empty.join(' ')}\n        filled -> ${fitrank.filled.join(' ')} (open: ${fitrank.open.join(', ')})`);

console.log('\ntier column on the board');
const tcol = await pg.evaluate(() => {
  S.drafted = {}; S.hist = []; S.pick = 1; S.filter = 'RB'; S.sort = 'adp'; S.hide = false;
  render();
  // find the column by its header — hardcoding an index let an added column
  // break this test silently once already
  const hdrs = [...document.querySelectorAll('th')].map(t => t.textContent.trim());
  const ti = hdrs.indexOf('Tier');
  const read = () => [...document.querySelectorAll('#tb tr')].slice(0, 8).map(r => ({
    name: r.cells[0].textContent.trim(),
    tier: r.cells[ti].querySelector('.tno')?.textContent,
    left: r.cells[ti].querySelector('.tleft')?.textContent.trim(),
    last: r.classList.contains('tlast'),
  }));
  const before = read();
  // the top RB's tier-mates, minus one
  const t1 = P.filter(p => p.p === 'RB' && tierOf(p) === 1);
  S.drafted[t1[0].n] = 'them';
  render();
  const after = read();
  const hdr = [...document.querySelectorAll('th')].map(t => t.textContent.trim());
  return {
    hdr, before, after,
    countBefore: before.find(r => r.tier === '1' && r.left)?.left,
    countAfter: after.find(r => r.tier === '1' && r.left)?.left,
    poolT1: t1.length,
    everyRowHasTier: [...document.querySelectorAll('#tb tr')].every(r => r.cells[ti].querySelector('.tno')),
  };
});
check('the board has a Tier column', tcol.hdr.includes('Tier'), true);
check('every row shows a tier', tcol.everyRowHasTier, true);
check('the count matches the pool', tcol.countBefore, `· ${tcol.poolT1} left`);
check('drafting a player decrements his tier count', tcol.countAfter, `· ${tcol.poolT1 - 1} left`);
console.log(`  info  ${tcol.before.slice(0, 3).map(r => `${r.name} t${r.tier} ${r.left}`).join(' | ')}`);

console.log('\ntier board panel');
const shape = await pg.evaluate(() => {
  S.drafted = {}; S.hist = []; S.pick = 1; S.filter = 'ALL'; S.hide = true;
  render();
  const cols = [...document.querySelectorAll('.shapecol')];
  const qb = tierShape('QB', avail(), survivors());
  const totals = {};
  for (const pos of ['QB', 'RB', 'WR', 'TE']) {
    const rows = tierShape(pos, avail(), survivors());
    totals[pos] = rows.reduce((a, r) => a + r.members.length, 0);
  }
  const poolPer = {};
  for (const pos of ['QB', 'RB', 'WR', 'TE']) poolPer[pos] = P.filter(p => p.p === pos).length;
  return {
    cols: cols.length,
    rowsPerCol: cols.map(c => c.querySelectorAll('.trow').length),
    hasRepl: cols.every(c => [...c.querySelectorAll('.tlab')].some(l => l.textContent === 'deep')),
    // every tier row must name the round band it goes in
    allHaveRounds: cols.every(c => [...c.querySelectorAll('.trow')].slice(0, -1)
      .every(r => /^R\d+(-\d+)?$/.test(r.querySelector('.trnd')?.textContent || ''))),
    totals, poolPer,
    // survivors can never exceed what is left in the tier
    survSane: qb.every(r => r.survives <= r.left),
    dropsNonNeg: qb.every(r => r.drop == null || r.drop >= 0),
  };
});
check('a column per position', shape.cols, 4);
check('every tier is accounted for — no player lost', shape.totals, shape.poolPer);
check('each position ends in a deep bucket', shape.hasRepl, true);
check('every tier names its round band', shape.allHaveRounds, true);
check('survivors never exceed what is left', shape.survSane, true);
check('drops are never negative', shape.dropsNonNeg, true);
console.log(`  info  tiers per position — ${shape.rowsPerCol.join(', ')}`);

console.log('\nbye collisions');
const bye = await pg.evaluate(() => {
  S.drafted = {}; S.hist = []; S.pick = 40; S.filter = 'ALL';
  // find the bye week with the most RB/WR available, then stack four starters on it
  const counts = {};
  for (const p of avail()) if (['RB', 'WR'].includes(p.p) && p.b) counts[p.b] = (counts[p.b] || 0) + 1;
  const wk = +Object.entries(counts).sort((a, b) => b[1] - a[1])[0][0];
  const stack = avail().filter(p => p.b === wk && ['RB', 'WR'].includes(p.p)).slice(0, 4);
  stack.forEach(p => { S.drafted[p.n] = 'me'; });
  render();

  const clash = avail().find(p => p.b === wk && p.p === 'WR');
  const clean = avail().find(p => p.b !== wk && p.p === 'WR');
  // a bench body on the same bye must not register as a collision
  const benchOnly = (() => {
    const before = byeAfter(clash, mine());
    return before;
  })();
  return {
    week: wk,
    load: [...byeLoad(mine()).entries()],
    riskClash: +byeRisk(clash, mine()).toFixed(3),
    riskClean: +byeRisk(clean, mine()).toFixed(3),
    benchOnly,
    floor: +byeRisk({ b: wk, p: 'WR', vor: 1 }, mine()).toFixed(3),
    flagged: document.querySelectorAll('.byecell.bad, .byecell.warn').length,
    noteWarns: document.getElementById('byeNote').textContent.includes('costs you'),
    // the penalty is bounded: it must never invert a large value gap
    boundedFit: (() => {
      const good = { ...clean, vor: 200, b: wk }; // huge value but a clash
      const meh = { ...clean, vor: 40 };
      return fitScore(good, mine(), 10, null) > fitScore(meh, mine(), 10, null);
    })(),
  };
});
check('a stacked week is counted', bye.load.some(([w, n]) => w === bye.week && n === 4), true);
check('a clashing player is penalised', bye.riskClash < 1, true);
check('a clean bye is not penalised', bye.riskClean, 1);
check('the penalty is floored, never a veto', bye.floor >= 0.7, true);
check('the strip flags the bad week', bye.flagged > 0, true);
check('the note names the cost', bye.noteWarns, true);
check('a big value gap still beats a bye clash', bye.boundedFit, true);
console.log(`  info  week ${bye.week}: ${bye.load.map(([w, n]) => `w${w}=${n}`).join(' ')}; `
  + `risk clash ${bye.riskClash} vs clean ${bye.riskClean}`);

console.log('\nrole in the offence');
const role = await pg.evaluate(() => {
  S.drafted = {}; S.hist = []; S.pick = 1; S.filter = 'ALL'; S.hide = true; S.sort = 'adp';
  render();
  const rbs = P.filter(p => p.p === 'RB' && p.role);
  const wrs = P.filter(p => p.p === 'WR' && p.role);
  const band = (lo, hi) => rbs.filter(p => p.a >= lo && p.a < hi);
  const roleset = [...new Set(rbs.map(p => p.role))].sort();
  // one WR1 per team at most — the label is an ordinal, not a compliment
  const perTeam = {};
  for (const p of wrs) if (p.role === 'WR1') perTeam[p.t] = (perTeam[p.t] || 0) + 1;
  return {
    tagged: P.filter(p => p.role).length,
    leaned: P.filter(p => p.leanTag).length,
    roleset,
    sharesInRange: [...rbs, ...wrs].every(p => p.shr > 0 && p.shr <= 1),
    // early backs should be workhorses, late ones should not
    earlyBell: band(0, 50).filter(p => p.role === 'bell cow').length,
    earlyTotal: band(0, 50).length,
    lateBench: band(100, 200).filter(p => ['backup', 'committee'].includes(p.role)).length,
    lateTotal: band(100, 200).length,
    dupWR1: Object.values(perTeam).filter(n => n > 1).length,
    leanTags: [...new Set(P.filter(p => p.leanTag).map(p => p.leanTag))].sort(),
    chipsRendered: document.querySelectorAll('#tb .rolechip').length,
    leanRendered: document.querySelectorAll('#tb .lean').length,
    // a committee back going early is exactly what this is for
    earlyCommittee: band(0, 60).filter(p => p.role === 'committee').map(p => `${p.n} ${p.a.toFixed(0)}`),
  };
});
check('roles use the expected vocabulary', role.roleset, ['backup', 'bell cow', 'committee', 'lead']);
check('every share is a real fraction', role.sharesInRange, true);
check('early backs are mostly workhorses', role.earlyBell / role.earlyTotal > 0.7, true);
check('late backs are mostly not', role.lateBench / role.lateTotal > 0.7, true);
check('no team has two WR1s', role.dupWR1, 0);
check('lean is three-valued', role.leanTags, ['even', 'pass', 'run']);
check('the board renders role chips', role.chipsRendered > 0, true);
console.log(`  info  ${role.tagged} roles, ${role.leaned} leans; `
  + `early bell cows ${role.earlyBell}/${role.earlyTotal}, late depth ${role.lateBench}/${role.lateTotal}`);
console.log(`  info  committee backs inside ADP 60 — ${role.earlyCommittee.join(', ') || 'none'}`);

console.log(errs.length ? `\nJS ERRORS: ${errs.join(' | ')}` : '\nno JS errors');
await b.close();
process.exit(fails || errs.length ? 1 : 0);
