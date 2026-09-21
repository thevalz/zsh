# Zebras Shooting Heroin — waiver & trade monitor

Watches the waiver wire for workload that is about to change hands, and scans
the other 11 rosters for managers whose weaknesses match my strengths.

## Why it is built this way

The league is **12-team, full PPR, superflex** (`QB / RB / RB / WR / WR / TE /
FLEX / FLEX / SUPER_FLEX / K / DEF`), $100 FAAB, trade deadline week 12.
Superflex is the fact that drives everything: nearly every team starts a second
quarterback, so 12 teams chase roughly 24 startable QBs and a quarterback is
worth more than his raw rank implies.

The waiver engine does not rank free agents by talent — that is what the
league's own "best available" list is for. It ranks them by **whose job is
about to open up**, and cross-references that against **how many people have
noticed**. A backup with a big opportunity and no trending adds is the buy; the
same player after 40,000 adds is just expensive.

## Usage

```
python3 -m fantasy.monitor report    # full standing analysis
python3 -m fantasy.monitor watch     # hourly mode — leads with what changed; read-only
python3 -m fantasy.monitor waiver    # waiver board + handcuff table only
python3 -m fantasy.monitor trades    # roster strengths + trade targets only
python3 -m fantasy.backtest          # score the value model against completed weeks
python3 -m fantasy.monitor values    # every roster's players with the numbers behind their value
```

No dependencies beyond the Python standard library. Everything comes from
public, keyless endpoints: the Sleeper API, ESPN's fantasy API, FantasyPros'
public rankings pages (and DynastyProcess's mirror of them), and RotoWire news.

## How the numbers work

**Player value** — rest-of-season expected points over replacement, ranked
across every skill player and pushed through the curve `100 · e^(-rank/60)`
(the gap between the RB1 and the RB12 is far larger than between the RB40 and
the RB52, and every threshold downstream is calibrated to that scale). For
each player:

1. *Usage* (`fantasy/usage.py`) — what he is actually being used for this
   season, per game, from Sleeper's weekly stat lines and the team rows they
   sit inside (nflverse snap counts fill in while Sleeper's are pending).
   Per-position coefficients turn that into expected *future* points per
   game: they are fit on the two previous seasons to predict rest-of-season
   PPG from first-part-of-season opportunity, at three points in the season,
   which discounts a hot touchdown month by itself. The feature set per
   position is whatever beat first-part PPG alone out of sample (fit on one
   season, test on the other, both ways): QB — PPG, rushing first downs,
   team red-zone attempts (r 0.46 vs 0.42); RB — PPG, touches, share of the
   team's red-zone carries (0.85, a tie); WR — PPG, targets, receiving first
   downs (0.78 vs 0.78); TE — target share, air-yards share, first downs
   (0.84 vs 0.80, PPG dropped). Wider sets scored lower. Snap share rides
   along as an extra column when it is available. The report header prints
   the fit's out-of-sample number each run. `USAGE_ALPHA` blends the
   prediction with raw PPG; at 1.0 the fitted predictor is trusted outright.
   The fit is trained on players with real roles and cannot extrapolate down
   to a backup's mop-up snaps, so below `USAGE_ROLE_SNAP` (35% of snaps) the
   prediction is scaled by snap share, and a quarterback whose snaps are not
   posted yet is scaled by attempts against `QB_ROLE_ATTEMPTS`.
2. *Depth chart and injuries* — who plays which weeks. A reserve-list or Out
   player is projected to miss `DEFAULT_ABSENCE_WEEKS` for his tag unless a
   blurb gave an eligible week, and the header counts how many absences rest
   on a default (`unverified`). Whoever sits behind an absent player inherits
   a share of his expected points for those weeks, using the same
   `INHERITANCE` and `INHERITANCE_BY_POSITION` factors as the waiver board.
3. *Schedule* (`fantasy/schedule.py`) — every remaining week through the last
   playoff week, each opponent scales expected points by
   `1 + SCHEDULE_K · (50 − percentile of points allowed to his position) / 50`,
   clamped to `±SCHEDULE_CAP`, using the matchup tool's defense grades. Weeks
   from the league's first playoff week count `PLAYOFF_WEIGHT` times. A bye
   contributes nothing. This gives weighted rest-of-season points, "ours".
4. *Prior* (`fantasy/sources.py`) — outside lists, each converted to weighted
   rest-of-season points over the same horizon: FantasyPros ROS expert
   consensus (a rank, converted through the ladder the other sources define;
   the DynastyProcess weekly mirror when the live page has fewer than
   `FP_MIN_EXPERTS`), ESPN's week-by-week projections summed over the
   remaining weeks, and Sleeper's week-by-week projections summed the same
   way under league scoring. Only lists that move during the season are
   used: a payload unchanged for `SOURCE_STALE_DAYS` is flagged static in the
   header and gets weight 0. `PRIOR_SOURCES` weights are what the backtest
   recommends. Sleeper's static season-total feed and its `search_rank` are
   no longer inputs.
5. *Shrinkage* — `ROS = (games · ours + PRIOR_GAMES · prior) / (games +
   PRIOR_GAMES)`, plus inherited points. The prior is worth `PRIOR_GAMES`
   games of evidence; a player with no games is pure prior.
6. *Replacement* — `REPLACEMENT_RANK` per position (QB 24, because of
   superflex; that is where the quarterback premium now lives, so
   `POSITION_MULTIPLIER` is flat). Points over replacement, ranked, onto the
   curve.

`healthy_value()` is the same pipeline with the player's own absence removed —
the stash question is what he is worth once he is back. A player with no
games this season is projected from last season's usage. `credibility()`
reads the rank behind the value.

**Backtest** — `python3 -m fantasy.backtest` scores all of this against what
actually happened. For each completed week N it rebuilds the table as it
stood before week N (usage and defense grades through N-1) and compares the
ordering with week N's league-scored points: Spearman ρ and top-N hit rate
per position, for the live model, the model with each part switched off, the
model across grids of `PRIOR_GAMES`, `USAGE_ALPHA` and `SCHEDULE_K`, the
prior alone, each source alone, the old `search_rank` curve, and Sleeper's
own pre-game projection for that week. It then sets a lineup for every roster
in the league from each candidate's values, with players who did not dress
that week excluded for every candidate, and reports the points left on the
bench against what the managers actually left. Team-level ρ across the 12
rosters and a recommended set of source weights (∝ ρ − 0.5) come out of the
same run, into `reports/backtest.md`. Rules: the prior is today's list
because no source publishes history, and the report says so on every table;
an in-progress week is labelled and never used to retune; a constant changes
only with the backtest number cited. A one-week test cannot see most of what
the schedule term does (it averages fifteen opponents), so a null result
there is expected; the lineup and team-level numbers are the better read on it.

**Opportunity** — for each free agent, the value of the players ahead of him on
his NFL depth chart, multiplied by:

- *depth distance* — the direct backup inherits 70%, the next man 25%, the
  third 10%. Distance is the real gap in depth-chart slots, so the tenth
  receiver listed does not get credited as anybody's handcuff.
- *position* — `RB 1.0, QB 0.85, TE 0.65, WR 0.30`. One back absorbing
  another's carries is the cleanest handoff in fantasy; receiver targets
  scatter across the whole room instead of falling to "the next wideout".
- *injury weight* — `Out/IR/PUP = 1.0`, `Doubtful = 0.75`, `Questionable = 0.35`.
- *credibility* — the backup's own standing. Kyle Allen is genuinely the man
  who plays if Josh Allen goes down, and is still worth nothing.
- *my stake* — a 1.6× multiplier when the player ahead is on **my** roster,
  because that is insurance I specifically need.

**Tiers** — `URGENT` the job is open now · `BUY EARLY` it is not, and the
market has not priced the risk · `INSURANCE` protects one of my starters ·
`STARTER FA` simply a good player nobody rostered · `SPECULATIVE` everything
else. Suggested FAAB scales with tier, the player's own value, and how
contested he is.

**Trade fit** — surplus is measured *above replacement level* (the best free
agent at that position), so a fourth running back only counts as an asset if
he is better than what anyone could claim for a dollar. Teams are then ranked
by **two-way** fit: what I have spare that they lack, plus what they have spare
that I lack. Positions where I am below the league median are never offered
out, however badly the other team needs them. Where no fair 1-for-1 exists, the
tool packages 2-for-1s — the way a deep, flat roster converts quantity into a
starter.

## News: two jobs, two mechanisms

A designation is not a diagnosis. Sleeper tags a cramp and a hyperextended knee
both "Questionable", which once put a third-string back at the top of this
board on the strength of a starter who was, in fact, fine.

The fix is to keep two layers apart:

**Completeness** — the Sleeper snapshot diff. It reads every tracked player
each run and compares full state, so it cannot miss a change the way a stream
can. It just cannot say how bad the change is.

**Interpretation** — RotoWire's per-player blurb history, keyed by the
`rotowire_id` Sleeper already carries for 796 of 813 skill players. Looked up
**only for players the diff already flagged**, so it stays a handful of
requests an hour.

That ordering matters. The national feeds are a five-item window — about five
hours on a quiet day, roughly fifteen minutes on a practice-report afternoon —
so trying to catch news as it goes past loses most of it. Asking about a named
player after the diff surfaces him has no window at all.

Each blurb is read for the two things a designation cannot tell you: whether
the language sounds serious (`MRI`, `torn`, `week-to-week`) or trivial
(`cramp`, `precautionary`, `rest day`), and whether he practiced. **Practice
participation is the best available predictor of Sunday** — Questionable plus a
full Friday means he plays; Questionable plus DNP is a coin flip. An alert that
reads "likely minor" and is not a DNP drops below the notification threshold,
so precautionary tags stop waking you.

Ad-hoc lookup for any player:

```
python3 -m fantasy.monitor player --name "Emeka Egbuka"
```

RotoWire often carries detail Sleeper lacks — Egbuka's body part is
`Undisclosed` in the player file and `toe` in the blurbs.

## State and alerting

`state/snapshot.json` records injury designations, depth-chart order, and
league ownership for every rostered player and their direct backups. Each run
diffs against it, so hourly checks report *changes* — a new injury, a
promotion, a drop, a trade — rather than re-reading the same board. The
snapshot is committed so that a fresh run on a clean checkout still has
yesterday's baseline to compare against.

`report` rewrites the snapshot and is what the site workflow runs hourly, so
the committed copy is the workflow's. `watch` only reads it (pass `--save` to
change that) and prints the baseline's commit time and age at the top of
"Since last run"; past two hours the workflow is lagging and some alerts will
be repeats of the previous hour.

`watch` prints a `::PUSH::` line: one sub-200-character summary suitable for a
phone notification, or a note that nothing was worth interrupting for.

## Tuning

Every weight lives in `config.py` with a comment explaining it. The ones most
worth revisiting: `INHERITANCE_BY_POSITION`, `VACANCY_WEIGHT`, `MARKET_HOT`
(what counts as "the league already knows"), and `MIN_SCORE`.
