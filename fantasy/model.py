"""Domain model: players, values, depth charts and league rosters."""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field

from . import config, sleeper, sources


def _rank_curve(rank: float) -> float:
    """Map a 1-based rank onto the 0-100 value scale."""
    return config.VALUE_SCALE * math.exp(-rank / config.VALUE_DECAY)


def consensus_rank(p: dict) -> float:
    """The prior rank: the broad-sourced consensus (fantasy/sources.py), falling
    back to Sleeper's `search_rank` only for a player no source lists."""
    row = sources.consensus_ranks().get(p.get("player_id") or "")
    if row:
        return float(row["rank"])
    return float(p.get("search_rank") or config.UNRANKED_RANK)


def consensus_value(p: dict) -> float:
    """The prior pushed through the decay curve."""
    rank = consensus_rank(p)
    if rank >= config.UNRANKED_RANK:
        return 0.0
    return _rank_curve(rank)


# ---------------------------------------------------------------------------
# Production: what players have actually scored this season under this
# league's scoring. Built lazily once per process, from Sleeper's weekly stat
# lines, and ranked across all skill positions so it lives on the same scale as
# the consensus rank it is blended with.
# ---------------------------------------------------------------------------

_PRODUCTION: dict | None = None
_PRODUCTION_META: dict = {"weeks": 0, "players": 0, "error": None}


def _league_points(line: dict, scoring: dict) -> float:
    return sum(w * line.get(stat, 0.0) for stat, w in scoring.items() if stat in line)


def production_table(force: bool = False) -> dict:
    """{player_id: {"pts", "games", "ppg", "rank", "value"}} for every skill
    player with at least one game logged this season.

    Failure is loud, not silent: if the stats cannot be fetched the table is
    empty, values fall back to pure consensus, and a warning goes to stderr,
    because a quiet fallback would look exactly like "nobody has played yet".
    """
    global _PRODUCTION
    if _PRODUCTION is not None and not force:
        return _PRODUCTION
    table: dict = {}
    try:
        state = sleeper.nfl_state()
        season = str(state.get("season") or config.SEASON)
        current = int(state.get("week") or 0)
        if state.get("season_type") != "regular":
            current = 0
        scoring = (sleeper.league().get("scoring_settings") or {})
        players = sleeper.players()
        totals: dict = {}
        weeks = 0
        for week in range(1, current + 1):
            lines = sleeper.weekly_stats(season, week, final=week < current) or {}
            weeks += 1
            for pid, line in lines.items():
                p = players.get(pid)
                if not p or not is_available_body(p) or not line.get("gp"):
                    continue
                t = totals.setdefault(pid, {"pts": 0.0, "games": 0})
                t["pts"] += _league_points(line, scoring)
                t["games"] += 1
        ranked = sorted(
            ((pid, t) for pid, t in totals.items() if t["games"] >= config.PRODUCTION_MIN_GAMES),
            key=lambda kv: -(kv[1]["pts"] / kv[1]["games"]),
        )
        for i, (pid, t) in enumerate(ranked, start=1):
            ppg = t["pts"] / t["games"]
            table[pid] = {
                "pts": round(t["pts"], 2), "games": t["games"], "ppg": round(ppg, 2),
                "rank": i, "value": _rank_curve(i),
            }
        _PRODUCTION_META.update(weeks=weeks, players=len(table), error=None)
    except Exception as err:  # noqa: BLE001 -- any failure must degrade loudly
        _PRODUCTION_META.update(weeks=0, players=0, error=str(err))
        print(f"warning: production data unavailable, values are consensus-only ({err})",
              file=sys.stderr)
        table = {}
    _PRODUCTION = table
    return table


def production_meta() -> dict:
    production_table()
    return dict(_PRODUCTION_META)


def production_weight(games: int) -> float:
    """How far actual production overrides the consensus prior."""
    if games <= 0:
        return 0.0
    return games / (games + config.PRODUCTION_PRIOR_GAMES)


def player_value(p: dict) -> float:
    """Approximate standalone fantasy value on a 0-100ish scale.

    The consensus rank (expert consensus and season projections from several
    sources, decayed exponentially so the curve matches how fantasy value
    actually behaves) is the prior. Once a
    player has games on record it is blended toward his season-to-date
    production rank under this league's scoring, and the superflex quarterback
    premium is applied to the result.
    """
    if not p:
        return 0.0
    prior = consensus_value(p)
    prod = production_table().get(p.get("player_id") or "")
    if prod:
        w = production_weight(prod["games"])
        base = (1.0 - w) * prior + w * prod["value"]
    else:
        base = prior
    if base <= 0.0:
        return 0.0
    return base * config.POSITION_MULTIPLIER.get(p.get("position"), 1.0)


def healthy_value(p: dict) -> float:
    """What a player is worth when he is playing, ignoring a current absence.

    The consensus sources price a reserve-list stint into the rank -- a back
    who will miss six weeks drops 150 spots in every rest-of-season list --
    which is right for standalone value and wrong for a stash decision, where
    the question is what he is worth once he is back. The best single rank any
    source gives him (Sleeper's search rank included) is the least
    injury-adjusted view we have.
    """
    if not p:
        return 0.0
    ranks = [float(p.get("search_rank") or config.UNRANKED_RANK)]
    row = sources.consensus_ranks().get(p.get("player_id") or "")
    if row:
        ranks += [float(r) for r in row["sources"].values()]
    best = min(ranks)
    if best >= config.UNRANKED_RANK:
        return 0.0
    return _rank_curve(best) * config.POSITION_MULTIPLIER.get(p.get("position"), 1.0)


def effective_rank(p: dict) -> float:
    """The rank a player's blended value corresponds to on the consensus curve.

    This is what "consensus rank" means once production is folded in, and it is
    what the credibility check should read -- a rank-209 receiver who has just
    posted a WR1 week is no longer a rank-209 receiver.
    """
    if not p:
        return float(config.UNRANKED_RANK)
    base = player_value(p) / config.POSITION_MULTIPLIER.get(p.get("position"), 1.0)
    if base <= 0.0:
        return float(config.UNRANKED_RANK)
    return -config.VALUE_DECAY * math.log(base / config.VALUE_SCALE)


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
    rank = effective_rank(p)
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
