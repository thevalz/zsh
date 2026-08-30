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
  const monotone = (() => {
    for (const pos of ['QB', 'RB', 'WR', 'TE']) {
      const l = P.filter(p => p.p === pos).sort((a, b) => a.a - b.a).map(tierOf);
      for (let i = 1; i < l.length; i++) if (l[i] < l[i - 1]) return false;
    }
    return true;
  })();
  return { qbs, tier: ts.tier, left: ts.left, members: ts.members.length, allTiered, monotone };
});
check('every player lands in a tier', tiers.allTiered, true);
check('tiers never go backwards as ADP rises', tiers.monotone, true);
check('tier state reports its own members', tiers.left, tiers.members);
console.log(`  info  top QBs — ${tiers.qbs.join(' ')}`);

console.log(errs.length ? `\nJS ERRORS: ${errs.join(' | ')}` : '\nno JS errors');
await b.close();
process.exit(fails || errs.length ? 1 : 0);
