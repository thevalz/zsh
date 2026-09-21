"""The remaining schedule: who each team still plays, and how hard that is.

Opponent difficulty is the matchup tool's per-defense percentile of PPR points
allowed to each position (100 = toughest), pooled over last season and this
one. Each remaining week scales a player's expected points by
1 + SCHEDULE_K * (50 - percentile) / 50, clamped, and playoff weeks count
PLAYOFF_WEIGHT times. A bye contributes nothing.
"""

from __future__ import annotations

import math
import sys

from . import config

NFLVERSE = "https://github.com/nflverse/nflverse-data/releases/download"


def horizon(settings: dict, current_week: int) -> tuple[list, int]:
    """(remaining weeks, first playoff week) for this league."""
    s = settings.get("settings") or {}
    start = int(s.get("playoff_week_start") or 15)
    teams = int(s.get("playoff_teams") or 6)
    rounds = max(1, math.ceil(math.log2(teams))) if teams > 1 else 1
    last = start + rounds - 1
    return list(range(max(1, current_week), last + 1)), start


def week_weight(week: int, playoff_start: int, playoff_weight: float = config.PLAYOFF_WEIGHT) -> float:
    return playoff_weight if week >= playoff_start else 1.0


def team_schedule(season: str) -> dict:
    """{team: {week: opponent}} for the regular season, from nflverse. {} on failure."""
    try:
        import matchups
        rows = matchups.read_csv(matchups.fetch_text(
            f"{NFLVERSE}/schedules/games.csv", ttl=config.CACHE_TTL["nflverse"]))
    except Exception as err:  # noqa: BLE001
        print(f"warning: schedule unavailable ({err}); no schedule adjustment", file=sys.stderr)
        return {}
    out: dict = {}
    for r in rows:
        if r["season"] != str(season) or r["game_type"] != "REG":
            continue
        wk = int(r["week"])
        out.setdefault(r["home_team"], {})[wk] = r["away_team"]
        out.setdefault(r["away_team"], {})[wk] = r["home_team"]
    return out


def defense_scores(season: str, through_week: int | None = None) -> dict:
    """{defense: {pos: percentile}} from matchups.load_team_defense; {} on failure."""
    try:
        import matchups
        td = matchups.load_team_defense(int(season), refresh=False, through_week=through_week)
    except Exception as err:  # noqa: BLE001
        print(f"warning: team defense grades unavailable ({err}); no schedule adjustment",
              file=sys.stderr)
        return {}
    return {t: v.get("fp_score", {}) for t, v in td.items()}


def multiplier(defense: dict, opp: str | None, pos: str, k: float = config.SCHEDULE_K,
               cap: float = config.SCHEDULE_CAP) -> float:
    """How much an opponent moves a player's expected points. 1.0 = league average."""
    if not opp:
        return 0.0                                   # bye
    score = (defense.get(opp) or {}).get(pos)
    if score is None:
        return 1.0
    adj = k * (50.0 - float(score)) / 50.0
    return 1.0 + max(-cap, min(cap, adj))


class Schedule:
    """Per-team, per-week multipliers for the remaining horizon."""

    def __init__(self, season: str, settings: dict, current_week: int,
                 through_week: int | None = None, k: float = config.SCHEDULE_K,
                 playoff_weight: float = config.PLAYOFF_WEIGHT, enabled: bool = True):
        self.weeks, self.playoff_start = horizon(settings, current_week)
        self.playoff_weight = playoff_weight
        self.k = k
        self.enabled = enabled
        self.sched = team_schedule(season) if enabled else {}
        self.defense = defense_scores(season, through_week) if enabled else {}
        self.available = bool(self.sched) and bool(self.defense)

    def opponent(self, team: str, week: int):
        return (self.sched.get(team) or {}).get(week)

    def weight(self, week: int) -> float:
        return week_weight(week, self.playoff_start, self.playoff_weight)

    def mult(self, team: str, pos: str, week: int) -> float:
        if not self.enabled or not self.sched:
            return 1.0                               # no schedule data: flat
        opp = self.opponent(team, week)
        if opp is None:
            return 0.0                               # bye
        return multiplier(self.defense, opp, pos, self.k)

    def weighted_games(self, team: str) -> float:
        return sum(self.weight(w) for w in self.weeks if self.opponent(team, w) or not self.sched)

    def describe(self) -> str:
        if not self.weeks:
            return "no remaining weeks"
        tail = f"weeks {self.weeks[0]}–{self.weeks[-1]}, playoff weeks from {self.playoff_start} ×{self.playoff_weight:g}"
        if not self.enabled:
            return tail + " (schedule adjustment off)"
        if not self.available:
            return tail + " ⚠️ schedule/defense data unavailable — flat"
        return tail + f", opponent adjustment ±{config.SCHEDULE_CAP:.0%}"
