/* Drive dist/draft-live.html against a stubbed Sleeper API.
   Uses the real draft + traded-pick payloads so pick ownership is exercised
   against the same data the live draft will produce.

   node app/test/sync.mjs
*/
import { chromium } from 'playwright';
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, resolve } from 'path';

const HERE = dirname(fileURLToPath(import.meta.url));
const APP = resolve(HERE, '..', 'dist', 'draft-live.html');
const draft = JSON.parse(readFileSync(resolve(HERE, 'fixture-draft.json'), 'utf8'));
const traded = JSON.parse(readFileSync(resolve(HERE, 'fixture-traded.json'), 'utf8'));

let fails = 0;
const check = (label, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) fails++;
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${label}`);
  if (!ok) console.log(`        got  ${JSON.stringify(got)}\n        want ${JSON.stringify(want)}`);
};

/* Keepers land on the pick their round dictates, exactly as Sleeper places
   them; picks 98 and 146 are ours (Skattebo R9, Dowdle R13). */
const KEEPERS = [
  { pick_no: 98, roster_id: 1, player_id: 'K-SKAT', is_keeper: true, metadata: { first_name: 'Cam', last_name: 'Skattebo' } },
  { pick_no: 146, roster_id: 1, player_id: 'K-DOWD', is_keeper: true, metadata: { first_name: 'Rico', last_name: 'Dowdle' } },
  { pick_no: 90, roster_id: 4, player_id: 'K-MAYE', is_keeper: true, metadata: { first_name: 'Drake', last_name: 'Maye' } },
];

const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });
const pg = await b.newPage({ viewport: { width: 1360, height: 1000 } });
const errs = [];
pg.on('pageerror', e => errs.push(String(e)));

/* Stub before any app code runs. `picks` is mutated from the test. */
await pg.addInitScript(({ draft, traded, keepers }) => {
  window.__picks = [...keepers];
  window.__fail = false;
  const json = d => Promise.resolve({
    ok: true, status: 200,
    headers: { get: k => (k.toLowerCase() === 'etag' ? 'W/"' + JSON.stringify(d).length + '"' : null) },
    json: () => Promise.resolve(d),
  });
  window.fetch = url => {
    if (window.__fail) return Promise.reject(new Error('network down'));
    const u = String(url);
    if (u.includes('/picks')) return json(window.__picks);
    if (u.includes('/traded_picks')) return json(traded);
    if (/\/draft\/\d+\?/.test(u)) return json(draft);
    if (u.includes('/drafts')) return json([draft]);
    if (u.includes('/users')) return json([]);
    if (u.includes('/rosters')) return json([]);
    return json([]);
  };
}, { draft, traded, keepers: KEEPERS });

await pg.goto('file://' + APP);
await pg.evaluate(() => localStorage.clear());
await pg.reload();
await pg.waitForFunction(() => typeof SYNC !== 'undefined' && SYNC.on, null, { timeout: 5000 })
  .catch(() => console.log('  (sync never came up)'));
await pg.waitForTimeout(300);

const read = () => pg.evaluate(() => ({
  pill: document.getElementById('syncPill').textContent,
  pick: +document.getElementById('sPick').textContent,
  next: document.getElementById('sNext').textContent,
  left: +document.getElementById('sLeft').textContent,
  owned: SYNC.owned,
  remaining: myPicks(),
  myRoster: SYNC.myRosterId,
  rosterNames: [...document.querySelectorAll('#roster .slotrow')]
    .map(r => r.innerText.replace(/\s+/g, ' ')).filter(t => !t.includes('open')),
  live: typeof liveRosters === 'function' && liveRosters(),
  rec1: document.querySelector('.rec .rec-name')?.innerText.replace(/\s+/g, ' '),
}));

console.log('\nsync boot');
let s = await read();
check('my roster id derived', s.myRoster, 1);
check('pick ownership incl. traded 38, excl. traded 119',
  s.owned, [2, 23, 26, 38, 47, 50, 71, 74, 95, 98, 122, 143, 146, 167, 170, 191]);
check('keepers removed from remaining picks (98, 146 gone)',
  s.remaining, [2, 23, 26, 38, 47, 50, 71, 74, 95, 122, 143, 167, 170, 191]);
check('live pick is first unused slot', s.pick, 1);
check('next pick is 2', s.next, '2');
check('keepers landed on my roster', s.rosterNames.length, 2);
check('live rosters available to the model', s.live, true);
console.log(`  info  pill = "${s.pill}"`);

console.log('\npicks arrive');
await pg.evaluate(() => {
  // fill 1..40, skipping slots already used by keepers
  const used = new Set(window.__picks.map(p => p.pick_no));
  const pool = ['4046', '6786', '4034', '5849', '8112', '11560', '4881', '6794'];
  for (let n = 1; n <= 40; n++) {
    if (used.has(n)) continue;
    window.__picks.push({ pick_no: n, roster_id: ((n - 1) % 12) + 1, player_id: pool[n % pool.length], metadata: {} });
  }
  return pollOnce();
});
await pg.waitForTimeout(200);
s = await read();
check('counter advanced past the filled block', s.pick, 41);
check('next owned pick after 40 is 47', s.next, '47');
// mine at or before 40: 2, 23, 26, 38 -> 14 - 4 = 10 still to come
check('picks left drops to 10', s.left, 10);

console.log('\ntransient blip');
/* A few misses in quick succession is a blip, not an outage. The board on
   screen is still the last good one, so the app should stay quiet. */
await pg.evaluate(async () => { window.__fail = true; for (let i = 0; i < 3; i++) await pollOnce(); });
await pg.waitForTimeout(150);
const blip = await pg.evaluate(() => ({
  banner: !document.getElementById('syncBanner').hidden,
  pill: document.getElementById('syncPill').textContent,
  boardIntact: Object.keys(S.drafted).length > 0,
}));
check('no alarm on a short blip', blip.banner, false);
check('board still shows the last good state', blip.boardIntact, true);
console.log(`  info  pill = "${blip.pill}"`);

console.log('\nsustained outage');
await pg.evaluate(async () => {
  SYNC.lastAt = Date.now() - 60000;          // failing for a solid minute
  for (let i = 0; i < 5; i++) await pollOnce();
});
await pg.waitForTimeout(150);
const deg = await pg.evaluate(() => ({
  pill: document.getElementById('syncPill').textContent,
  banner: !document.getElementById('syncBanner').hidden,
  manualWorks: (() => { const before = S.pick; mark('Bo Nix', 'them'); return S.pick !== before; })(),
}));
check('pill shows retrying', deg.pill, 'retrying');
check('banner shown', deg.banner, true);
check('manual marking re-enabled while degraded', deg.manualWorks, true);

console.log('\nrecovery');
await pg.evaluate(async () => { window.__fail = false; await pollOnce(); });
await pg.waitForTimeout(200);
const rec = await pg.evaluate(() => ({
  pill: document.getElementById('syncPill').textContent,
  banner: !document.getElementById('syncBanner').hidden,
  blocked: (() => { const before = S.pick; mark('Baker Mayfield', 'them'); return S.pick === before; })(),
}));
check('banner cleared', rec.banner, false);
check('hand marks ignored again once authoritative', rec.blocked, true);
console.log(`  info  pill = "${rec.pill}"`);

/* The point of syncing rosters is that scarcity should depend on what the
   teams ahead of you actually need, not on ADP order alone. Give every
   intervening team a full complement of quarterbacks and the best remaining QB
   ought to survive a run that would otherwise swallow him. */
console.log('\nneed-aware simulation');
const sim = await pg.evaluate(() => {
  S.drafted = {}; S.hist = [];
  SYNC.made = new Set(); SYNCED_MADE = SYNC.made;
  S.pick = 1;
  SYNC.owned = [1, 14]; SYNCED_PICKS = SYNC.owned;   // 12 picks between mine
  SYNC.myRosterId = 1;

  const topQB = avail().find(p => p.p === 'QB').n;
  const qbs = avail().filter(p => p.p === 'QB').slice(1, 4);
  const survivesQB = () => {
    const s = survivors();
    return s.some(p => p.n === topQB);
  };

  // every team ahead of me already has two quarterbacks -> no QB need
  SYNC.rosters = new Map();
  for (let rid = 1; rid <= 12; rid++) SYNC.rosters.set(rid, qbs.slice());
  const withQBsFilled = survivesQB();

  // now nobody has a quarterback -> QB need is maximal
  SYNC.rosters = new Map();
  for (let rid = 1; rid <= 12; rid++) SYNC.rosters.set(rid, []);
  const withQBsEmpty = survivesQB();

  const adpOrder = avail().slice(13).some(p => p.n === topQB);
  return { topQB, withQBsFilled, withQBsEmpty, adpOrder };
});
check('top QB survives when the teams ahead are set at QB', sim.withQBsFilled, true);
check('top QB is taken when they all still need one', sim.withQBsEmpty, false);
check('pure ADP order would have taken him regardless', sim.adpOrder, false);
console.log(`  info  simulated on ${sim.topQB}`);

console.log('\naround the room');
await pg.evaluate(() => pollOnce());
await pg.waitForTimeout(200);
const opp = await pg.evaluate(() => ({
  shown: !document.getElementById('oppCard').hidden,
  cards: document.querySelectorAll('.opp').length,
  mine: document.querySelectorAll('.opp.me').length,
  first: document.querySelector('.opp')?.innerText.replace(/\s+/g, ' '),
  ordered: [...document.querySelectorAll('.opp-when')].map(e => +(e.textContent.match(/\d+/) || [0])[0]),
}));
check('panel visible when synced', opp.shown, true);
check('one card per team', opp.cards, 12);
check('my team flagged exactly once', opp.mine, 1);
check('sorted by who picks next', opp.ordered, [...opp.ordered].sort((a, b) => a - b));
console.log(`  info  ${opp.first}`);

console.log(errs.length ? `\nJS ERRORS: ${errs.join(' | ')}` : '\nno JS errors');
await b.close();
process.exit(fails || errs.length ? 1 : 0);
