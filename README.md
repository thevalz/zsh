# Zebras Shooting Heroin — fantasy tools

Helpers for the Zebras Shooting Heroin 12-team Superflex PPR league on Sleeper.

| File | What it is |
|---|---|
| `wr_cb_matchups.py` | Weekly **WR vs CB matchup checker** (see below) |
| `wr_alignment_overrides.json` | Optional slot/outside overrides for WRs the depth chart mislabels |
| `matchups/` | Generated weekly matchup reports |
| `last-years-draft.md`, `draft_board.csv` | Record of last year's draft |

## WR vs CB matchup checker

Before setting your lineup, find out which of your receivers is drawing a tough cornerback.

```bash
python3 wr_cb_matchups.py                          # my WRs, current week
python3 wr_cb_matchups.py --week 3                 # a different week
python3 wr_cb_matchups.py --all                    # every team's WR1-WR3 (waiver / trade targets)
python3 wr_cb_matchups.py --player "Ladd McConkey" # any WR, repeatable
python3 wr_cb_matchups.py --cbs                    # starting-CB toughness leaderboard
python3 wr_cb_matchups.py --full --markdown -o matchups/week03.md   # the weekly report
python3 wr_cb_matchups.py --refresh                # bypass the 12-hour download cache
```

Requires Python 3.10+ and nothing else. Downloads are cached in `.cache/` for 12 hours.

### Sample

```
🟡 Emeka Egbuka TB (BN) @ CIN -- NEUTRAL (score 67, outside WR)
     primary : Dax Hill [B 61] 6.1 yds/tgt, 88 rtg, 64% cmp, 3 TD / 1 INT on 98 tgt
     primary : DJ Turner II [B 74] 7.0 yds/tgt, 79 rtg, 49% cmp, 4 TD / 2 INT on 97 tgt
     also    : Jalen Davis [?] (low sample (20 tgt))
```

### How it works

**Data** (all free, no keys):

- Sleeper API: your league, roster, starters, and the current NFL week.
- nflverse schedules: opponent, home/away, byes.
- nflverse depth charts: each defense's starting LCB / RCB / nickel and each offense's WR1 / WR2 / WR3.
- nflverse PFR advanced defense stats: per-defender targets, completions, yards, TDs and INTs allowed.

**CB grade.** Coverage stats from this season, last season, and (at half weight) the season before are pooled, so
grades move toward current-year play as games pile up while a CB who missed last year still gets credit for the
year before. Every cornerback with 30+ weighted targets is percentile-ranked on yards per target, passer rating and
completion % allowed. The average percentile is the 0-100 **score** (100 = hardest to throw on) and maps to a grade:
A 80+, B 60+, C 40+, D 20+, F below. Safeties who play nickel are graded on the same CB scale. Rookies and CBs
under 30 targets show `?` and count as a neutral 50. Scores are pulled toward 50 when a CB's sample is small.

**WR to CB matching.** Outside receivers (WR1/WR2 on the depth chart) see both boundary corners over a game, so their
matchup score is the average of the opponent's LCB1 and RCB1. Slot receivers (WR3, or anyone listed as `slot` in
`wr_alignment_overrides.json`) are matched to the nickel. If a boundary CB is A-grade, the receiver is flagged as a
**shadow risk**.

**Verdict.** TOUGH at 68+, SOFT at 38 or below, NEUTRAL in between.

### Limitations

- Depth charts are a proxy for alignment. There is no free public shadow-coverage or per-snap alignment feed, so an
  elite CB who shadows shows up as a flag, not a certainty. Edit `wr_alignment_overrides.json` when a receiver's
  role differs from the chart.
- Early in the season the grades lean on last year's stats. They reweight automatically as 2026 games land in the
  nflverse data.
- PFR coverage stats credit the nearest defender, so zone-heavy defenses can make a CB look better or worse than
  film would.
- Only WRs are graded. RB and TE matchups are driven by linebackers and safeties, which this does not model.
