"""Domain model: players, values, depth charts and league rosters."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import config, sleeper


def player_value(p: dict) -> float:
    """Approximate standalone fantasy value on a 0-100ish scale.

    Sleeper's `search_rank` is the only consensus signal the public API exposes.
    We decay it exponentially so the curve matches how fantasy value actually
    behaves, then apply the superflex quarterback premium.
    """
    if not p:
        return 0.0
    rank = p.get("search_rank") or config.UNRANKED_RANK
    if rank >= config.UNRANKED_RANK:
        return 0.0
    base = config.VALUE_SCALE * math.exp(-rank / config.VALUE_DECAY)
    return base * config.POSITION_MULTIPLIER.get(p.get("position"), 1.0)


def is_available_body(p: dict) -> bool:
    """Filter out retired/practice-squad ghosts that clutter the player file."""
    return bool(p.get("team")) and p.get("position") in config.SKILL_POSITIONS


def vacancy(p: dict) -> float:
    """How much of this player's workload his injury tag puts up for grabs.

    Damped when no body part is named: an undisclosed Questionable is far more
    often precautionary than a named one.
    """
    base = config.VACANCY_WEIGHT.get(p.get("injury_status"), 0.0)
    if not base:
        return 0.0
    part = (p.get("injury_body_part") or "").strip().lower()
    if part in ("", "undisclosed", "not injury related"):
        base *= config.UNDISCLOSED_DISCOUNT
    return base


def injury_label(p: dict) -> str:
    """e.g. 'Questionable (Knee)' -- the detail that separates a cramp from an MRI."""
    status = p.get("injury_status")
    if not status:
        return ""
    part = (p.get("injury_body_part") or "").strip()
    return f"{status} ({part})" if part else status


@dataclass
class Team:
    roster_id: int
    owner_id: str
    display_name: str
    team_name: str
    is_me: bool
    player_ids: list = field(default_factory=list)
    wins: int = 0
    losses: int = 0
    faab_left: int = 100

    @property
    def label(self) -> str:
        return self.team_name or self.display_name


@dataclass
class League:
    teams: list
    players: dict
    settings: dict

    @property
    def me(self) -> Team:
        return next(t for t in self.teams if t.is_me)

    @property
    def rostered(self) -> set:
        out = set()
        for t in self.teams:
            out.update(t.player_ids)
        return out

    def owner_of(self, pid: str):
        for t in self.teams:
            if pid in t.player_ids:
                return t
        return None

    def name(self, pid: str) -> str:
        p = self.players.get(pid) or {}
        return p.get("full_name") or p.get("last_name") or str(pid)

    def describe(self, pid: str) -> str:
        p = self.players.get(pid) or {}
        return f"{self.name(pid)} ({p.get('position')}-{p.get('team')})"

    def roster_of(self, team: Team, position: str | None = None) -> list:
        out = [pid for pid in team.player_ids if self.players.get(pid)]
        if position:
            out = [pid for pid in out if self.players[pid].get("position") == position]
        return sorted(out, key=lambda pid: -player_value(self.players[pid]))


def load(league_id: str = config.LEAGUE_ID) -> League:
    raw_rosters = sleeper.rosters(league_id)
    raw_users = {u["user_id"]: u for u in sleeper.users(league_id)}
    all_players = sleeper.players()
    settings = sleeper.league(league_id)

    teams = []
    for r in raw_rosters:
        u = raw_users.get(r.get("owner_id"), {})
        meta = u.get("metadata") or {}
        s = r.get("settings") or {}
        teams.append(
            Team(
                roster_id=r["roster_id"],
                owner_id=r.get("owner_id") or "",
                display_name=u.get("display_name", "?"),
                team_name=meta.get("team_name") or "",
                is_me=u.get("display_name") == config.MY_USERNAME,
                player_ids=list(r.get("players") or []),
                wins=s.get("wins", 0),
                losses=s.get("losses", 0),
                faab_left=100 - s.get("waiver_budget_used", 0),
            )
        )
    teams.sort(key=lambda t: t.roster_id)
    return League(teams=teams, players=all_players, settings=settings)


def depth_charts(players: dict) -> dict:
    """Build {(team, position): [player_id, ...]} ordered by depth chart slot.

    Sleeper populates `depth_chart_order` for most relevant players. Anyone
    missing an order is appended behind the charted players, ranked by value,
    so an unlisted rookie still shows up as a deep backup rather than vanishing.
    """
    charts: dict = {}
    for pid, p in players.items():
        if not is_available_body(p):
            continue
        key = (p["team"], p["position"])
        charts.setdefault(key, []).append(pid)

    for key, pids in charts.items():
        def sort_key(pid):
            p = players[pid]
            order = p.get("depth_chart_order")
            return (0, order) if order else (1, -player_value(p))
        pids.sort(key=sort_key)
    return charts


def players_ahead(pid: str, players: dict, charts: dict) -> list:
    """Everyone ahead of `pid` on his depth chart, as (player_id, distance).

    Distance is the real gap in depth-chart slots, not the position in this
    list. The team's WR1 is one slot ahead of the WR2 but nine ahead of the
    tenth man, and only the first of those is a handcuff.
    """
    p = players.get(pid)
    if not p or not is_available_body(p):
        return []
    chart = charts.get((p["team"], p["position"]), [])
    if pid not in chart:
        return []
    idx = chart.index(pid)
    return [(ahead_pid, idx - j) for j, ahead_pid in enumerate(chart[:idx])]


def credibility(p: dict) -> float:
    """How much inherited workload this player could actually convert.

    Being next in line is necessary but not sufficient -- the backup has to be
    good enough that the touches are worth having.
    """
    if not p:
        return 0.0
    rank = p.get("search_rank") or config.UNRANKED_RANK
    if rank >= config.UNRANKED_RANK:
        return 0.0
    excess = max(0.0, rank - config.CREDIBILITY_FLOOR_RANK)
    return math.exp(-excess / config.CREDIBILITY_DECAY)


def backups_of(pid: str, players: dict, charts: dict, limit: int = 3) -> list:
    """Everyone listed behind `pid`, nearest first."""
    p = players.get(pid)
    if not p or not is_available_body(p):
        return []
    chart = charts.get((p["team"], p["position"]), [])
    if pid not in chart:
        return []
    return chart[chart.index(pid) + 1 : chart.index(pid) + 1 + limit]
