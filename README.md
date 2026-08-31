# Zebras Shooting Heroin — 2026 Draft Assets

Working materials for the 2026 keeper decision and live draft, for
**Made America 2024 / supervillain** (Sleeper draft slot 2).

## League

12 teams · **superflex** (`QB RB RB WR WR TE FLEX FLEX SUPER_FLEX K DEF` + 5 bench) ·
full PPR, 4-point passing TDs · 16-round snake · **max 2 keepers**.

**Keeper rule:** keep a player two rounds earlier than he was drafted. Waiver
adds cost round 11. Rounds 1–2 picks cannot be kept — there is no room to move
up two. Cost follows the player through a trade.

**Our picks:** 2, 23, 26, **38**, 47, 50, 71, 74, 95, 122, 143, 167, 170, 191.
Pick 38 came in from Jersey Rum Hams for our 10.11; rounds 9 and 13 are
forfeited to the two keepers.

**Keepers: Cam Skattebo (R9, pick 98) and Rico Dowdle (R13, pick 146).**

## Layout

```
app/build.py               refresh ADP + projections + player-id map, emit
                           app/dist/draft-live.html and docs/
app/template.html          page shell and CSS, with __DATA__ / __SCRIPT__ slots
app/src/state.js           data, persistence, whose-pick arithmetic
app/src/model.js           roster fill, positional need, need-aware simulation
app/src/sleeper.js         Sleeper client: discovery, polling, backoff
app/src/ui.js              rendering and events
app/test/sync.mjs          sync behaviour against a stubbed API (real payloads)
app/test/model.mjs         recommendation regressions — the scoring sign bug
app/test/mock.mjs          adapting to a mock with different settings
app/test/live.mjs          smoke test against the real API — needs an http origin
data/league_config.json    league settings, our picks, keeper rule, modelled keepers
data/players_sf_adp.json   283-player superflex pool: FFC 2QB ADP, FFToday
                           projections scored under our rules, and VOR
data/player_ids.json       Sleeper player_id -> our pool, so the app never
                           downloads Sleeper's 14 MB player file at runtime
data/projections.json      cached FFToday stat lines, so --no-fetch is really
                           offline and a partial fetch never drops a position
data/player_meta.json      rookie status (years_exp) per player
data/adp_history.json      archived daily ADP snapshots; the source of velocity
data/rosters_2025.json     all 12 final 2025 rosters with original draft positions
data/standings_2025.json   final rank, wins, points for, waiver moves
analysis/rosters.py        rosters as a Python literal, for quick scripting
analysis/keeper_math.py    the surplus model — run it to reproduce the keeper table
tools/draft-console.html   the earlier manual-only console (published as an Artifact)
draft_board.csv            2025 draft board, reconstructed (see caveat below)
last-years-draft.md        the same board in prose
```

## Running things

```sh
python3 analysis/keeper_math.py     # per-team keeper surplus, top 2 each
python3 app/build.py                # refresh ADP and rebuild the console
python3 app/build.py --no-fetch     # rebuild offline from cached data/
node app/test/sync.mjs              # sync, keepers, ownership, outage handling
node app/test/model.mjs             # recommendation model regressions
node app/test/mock.mjs              # adapting to a foreign mock draft
```

### The live console

**It has to be served over http — it cannot run from `file://`.** A page opened
straight off disk has an opaque origin and the browser refuses every
cross-origin request, so the Sleeper sync silently never starts. Published
Artifacts are out for the same reason: their CSP blocks external hosts.

Live at **https://thevalz.github.io/zsh/docs/draft-live.html** — bookmark that
exact URL, it is the one to open on draft day.

Pages is currently set to **Deploy from a branch** → branch
`claude/fantasy-keeper-recommendations-tp9ron` → folder **`/` (root)**, so
Jekyll renders this README at `/zsh/` and the app sits one level down under
`/docs/`. Switching the folder setting to **`/docs`** would shorten the app URL
to `https://thevalz.github.io/zsh/draft-live.html` and drop the README page;
either works, but the URL differs between them, so check the setting before
trusting a bookmark.

Branch deployment rather than the GitHub Actions source, deliberately: the
auto-created `github-pages` environment only permits deployments from the
repository's *default* branch, and the default branch here is a different
`claude/*` branch. Branch deployment has no such gate.

`docs/` is committed build output — `app/build.py` writes both it and
`app/dist/` (the latter gitignored, for local serving). `.github/workflows/pages.yml`
rebuilds daily at 11:17 UTC with fresh ADP and commits `docs/` only if it
changed, so the numbers stay current without anyone remembering. That commit
step needs **Settings → Actions → General → Workflow permissions → Read and
write**; the site itself works without it, only the auto-refresh does not.

The site is public, like the repo.

Locally:

```sh
python3 app/build.py
cd app/dist && python3 -m http.server 8777      # or: npm run serve
# open http://localhost:8777/
```

### Driving a Sleeper mock

The console adopts whatever draft you point it at — team count, round count and
lineup all come from the draft's own settings, so a 10-team single-QB mock is
scored against *that*, not against our 12-team superflex league.

1. Start a mock in Sleeper.
2. Open the site and hit **Connect draft**. It asks Sleeper what drafts your
   account has and offers them as buttons. If your mock is not listed, use
   *enter a draft ID* and paste the draft URL — it pulls the id out.
3. A mock usually does not name you in `draft_order`, so the console asks which
   seat is yours. Pick it, or pass `?slot=3` directly.

`?draft=<id>` and `?league=<id>` both work as URL parameters. Changing draft
clears `slot`, since a new draft means a new seat.

What it does once connected: marks every pick as it happens, derives your own
pick numbers from the draft order and traded picks (so pick 38 is in and 119 is
out without anyone hardcoding it), treats keepers as the picks they are, and
ranks what to take by what will still be on the board when you next pick —
simulating the intervening teams from *their* open starter slots rather than
assuming the board empties in ADP order.

If Sleeper stops answering, the pill turns amber, a banner explains why (with
poll and error counts), and marking picks by hand starts working again. It
re-syncs on its own. Hover the pill any time for live counters.

### Ranking by need and value, not ADP

ADP answers "who is best", which is not the question you have on the clock. The
question is "who is best *for me, here*", and that needs two things ADP does not
carry.

**Value: points over replacement, under our own scoring.** `app/build.py` pulls
FFToday's season projections and scores them with the `scoring_settings` block
in `league_config.json` — our 0.04/yard passing, 4-point passing TDs, full PPR.
Replacement level is where our starting requirements actually put it:
dedicated slots plus each position's share of FLEX and SUPER_FLEX, times 12
teams. That lands at **QB253, RB149, TE149, WR181**.

The superflex consequence is the whole point. Twenty-four quarterbacks start in
this league, so QB replacement level sits 100 points above every other position
and the best quarterback is worth much less *over that bar* than his draft price
implies — **Josh Allen is ADP #1 and VOR #10**. That is the same conclusion the
keeper analysis reached about Goff, arrived at independently.

Projections cover 225 of the pool; the remaining 58 are gap-filled by
interpolating the ADP-to-VOR curve and marked `~` in the table.

**Need** is the same weight the recommendations use — which lineup slot the
player would actually fill, squared, so a position you have already covered has
to be much better to outrank one you have not. The board's **need+value** sort
multiplies the two and adds a 25% bump for players the simulation says will not
survive to your next pick. Toggle to **ADP** for the flat market view.

Watch it work: with QB, SUPER_FLEX and both RB slots filled, the board flips
from RB-led to WR-led without you touching a filter.

### Tiers of production, not of price

A tier is a group of players who score about the same, so which one you end up
with barely matters — what matters is **how many are left before the drop**.

These are tiers of **projected points**, not of ADP. An earlier version broke
tiers on ADP gaps, which describes when a player will be taken rather than what
he produces. Two players a round apart in cost but identical in output are the
same decision, and that disagreement is the thing worth exploiting: across
QB/RB/WR/TE there are currently **22 places where the production order beats the
draft order**.

Two details make the breaks mean something:

- **Only players above replacement are tiered.** Everyone below is one
  undifferentiated `repl` bucket, because they genuinely are interchangeable.
  Tiering the whole pool put every break down in the tail, where interpolated
  values fall off a cliff, and left the draftable top as a single useless
  69-man tier.
- **Breaks go at the largest drops**, with the tier count scaled to how many
  players are actually in play. A fixed point-gap threshold gave 14 tiers at QB
  and 4 at RB.

The **Tier board** panel shows all of it at once — every tier at every position,
how many are undrafted, the points given up if that tier empties (`↓`), and how
many reach your next pick (`→`). The superflex shape is visible immediately:
one Josh Allen, then four, then a **block of 13 essentially interchangeable
arms**, and only 24 quarterbacks above replacement for 24 starting slots.

The player board carries the same tier number per row plus how many of it
remain, and marks the last man in a tier in red — the cliff is right after him.

### Sources considered

Reachable and used: **FFC** (2QB ADP is the spine; PPR pulled too, since its
pool runs deeper, and mapped onto the superflex scale by monotone
interpolation), **FFToday** projections, **Sleeper** trending adds and player
metadata.

Reachable and rejected: FantasyPros superflex ADP returns 200 but renders its
table in JavaScript, so there is nothing to parse from the HTML. Reddit's JSON
endpoints are blocked from here, and there is no X/Twitter access — which is why
the breakout signal below is built from behaviour rather than from text.

The meaningful addition is projections. Every ADP source, however many you
stack, measures the same thing: what drafters did. Projections measure what a
player is expected to *score*, which is the only input that can disagree with
the market for a reason.

### Finding breakouts

ADP is an average of what people already did, so a breakout only shows up in it
after the price has moved. Three independent signals catch the move instead:

- **ADP velocity** — spots gained against archived snapshots. This is the
  market changing its own mind, not anyone's opinion.
- **Sleeper trending adds** — roster adds across every league on the platform.
  Behaviour, not sentiment; nothing is inferring tone from text.
- **Rookie status** — `years_exp == 0`, because a rookie with buzz is a
  different proposition from a veteran with buzz.

Corroboration is the point: the score rewards two signals agreeing far more
than one shouting, so a player has to show up twice to rank highly.

**Buzz never drives a pick.** It moves a recommendation's score by at most 18%,
enough to separate two similar players inside a tier and never enough to pull a
position you don't need to the top. Everything else it does is in the *Rising*
panel, which is a watchlist rather than advice.

## Caveats worth keeping in mind

- **`draft_board.csv` is a reconstruction** from a screenshot and is wrong on
  about 7% of picks — it had Josh Allen at 7.12 when he actually went 1.02.
  `data/rosters_2025.json` came from the platform and is authoritative. Prefer
  it wherever the two disagree.
- **Keepers in `league_config.json` are modelled**, not declared — the model
  assumes each team keeps its top two by surplus. Last season one team made two
  roster moves all year, so not everyone optimises. Replace with the real list
  once the keeper deadline passes.
- **ADP is a lagging average**, which is why the console also carries a buzz
  signal — see below. Re-pull before drafting with `python3 app/build.py`.
- **FFC publishes in windows, not continuously.** A refresh that returns
  identical numbers means no new window has been published, not that the fetch
  failed. Check `data/adp_history.json` for the last date that actually moved.
- **Projections are one source's opinion.** FFToday's own totals reproduce
  exactly for RB/WR/TE from the columns we parse; QB differs by ~4.7 points
  because of how they compute their displayed FPts, not because of the mapping.
  A second projection source would be the next real improvement.
- **Buzz needs history to mean anything.** Velocity is measured against archived
  snapshots in `data/adp_history.json`, bootstrapped from two points recoverable
  from git. It sharpens with every daily rebuild; treat it as thin for now.
- 2025 finishing order is one season of twelve teams. Wins diverged sharply
  from points scored, so treat any strategy inference from it as suggestive.
