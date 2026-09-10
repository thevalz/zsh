# Zebras Shooting Heroin — fantasy tools

Helpers for the Zebras Shooting Heroin 12-team Superflex PPR league on Sleeper.

Live site: **https://thevalz.github.io/zsh/** (see [Deploying](#deploying)).

| File | What it is |
|---|---|
| `matchups.py` | Weekly **matchup checker** for QB / RB / WR / TE, mine and my opponent's (see below) |
| `fantasy/` | **Waiver & trade monitor** (`python3 -m fantasy.monitor report`); see `fantasy/README.md` |
| `build_site.py` | Renders the reports into the static site in `docs/` |
| `.github/workflows/site.yml` | Scheduled GitHub Actions job that regenerates the reports and site |
| `wr_alignment_overrides.json` | Optional slot/outside overrides for WRs the depth chart mislabels |
| `shadow_corners.json` | Corners documented as travelling with WR1s; a team's WR1 is scored against that corner alone |
| `matchups/`, `reports/` | Generated weekly matchup reports and the latest waiver report |
| `docs/` | The built site (committed by the workflow); also holds the archived draft console |
| `last-years-draft.md`, `draft_board.csv` | Record of last year's draft |

## Matchup checker

Before setting a lineup, find out which of your starters (and your opponent's) face a tough individual defender,
and which face a weak pass or run defense.

```bash
python3 matchups.py                                  # my lineup + this week's opponent
python3 matchups.py --week 3                         # a different week
python3 matchups.py --all                            # every team's QB1, RB1, WR1-WR3, TE1 (waiver / trade targets)
python3 matchups.py --player "Bijan Robinson" --player "Trey McBride"
python3 matchups.py --defenses                       # team pass / run defense table
python3 matchups.py --cbs                            # starting-CB toughness leaderboard
python3 matchups.py --full --markdown -o matchups/week03.md   # the weekly report, all sections
python3 matchups.py --refresh                        # bypass the 12-hour download cache
```

Requires Python 3.10+ and nothing else. Downloads are cached in `.cache/` for 12 hours.

### Sample

```
🔴 Emeka Egbuka WR TB (WR) @ CIN -- TOUGH (score 83, outside)
     team D  : CIN pass D [F 19, #29] 7.8 yds/att, +0.19 EPA/dropback, 25.3 PPR/g to WRs (#2)
     points  : leaks to RB (#32), TE (#32); holds WR (#2)
     RCB     : DJ Turner II [B 74] 7.0 yds/tgt, 79 rtg, 49% cmp, 4 TD / 2 INT on 97 tgt
               2025: 73 tgt, 48% cmp, 6.4 y/t, 76 rtg, 4 TD / 2 INT | 2024: 48 tgt, 54% cmp, 8.8 y/t, 91 rtg, 1 TD / 0 INT
     also LCB: Dax Hill [B 61] 6.1 yds/tgt, 88 rtg, 64% cmp, 3 TD / 1 INT on 98 tgt
     also NB : Jalen Davis [?] (low sample (20 tgt))
     shadow  : DJ Turner II travels with WR1s -- Al Golden (CIN): 'take on the go-to guy' every week; ...
     vegas   : underdog by 3.5, total 50.5, implied 23.5 -- underdog in a high total -- pass-volume script
     volume  : 2025: 7.5 tgt/g, 24% share, 3.7 rec, 55 yds, 11.5 PPR/g (17 g)

🟡 Quentin Johnston WR LAC (BN) vs ARI -- NEUTRAL (score 51, outside)
     team D  : ARI pass D [D 25, #26] 7.2 yds/att, +0.16 EPA/dropback, 30.2 PPR/g to WRs (#15)
     points  : leaks to RB (#30), TE (#31)
     LCB     : Will Johnson [D 33] 7.4 yds/tgt, 110 rtg, 66% cmp, 4 TD / 0 INT on 62 tgt
               2025: 62 tgt, 66% cmp, 7.4 y/t, 110 rtg, 4 TD / 0 INT
     RCB     : Denzel Burke [B 67] 6.9 yds/tgt, 71 rtg, 61% cmp, 2 TD / 3 INT on 54 tgt
               2025: 54 tgt, 61% cmp, 6.9 y/t, 71 rtg, 2 TD / 3 INT
     also NB : Max Melton [D 40] 8.1 yds/tgt, 93 rtg, 63% cmp, 1 TD / 0 INT on 71 tgt
     vegas   : favoured by 9.5, total 47.5, implied 28.5 -- heavy favourite -- run-leaning script, fewer late throws
     volume  : 2025: 6.5 tgt/g, 20% share, 3.9 rec, 57 yds, 13.2 PPR/g (13 g)

🔴 Trey McBride TE ARI (TE) @ LAC -- TOUGH (score 73)
     team D  : LAC pass D [A 82, #6] 6.6 yds/att, -0.09 EPA/dropback, 10.5 PPR/g to TEs (#5)
     FS      : Elijah Molden [C 45] 9.1 yds/tgt, 86 rtg, 61% cmp, 2 TD / 2 INT on 44 tgt
     SS      : Derwin James Jr. [A 83] 5.5 yds/tgt, 65 rtg, 61% cmp, 1 TD / 3 INT on 83 tgt
```

The two receivers above are the case that motivated the deeper output. Cincinnati's pass defense grades an F,
which used to make Egbuka look like a soft spot. The points line shows the F comes from tight ends and backs
while receivers are held to the second-fewest points in the league, the shadow line shows the WR1 draws a travel
corner rather than the average of both boundary corners, and the corner's per-season split shows a 2025 that
was much better than his pooled grade. Johnston's boundary corner is the softest either receiver sees, but the
Vegas line says his team is a 9.5-point favourite, which caps the throws. The volume line is the half of the
matchup no corner can take away.

### How it works

**Data** (all free, no keys):

- Sleeper API: league, rosters, and the week's head-to-head pairing (so your opponent's lineup is graded too).
- nflverse schedules: opponent, home/away, byes.
- nflverse depth charts: each defense's starting LCB / RCB / nickel, both safeties, and off-ball linebackers
  (MLB + WLB in a 4-3, the two inside LBs in a 3-4); each offense's QB1, RB1, WR1-WR3, TE1.
- nflverse PFR advanced defense stats: per-defender targets, completions, yards, TDs and INTs allowed.
- nflverse team and player weekly stats: what every defense allows (yards and EPA per play, PPR points by position),
  and each player's own targets, target share and PPR per game last season (this season is added as it accrues).
- nflverse schedules: the Vegas spread and total, for the game-script line.
- Sleeper player file: injury tags on the defenders themselves (⚕ next to the name).
- `shadow_corners.json`: corners whose coordinator is documented as travelling them with the opponent's WR1.

**Individual defender grade.** Coverage stats from this season, last season, and (at half weight) the season before
are pooled, so grades move toward current-year play as games pile up while a defender who missed last year still
gets credit for the year before. Every defender with 30+ weighted targets is percentile-ranked *within their
position group* (CBs vs CBs, safeties vs safeties, linebackers vs linebackers) on yards per target, passer rating
and completion % allowed. The average percentile, pulled toward 50 for small samples, is the 0-100 **score**
(100 = hardest to throw on) and maps to a grade: A 80+, B 60+, C 40+, D 20+, F below. Rookies and defenders under
30 targets show `?` and count as a neutral 50. Each defender's per-season line is printed under the pooled one, and
a second score built from the last two seasons only is shown as `recent` when it differs by 8 or more; the matchup
uses the average of the pooled and recent scores, so a corner who broke out last year is not dragged down by his
rookie tape and a one-year wonder is not taken at face value either.

**Team defense grade.** Last season plus this season (this season weighted 1.5x as it accrues). Pass D combines
yards per attempt, EPA per dropback and PPR points per game allowed to WR+TE; run D combines yards per carry, EPA per
carry and PPR points per game allowed to RBs. Each is a percentile among the 32 teams (100 = toughest, #1 = toughest)
with an A-F grade. Points allowed per game to QB / RB / WR / TE are shown separately with their rank, and a
`points` line names where a defense *leaks* (ranked 27th or worse against a position) and what it *holds* (top 6).

The overall pass grade is what a quarterback faces. A receiver or tight end is blended with the points the defense
allows *to that position*, not the overall grade: a defense can be an F against the pass and still hold receivers
if the leak is at tight end and running back, and the old blend called that a soft spot.

**Who faces whom.**

| Position | Individual matchup | Team defense | Blend |
|---|---|---|---|
| QB | none | opponent pass D | 100% team |
| WR | outside: average of the two boundary CBs; slot: the nickel; a WR1 facing a listed travel corner: that corner alone | PPR allowed to WRs | 65% CB / 35% team |
| TE | average of the two starting safeties | PPR allowed to TEs | 50% / 50% |
| RB | average of the off-ball LBs' coverage grades | run D | 40% LB / 60% team |

**Verdict.** TOUGH at 68+, SOFT at 38 or below, NEUTRAL in between.

**Shadow coverage.** `shadow_corners.json` lists corners whose defense is documented as travelling them with the
opponent's top receiver, each with a note and a source. When one of them is a listed boundary corner for the
opponent and the receiver being graded is his team's WR1 playing outside, the matchup is scored against that
corner alone (🔒) instead of the boundary average. Entries are matched by name, so a trade moves the corner with
him. Add a corner only when the usage is documented; occasional travellers are left out on purpose. Separately, an
A-grade boundary corner still flags a WR1 as a **shadow risk** (⚠).

**Game script and volume.** Every matchup carries the Vegas spread, total and the team's implied points with a
one-line read: a favourite of 7 or more leans run, an underdog of 3 or more in a 48+ total throws, a 41-or-under
total is a slog. Under that is the player's own volume from last season: targets per game, target share, catches,
yards and PPR per game (carries for a back). A tough corner caps a receiver's ceiling; his target share is his floor.

### Limitations

- Depth charts are a proxy for alignment. There is no free public shadow-coverage or per-snap alignment feed, so an
  elite CB who shadows shows up as a flag, not a certainty. Edit `wr_alignment_overrides.json` when a receiver's
  role differs from the chart.
- The travel-corner list is hand-maintained. Nothing public says who shadows whom each week, so the file only
  carries corners whose coordinator has said so on the record; a corner missing from it is scored the old way.
- Early in the season everything leans on last year's stats. The nflverse weekly stat files for the new season
  appear after its first games and are picked up automatically; team defenses in particular can change a lot year
  to year, so treat September team grades as priors.
- PFR coverage stats credit the nearest defender, so zone-heavy defenses can make a defender look better or worse
  than film would. Linebacker coverage grades only speak to an RB's receiving work; the run-defense grade carries
  most of the RB score.
- K and DEF are not graded.

## Deploying

The site is plain static HTML in `docs/`, rebuilt by `.github/workflows/site.yml`:

1. `matchups.py --full --markdown --out-dir matchups` writes `matchups/weekNN.md`.
2. `python3 -m fantasy.monitor report --out reports/waivers.md` writes the waiver/trade report and updates
   `fantasy/state/snapshot.json` so the next run can diff against it.
3. `build_site.py` renders both into `docs/` (dashboard, per-week pages, waiver page, weeks index).
4. The workflow commits `docs/`, `matchups/`, `reports/` and the snapshot back to the branch.

Rebuild locally with the same three commands, then open `docs/index.html`.

**One-time repository settings** (cannot be changed from a workflow):

- *Settings → Pages → Build and deployment*: Source **Deploy from a branch**, branch **this branch**, folder
  **/docs**. Pages was previously pointed at the keeper branch's root; `docs/draft-live.html` here is an archived
  copy of that draft console so its URL keeps working under the new source.
- *Settings → General → Default branch*: GitHub only runs scheduled (`cron`) workflows from the default branch.
  Make this branch the default, or merge it into the default, for the every-6-hours rebuild to happen on its own.
  Until then, trigger it from *Actions → Build matchups + waiver site → Run workflow*.

