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
app/build.py               refresh ADP + player-id map, emit app/dist/draft-live.html
app/template.html          page shell and CSS, with __DATA__ / __SCRIPT__ slots
app/src/state.js           data, persistence, whose-pick arithmetic
app/src/model.js           roster fill, positional need, need-aware simulation
app/src/sleeper.js         Sleeper client: discovery, polling, backoff
app/src/ui.js              rendering and events
app/test/sync.mjs          22 assertions against a stubbed API (real payloads)
app/test/live.mjs          smoke test against the real API — needs an http origin
data/league_config.json    league settings, our picks, keeper rule, modelled keepers
data/players_sf_adp.json   242-player superflex pool (FFC 2QB ADP)
data/player_ids.json       Sleeper player_id -> our pool, so the app never
                           downloads Sleeper's 14 MB player file at runtime
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
node app/test/sync.mjs              # 22 assertions, stubbed API
```

### The live console

**It has to be served over http — it cannot run from `file://`.** A page opened
straight off disk has an opaque origin and the browser refuses every
cross-origin request, so the Sleeper sync silently never starts. Published
Artifacts are out for the same reason: their CSP blocks external hosts.

```sh
python3 app/build.py
cd app/dist && python3 -m http.server 8777
# then open http://localhost:8777/draft-live.html
```

GitHub Pages works too if you want it on your phone at the table.

It finds the draft on its own from the league in `league_config.json`.
`?draft=<id>` points it at any other draft — that is how you drive a Sleeper
mock. `?league=<id>` switches leagues.

What it does once connected: marks every pick as it happens, derives your own
pick numbers from the draft order and traded picks (so pick 38 is in and 119 is
out without anyone hardcoding it), treats keepers as the picks they are, and
ranks what to take by what will still be on the board when you next pick —
simulating the intervening teams from *their* open starter slots rather than
assuming the board empties in ADP order.

If Sleeper stops answering, the pill turns amber, a banner explains why, and
marking picks by hand starts working again. It re-syncs on its own.

## Caveats worth keeping in mind

- **`draft_board.csv` is a reconstruction** from a screenshot and is wrong on
  about 7% of picks — it had Josh Allen at 7.12 when he actually went 1.02.
  `data/rosters_2025.json` came from the platform and is authoritative. Prefer
  it wherever the two disagree.
- **Keepers in `league_config.json` are modelled**, not declared — the model
  assumes each team keeps its top two by surplus. Last season one team made two
  roster moves all year, so not everyone optimises. Replace with the real list
  once the keeper deadline passes.
- **ADP is a snapshot.** The superflex sample opens 24 July, so late-August camp
  news is underweighted. Re-pull before drafting:
  `curl -s "https://fantasyfootballcalculator.com/api/v1/adp/2qb?teams=12&year=2026&position=all"`
- 2025 finishing order is one season of twelve teams. Wins diverged sharply
  from points scored, so treat any strategy inference from it as suggestive.
