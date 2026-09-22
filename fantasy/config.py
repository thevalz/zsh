"""League-specific configuration for the Zebras Shooting Heroin monitor."""

LEAGUE_ID = "1390899489962741761"
LEAGUE_NAME = "Zebras Shooting Heroin"
MY_USERNAME = "thevalz"
SEASON = "2026"

# Starting lineup, taken from the league's roster_positions.
# QB / RB / RB / WR / WR / TE / FLEX / FLEX / SUPER_FLEX / K / DEF + 5 bench.
FLEX_ELIGIBLE = ("RB", "WR", "TE")
SUPERFLEX_ELIGIBLE = ("QB", "RB", "WR", "TE")

# How many players at each position a team realistically starts every week.
# The SUPER_FLEX slot is why QB counts as 2: nearly every team plays a second QB
# there, which is what makes quarterbacks scarce in this format.
STARTERS = {"QB": 2, "RB": 2, "WR": 2, "TE": 1}

# Positions the analysis reasons about. K/DEF are streamed and ignored.
SKILL_POSITIONS = ("QB", "RB", "WR", "TE")

# Value curve. A consensus rank is mapped onto a points scale that decays the
# way real fantasy value does -- the gap between the RB1 and RB12 is much
# larger than between the RB40 and RB52.
VALUE_DECAY = 60.0          # larger = flatter curve
VALUE_SCALE = 100.0
UNRANKED_RANK = 9999

# ---------------------------------------------------------------------------
# Value = rest-of-season expected points over replacement (fantasy/model.py).
# Built from what a player is actually being used for (snaps, targets, carries,
# red-zone looks), projected over his remaining schedule, adjusted for the
# depth chart and injuries, and shrunk toward an outside prior while the sample
# is small. Every constant below is scored by `python3 -m fantasy.backtest`;
# change one only with the backtest number cited.
# ---------------------------------------------------------------------------

# Expected points per game = USAGE_ALPHA * (the usage model's prediction) +
# (1 - USAGE_ALPHA) * actual points per game. The usage model is fit on the two
# previous seasons to *predict* rest-of-season points from first-part-of-season
# opportunity (targets, shares of the team's targets and air yards, red-zone
# looks, carries, the team's own scoring chances) with actual points per game
# as one of its inputs, so 1.0 means "trust the fitted predictor"; lowering it
# leans back on raw points. Touchdowns are noise; targets and carries are not.
USAGE_ALPHA = 1.0
USAGE_MIN_GAMES = 1
# The usage fit is trained on players with real roles, so it cannot
# extrapolate down to a backup who took a few mop-up snaps: a quarterback
# with one point on three attempts still projects like a starter, because
# the team's red-zone trips are his team's too. Below this snap share the
# prediction is scaled by share / USAGE_ROLE_SNAP; at or above it (any
# regular, including a 54%-snap workhorse back) nothing changes.
USAGE_ROLE_SNAP = 0.35
# Same idea for a quarterback whose snaps are not posted yet: fewer than this
# many pass attempts per game scales his prediction down proportionally.
QB_ROLE_ATTEMPTS = 20.0

# The outside prior. Only sources that move during the season are used: a
# list whose payload has not changed in SOURCE_STALE_DAYS is flagged static in
# the report header and gets weight 0. Weights are what the backtest recommends
# (proportional to each source's rank correlation with actual points on
# completed weeks); see reports/backtest.md.
PRIOR_SOURCES = {
    "fantasypros": 1.0,   # rest-of-season PPR expert consensus rank, re-ranked weekly
    "espn": 1.0,          # ESPN week-by-week projections, summed over remaining weeks
    "sleeper": 1.0,       # Sleeper week-by-week projections, summed over remaining weeks
}
SOURCE_STALE_DAYS = 10
# The live FantasyPros ROS page can carry two experts on a Monday. Below this
# many, use the DynastyProcess weekly mirror, which has the full set.
FP_MIN_EXPERTS = 8

# Shrinkage toward the prior: ROS = (games * ours + PRIOR_GAMES * prior) /
# (games + PRIOR_GAMES). The prior is worth PRIOR_GAMES games of evidence --
# one game moves a player a fifth of the way, four games half way. A player
# with no games logged is pure prior; a missing week is not a zero.
PRIOR_GAMES = 4.0

# Schedule. Each remaining opponent scales a player's expected points by
# 1 + SCHEDULE_K * (50 - defense percentile vs his position) / 50, clamped to
# +/- SCHEDULE_CAP, where 100 is the toughest defense in the league. Weeks from
# the league's first playoff week onward count PLAYOFF_WEIGHT times, because
# that is when the title is decided. A bye contributes nothing.
SCHEDULE_K = 0.15
SCHEDULE_CAP = 0.15
PLAYOFF_WEIGHT = 2.0

# Replacement level per position: the ROS points of the Nth-best player, where
# N is roughly how many start league-wide. QB is 24 because of superflex --
# that is where the quarterback premium now lives, so POSITION_MULTIPLIER is
# flat.
REPLACEMENT_RANK = {"QB": 24, "RB": 30, "WR": 30, "TE": 12}

# How many weeks an absence costs when no blurb has said otherwise. These are
# defaults, not diagnoses; a player priced on them is marked `unverified`, and
# a blurb with an eligible week overrides them.
DEFAULT_ABSENCE_WEEKS = {"IR": 4, "PUP": 4, "Sus": 2, "NA": 2, "DNR": 99, "Out": 1, "Doubtful": 1}

# Consistency, reported next to value but never priced into it: floor and
# ceiling are the 25th and 75th percentiles of a player's league points per
# game played over last season and this one; bust rate is the share of those
# games under BUST_POINTS. Fewer than CONSISTENCY_MIN_GAMES logged and the
# report shows a dash with the count instead of a number.
BUST_POINTS = 8.0
CONSISTENCY_MIN_GAMES = 6

# Kept flat: the superflex premium is expressed by REPLACEMENT_RANK["QB"].
POSITION_MULTIPLIER = {"QB": 1.0, "RB": 1.0, "WR": 1.0, "TE": 1.0}

# Injury designations, and how much of the starter's workload we assume is
# actually up for grabs when he carries that tag.
VACANCY_WEIGHT = {
    "Out": 1.0,
    "IR": 1.0,
    "PUP": 1.0,
    "Sus": 1.0,
    "DNR": 1.0,
    "NA": 0.8,
    "Doubtful": 0.75,
    "Questionable": 0.35,
    "COV": 0.5,
}

# A designation is not a diagnosis. Sleeper tags a cramp and a hyperextended
# knee both "Questionable", and the only machine-readable difference is whether
# a body part is named -- a real injury usually gets one, a precautionary tag
# often does not. This is a heuristic, not a diagnosis: teams do conceal real
# injuries, so an undisclosed tag is damped rather than dismissed, and the body
# part is printed on the board so a human can make the final call.
UNDISCLOSED_DISCOUNT = 0.6

# Designations that mean "this is news" rather than "this is a lingering tag".
ALERT_STATUSES = ("Out", "IR", "PUP", "Doubtful", "Sus", "NA", "DNR")

# How much of a starter's value flows to each backup, by depth-chart distance.
# The direct backup inherits most of it; the third-stringer sees very little
# unless the two men ahead of him are both hurt.
INHERITANCE = {1: 0.70, 2: 0.25, 3: 0.10}

# Workload does not cascade down a depth chart the same way at every position.
# One running back absorbing another's carries is the cleanest handoff in
# fantasy football. A quarterback's job transfers completely but rarely
# productively. Receiver targets scatter across the whole room rather than
# falling to "the next wideout", so WR inheritance is deliberately damped.
INHERITANCE_BY_POSITION = {"RB": 1.0, "QB": 0.85, "TE": 0.65, "WR": 0.30}

# Standing insurance value behind a *healthy* starter is only a real concept at
# RB and QB. Nobody rosters the WR3 in case the WR1 tweaks a hamstring.
HANDCUFF_POSITIONS = ("RB", "QB")

# A backup only inherits value he is capable of converting. Kyle Allen is
# genuinely the man who plays if Josh Allen goes down and is still worth
# nothing in fantasy, so inherited value is scaled by the backup's own standing.
CREDIBILITY_FLOOR_RANK = 120.0   # at or better than this, fully credible
CREDIBILITY_DECAY = 180.0

# Consensus rank is stale exactly when it matters most -- a third-stringer whose
# situation changed this week still carries last month's rank. A stampede of
# waiver adds is the market re-ranking him in real time, so heavy add volume
# raises the credibility floor regardless of what the rank still says.
MARKET_CREDIBILITY_FULL = 50000.0   # adds/24h that count as a full re-rank
MARKET_CREDIBILITY_CAP = 0.85       # market alone never certifies more than this
MARKET_CREDIBILITY_MIN_ADDS = 5000  # below this, add volume is just noise

# Scoring weights for the waiver board.
W_OPPORTUNITY = 1.0     # value unlocked by injuries ahead of him
# Insurance behind a *healthy* starter is contingent on an injury that has not
# happened. Roughly a quarter of starters miss meaningful time, so speculative
# handcuff value must stay well below live, already-vacated opportunity --
# otherwise the board recommends lottery tickets over players starting Sunday.
W_HANDCUFF = 0.18       # standing insurance value behind a healthy starter
W_OWN_STAKE = 1.6       # multiplier when the starter ahead is on MY roster
W_FIT = 0.40            # bonus for filling a position I am thin at
W_MARKET = 0.30         # penalty as the rest of the world catches on

# When every man ahead of a player is hurt, he is not a handcuff -- he is the
# starter. Linear per-blocker scoring understates that, so a clear path pays a
# bonus scaled to the job he is stepping into.
CLEAR_PATH_BONUS = 0.55

# Reserve lists (IR/PUP) are not a verdict. A player waiting out a window with
# a date on it is a stash; a player finished for the year is not -- and the
# designation alone never says which. Candidates at or above this standing
# value get their news read before we decide, which is a handful of players,
# not the whole pool.
STASH_MIN_VALUE = 6.0
MAX_STASH_LOOKUPS = 6
RESERVE_STATUSES = ("IR", "PUP", "Sus", "DNR", "NA")

# A free agent needs this much score to make the board at all.
MIN_SCORE = 4.0

# Trending-add count that counts as "the league already knows".
MARKET_HOT = 20000.0

CACHE_TTL = {
    "players": 6 * 3600,
    "rosters": 900,
    "users": 6 * 3600,
    "league": 6 * 3600,
    "trending": 1800,
    "state": 1800,
    "stats": 1800,          # the in-progress week; completed weeks cache for a day
    "stats_final": 86400,
    "stats_prior_season": 7 * 86400,
    "usage_fit": 7 * 86400,
    "fantasypros": 6 * 3600,
    "fantasypros_mirror": 24 * 3600,
    "projections": 6 * 3600,
    "crosswalk": 24 * 3600,
    "nflverse": 12 * 3600,
    "news": 900,
}
