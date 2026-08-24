/* Smoke test against the real Sleeper API — no stubs.

   Serve dist over http first (a file:// page cannot make cross-origin
   requests at all), then:

     cd app/dist && python3 -m http.server 8777 &
     node app/test/live.mjs
*/
import { chromium } from 'playwright';

const URL = process.argv[2] || 'http://localhost:8777/draft-live.html';
const proxy = process.env.HTTPS_PROXY || process.env.https_proxy;
const b = await chromium.launch({
  executablePath: '/opt/pw-browsers/chromium',
  ...(proxy ? { proxy: { server: proxy, bypass: 'localhost,127.0.0.1' } } : {}),
  args: proxy ? ['--ignore-certificate-errors'] : [],
});
const pg = await (await b.newContext({ ignoreHTTPSErrors: true })).newPage();
const errs = [];
pg.on('pageerror', e => errs.push(String(e)));

await pg.goto(URL);
await pg.waitForFunction(() => typeof SYNC !== 'undefined' && SYNC.on, null, { timeout: 15000 })
  .catch(() => console.log('sync did not come up within 15s'));
await pg.waitForTimeout(1000);

const out = await pg.evaluate(() => ({
  origin: location.origin,
  syncOn: SYNC.on,
  draftId: SYNC.draftId,
  draftStatus: SYNC.draft && SYNC.draft.status,
  myRosterId: SYNC.myRosterId,
  owned: SYNC.owned,
  remaining: myPicks(),
  picksSeen: SYNC.picks.length,
  pill: document.getElementById('syncPill').textContent,
  teamsNamed: [...SYNC.teamName.values()].slice(0, 4),
  oppCards: document.querySelectorAll('.opp').length,
  banner: document.getElementById('syncBanner').hidden ? null : document.getElementById('syncBanner').innerText,
}));
console.log(JSON.stringify(out, null, 1));

const want = [2, 23, 26, 38, 47, 50, 71, 74, 95, 98, 122, 143, 146, 167, 170, 191];
const ok = JSON.stringify(out.owned) === JSON.stringify(want);
console.log(ok ? 'PASS  pick ownership matches the traded-pick reality'
               : `FAIL  owned=${JSON.stringify(out.owned)}`);
console.log(errs.length ? 'ERRORS ' + errs.join(' | ') : 'no JS errors');
await b.close();
process.exit(ok && out.syncOn && !errs.length ? 0 : 1);
