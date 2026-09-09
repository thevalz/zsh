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
python3 -m fantasy.monitor watch     # hourly mode — leads with what changed
python3 -m fantasy.monitor waiver    # waiver board + handcuff table only
python3 -m fantasy.monitor trades    # roster strengths + trade targets only
```

No dependencies beyond the Python standard library. Everything comes from the
public Sleeper API (no auth) plus ESPN's public news feed.

## How the numbers work

**Player value** — Sleeper's `search_rank` decayed exponentially
(`100 · e^(-rank/60)`), because the gap between the RB1 and the RB12 is far
larger than between the RB40 and the RB52. Quarterbacks get a 1.20× superflex
premium. This is a consensus-rank proxy, not a projection; it is the best
signal the public API exposes.

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

`watch` prints a `::PUSH::` line: one sub-200-character summary suitable for a
phone notification, or a note that nothing was worth interrupting for.

## Tuning

Every weight lives in `config.py` with a comment explaining it. The ones most
worth revisiting: `INHERITANCE_BY_POSITION`, `VACANCY_WEIGHT`, `MARKET_HOT`
(what counts as "the league already knows"), and `MIN_SCORE`.
