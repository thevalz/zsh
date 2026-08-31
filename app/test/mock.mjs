/* A Sleeper mock is not our league: different team count, different rounds,
   often a different lineup, and no draft_order naming us. The console has to
   adopt the draft's own shape rather than scoring against the real league.

   node app/test/mock.mjs
*/
import { chromium } from 'playwright';
import { fileURLToPath } from 'url';
import { dirname, resolve } from 'path';

const HERE = dirname(fileURLToPath(import.meta.url));
const APP = resolve(HERE, '..', 'dist', 'draft-live.html');

/* 10 teams, 15 rounds, single-QB, one flex, no superflex — deliberately
   unlike the 12-team superflex league the data was built for. */
const MOCK = {
  draft_id: '999000111222',
  type: 'snake',
  status: 'drafting',
  league_id: null,
  draft_order: null,
  slot_to_roster_id: null,
  settings: {
    teams: 10, rounds: 15, reversal_round: 0,
    slots_qb: 1, slots_rb: 2, slots_wr: 3, slots_te: 1,
    slots_flex: 1, slots_super_flex: 0, slots_k: 1, slots_def: 1, slots_bn: 5,
  },
};

let fails = 0;
const check = (label, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) fails++;
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${label}`);
  if (!ok) console.log(`        got  ${JSON.stringify(got)}\n        want ${JSON.stringify(want)}`);
};

const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });
const errs = [];

async function open(query) {
  const pg = await b.newPage({ viewport: { width: 1360, height: 900 } });
  pg.on('pageerror', e => errs.push(String(e)));
  await pg.addInitScript(mock => {
    const json = d => Promise.resolve({
      ok: true, status: 200,
      headers: { get: () => 'W/"m"' },
      json: () => Promise.resolve(d),
    });
    window.fetch = u => {
      u = String(u);
      if (u.includes('/picks')) return json([]);
      if (u.includes('/traded_picks')) return json([]);
      if (/\/draft\/\d+\?/.test(u)) return json(mock);
      return json([]);
    };
  }, MOCK);
  await pg.goto('file://' + APP + query);
  await pg.evaluate(() => localStorage.clear());
  await pg.reload();
  await pg.waitForTimeout(700);
  return pg;
}

console.log('\nmock without a slot');
let pg = await open(`?draft=${MOCK.draft_id}`);
let out = await pg.evaluate(() => ({
  slots: D.slots, bench: D.bench, teams: D.teams, rounds: D.rounds,
  myRosterId: SYNC.myRosterId,
  banner: document.getElementById('syncBanner').hidden ? null
    : document.getElementById('syncBanner').innerText.slice(0, 24),
  rosterRows: document.querySelectorAll('#roster .slotrow').length,
}));
check('adopts the mock lineup, not the league one',
  out.slots, ['QB', 'RB', 'RB', 'WR', 'WR', 'WR', 'TE', 'FLEX', 'K', 'DEF']);
check('adopts bench size', out.bench, 5);
check('adopts team count', out.teams, 10);
check('adopts round count', out.rounds, 15);
check('roster panel redrawn to the mock shape', out.rosterRows, 10);
check('asks which team is yours', out.banner, 'Which team is yours? Thi');
await pg.close();

console.log('\nmock with ?slot=3');
pg = await open(`?draft=${MOCK.draft_id}&slot=3`);
const want = [];
for (let r = 1; r <= 15; r++) want.push((r - 1) * 10 + (r % 2 ? 3 : 8));
out = await pg.evaluate(() => ({
  owned: SYNC.owned, mySlot: SYNC.mySlot, next: document.getElementById('sNext').textContent,
  banner: document.getElementById('syncBanner').hidden,
  live: liveRosters(), opps: document.querySelectorAll('.opp').length,
}));
check('slot honoured', out.mySlot, 3);
check('snake picks for slot 3 of 10, 15 rounds', out.owned, want);
check('first pick is 3', out.next, '3');
check('no banner once the slot is known', out.banner, true);
check('need-aware model engaged', out.live, true);
check('one card per mock team', out.opps, 10);
await pg.close();

console.log(errs.length ? `\nJS ERRORS: ${errs.join(' | ')}` : '\nno JS errors');
await b.close();
process.exit(fails || errs.length ? 1 : 0);
