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

# Value curve. Sleeper's `search_rank` is a rough consensus rank; this maps it
# onto a points scale that decays the way real fantasy value does -- the gap
# between the RB1 and RB12 is much larger than between the RB40 and RB52.
VALUE_DECAY = 60.0          # larger = flatter curve
VALUE_SCALE = 100.0
UNRANKED_RANK = 9999

# Superflex premium. A startable QB is worth more here than his raw rank implies
# because 12 teams are chasing ~24 startable quarterbacks.
POSITION_MULTIPLIER = {"QB": 1.20, "RB": 1.0, "WR": 1.0, "TE": 1.0}

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
    "news": 900,
}
