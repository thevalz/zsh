# Zebras Shooting Heroin — fantasy tools

Helpers for the Zebras Shooting Heroin 12-team Superflex PPR league on Sleeper.

| File | What it is |
|---|---|
| `matchups.py` | Weekly **matchup checker** for QB / RB / WR / TE, mine and my opponent's (see below) |
| `wr_alignment_overrides.json` | Optional slot/outside overrides for WRs the depth chart mislabels |
| `matchups/` | Generated weekly matchup reports |
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
🔴 Josh Allen QB BUF (QB) @ HOU -- TOUGH (score 89)
     team D  : HOU pass D [A 89, #3] 6.5 yds/att, -0.15 EPA/dropback, 12.8 PPR/g to QBs (#3)

🟡 Derrick Henry RB BAL (RB) @ IND -- NEUTRAL (score 64)
     team D  : IND run D [A 81, #5] 3.9 yds/carry, -0.06 EPA/carry, 19.9 PPR/g to RBs (#10)
     MLB     : CJ Allen [?] (rookie)
     WLB     : Akeem Davis-Gaither [D 26] 8.4 yds/tgt, 128 rtg, 75% cmp, 6 TD / 1 INT on 57 tgt

🔴 Trey McBride TE ARI (TE) @ LAC -- TOUGH (score 73)
     team D  : LAC pass D [A 82, #6] 6.6 yds/att, -0.09 EPA/dropback, 10.5 PPR/g to TEs (#5)
     FS      : Elijah Molden [C 45] 9.1 yds/tgt, 86 rtg, 61% cmp, 2 TD / 2 INT on 44 tgt
     SS      : Derwin James Jr. [A 83] 5.5 yds/tgt, 65 rtg, 61% cmp, 1 TD / 3 INT on 83 tgt
     also NB : Tarheeb Still [B 66] 5.9 yds/tgt, 79 rtg, 65% cmp, 2 TD / 2 INT on 93 tgt
```

### How it works

**Data** (all free, no keys):

- Sleeper API: league, rosters, and the week's head-to-head pairing (so your opponent's lineup is graded too).
- nflverse schedules: opponent, home/away, byes.
- nflverse depth charts: each defense's starting LCB / RCB / nickel, both safeties, and off-ball linebackers
  (MLB + WLB in a 4-3, the two inside LBs in a 3-4); each offense's QB1, RB1, WR1-WR3, TE1.
- nflverse PFR advanced defense stats: per-defender targets, completions, yards, TDs and INTs allowed.
- nflverse team and player weekly stats: what every defense allows (yards and EPA per play, PPR points by position).

**Individual defender grade.** Coverage stats from this season, last season, and (at half weight) the season before
are pooled, so grades move toward current-year play as games pile up while a defender who missed last year still
gets credit for the year before. Every defender with 30+ weighted targets is percentile-ranked *within their
position group* (CBs vs CBs, safeties vs safeties, linebackers vs linebackers) on yards per target, passer rating
and completion % allowed. The average percentile, pulled toward 50 for small samples, is the 0-100 **score**
(100 = hardest to throw on) and maps to a grade: A 80+, B 60+, C 40+, D 20+, F below. Rookies and defenders under
30 targets show `?` and count as a neutral 50.

**Team defense grade.** Last season plus this season (this season weighted 1.5x as it accrues). Pass D combines
yards per attempt, EPA per dropback and PPR points per game allowed to WR+TE; run D combines yards per carry, EPA per
carry and PPR points per game allowed to RBs. Each is a percentile among the 32 teams (100 = toughest, #1 = toughest)
with an A-F grade. Points allowed per game to QB / RB / WR / TE are shown separately with their rank.

**Who faces whom.**

| Position | Individual matchup | Team defense | Blend |
|---|---|---|---|
| QB | none | opponent pass D | 100% team |
| WR | outside: average of the two boundary CBs; slot: the nickel | pass D | 65% CB / 35% team |
| TE | average of the two starting safeties | pass D | 50% / 50% |
| RB | average of the off-ball LBs' coverage grades | run D | 40% LB / 60% team |

**Verdict.** TOUGH at 68+, SOFT at 38 or below, NEUTRAL in between. If a boundary CB is A-grade the receiver is
flagged as a **shadow risk**.

### Limitations

- Depth charts are a proxy for alignment. There is no free public shadow-coverage or per-snap alignment feed, so an
  elite CB who shadows shows up as a flag, not a certainty. Edit `wr_alignment_overrides.json` when a receiver's
  role differs from the chart.
- Early in the season everything leans on last year's stats. The nflverse weekly stat files for the new season
  appear after its first games and are picked up automatically; team defenses in particular can change a lot year
  to year, so treat September team grades as priors.
- PFR coverage stats credit the nearest defender, so zone-heavy defenses can make a defender look better or worse
  than film would. Linebacker coverage grades only speak to an RB's receiving work; the run-defense grade carries
  most of the RB score.
- K and DEF are not graded.
