"""Domain model: players, values, depth charts and league rosters."""

from __future__ import annotations

import bisect
import math
import statistics
import sys
from dataclasses import dataclass, field

from . import config, schedule, sleeper, sources, usage


def _rank_curve(rank: float) -> float:
    """Map a 1-based rank onto the 0-100 value scale."""
    return config.VALUE_SCALE * math.exp(-rank / config.VALUE_DECAY)


# ---------------------------------------------------------------------------
# Value: rest-of-season expected points over replacement.
#
# For every skill player:
#   1. what he is used for (usage.py)            -> expected points per game
#   2. the depth chart and injuries              -> who plays which weeks, who
#                                                   inherits what
#   3. his remaining opponents (schedule.py)     -> per-week multiplier,
#                                                   playoff weeks weighted up
#   4. the outside prior (sources.py)            -> shrinkage while the sample
#                                                   is small
#   5. replacement level per position            -> points over replacement,
#                                                   ranked, onto the curve
# Built once per process for the live view; the backtest rebuilds it as of
# any past week.
# ---------------------------------------------------------------------------

_TABLE: dict | None = None
_TABLE_KEY: tuple | None = None
_META: dict = {}


def _played_weeks(season: str, current_week: int) -> int:
    """The last week with any game logged -- Sleeper's `week` rolls on Tuesday,
    so on a Monday the current week is played but still 'current'."""
    last = 0
    for wk in range(1, current_week + 1):
        try:
            lines = sleeper.weekly_stats(season, wk, final=wk < current_week) or {}
        except RuntimeError:
            break
        if any(v.get("gp") for v in lines.values()):
            last = wk
    return last


def _plays(p: dict, weeks: list, first_week: int, absences: dict | None) -> tuple[list, bool]:
    """Fraction of each remaining week this player is expected to play, and
    whether that rests on a default rather than a blurb."""
    status = p.get("injury_status")
    pid = p.get("player_id")
    unverified = False
    out = []
    back = (absences or {}).get(pid)
    if back is not None:
        # A blurb said when he is back; that beats any tag, including a bare
        # "Out" that would otherwise be read as one week.
        out = [1.0 if w >= back else 0.0 for w in weeks]
    elif status in config.RESERVE_STATUSES or status == "DNR":
        back = first_week + config.DEFAULT_ABSENCE_WEEKS.get(status, 4)
        unverified = True
        out = [1.0 if w >= back else 0.0 for w in weeks]
    elif status in ("Out", "Doubtful", "Questionable"):
        miss = vacancy(p)
        out = [(1.0 - miss) if w == first_week else 1.0 for w in weeks]
        unverified = status != "Questionable"
    else:
        out = [1.0] * len(weeks)
    return out, unverified


def _charts_by_usage(players: dict, xppg: dict) -> dict:
    """Depth charts ordered by Sleeper's slot, then by usage -- built here so
    the value table never has to call player_value() to order itself."""
    charts: dict = {}
    for pid, p in players.items():
        if not is_available_body(p):
            continue
        charts.setdefault((p["team"], p["position"]), []).append(pid)
    for pids in charts.values():
        pids.sort(key=lambda pid: (
            (0, players[pid]["depth_chart_order"]) if players[pid].get("depth_chart_order")
            else (1, -xppg.get(pid, 0.0), players[pid].get("search_rank") or config.UNRANKED_RANK)
        ))
    return charts


def value_table(through_week: int | None = None, *, alpha: float | None = None,
                prior_games: float | None = None, schedule_k: float | None = None,
                use_prior: bool = True, use_schedule: bool = True, use_depth: bool = True,
                prior_only: str | None = None, absences: dict | None = None,
                force: bool = False) -> dict:
    """{player_id: row} for every available skill player.

    Row keys: value, rank, ros (weighted ROS points), ros_healthy, prior,
    ours, inherited, games, xppg, usage_ppg, actual_ppg, snap_share, sched,
    weeks, vorp, unverified.

    `through_week` builds the table as it stood after that week (the backtest);
    the keyword arguments switch parts off or retune constants for the same
    purpose. The live view (no arguments) is cached per process.
    """
    global _TABLE, _TABLE_KEY
    alpha = config.USAGE_ALPHA if alpha is None else alpha
    prior_games = config.PRIOR_GAMES if prior_games is None else prior_games
    schedule_k = config.SCHEDULE_K if schedule_k is None else schedule_k
    key = (through_week, alpha, prior_games, schedule_k, use_prior, use_schedule, use_depth,
           prior_only, tuple(sorted((absences or {}).items())))
    if _TABLE is not None and _TABLE_KEY == key and not force:
        return _TABLE

    state = sleeper.nfl_state()
    season = str(state.get("season") or config.SEASON)
    current = int(state.get("week") or 0)
    if state.get("season_type") != "regular":
        current = 0
    settings = sleeper.league()
    scoring = settings.get("scoring_settings") or {}
    players = sleeper.players()
    skill = {pid: p for pid, p in players.items() if is_available_body(p)}

    played = _played_weeks(season, current) if through_week is None else min(through_week, current)
    first_week = played + 1
    sched = schedule.Schedule(season, settings, first_week,
                              through_week=played if through_week is not None else None,
                              k=schedule_k, enabled=use_schedule)
    weeks = sched.weeks
    weight = sched.weight

    # 1. usage
    fit_seasons = [str(int(season) - 2), str(int(season) - 1)]
    coefs = usage.fit_coefficients(fit_seasons, scoring, players)
    use = usage.usage_table(season, played, current, scoring, players, alpha=alpha, coefs=coefs) if played else {}
    # last season's usage, for what an unplayed player looks like when healthy
    last = usage.usage_table(fit_seasons[-1], 17, 99, scoring, players, alpha=alpha,
                             coefs=coefs, prior_season_ttl=True)
    xppg = {pid: r["xppg"] for pid, r in use.items()}
    # Game logs for floor / ceiling: last season plus this one, through the
    # weeks on record. Reported, never priced.
    logs = usage.weekly_points([fit_seasons[-1], season], scoring, players, played, current)

    # 4. prior (fetched before depth so a no-game player's ppg can seed inheritance)
    prior = {}
    if use_prior:
        try:
            prior = sources.prior(season, weeks, weight, scoring, players, only=prior_only)
        except Exception as err:  # noqa: BLE001
            print(f"warning: prior unavailable ({err}); values are usage-only", file=sys.stderr)
            prior = {}

    def ppg_guess(pid: str) -> float:
        """Best per-game guess for a player, for inheritance seeding."""
        if pid in xppg:
            return xppg[pid]
        wg = sched.weighted_games(skill[pid]["team"]) or 1.0
        if pid in prior:
            return prior[pid]["ros"] / wg
        return last.get(pid, {}).get("xppg", 0.0) * 0.5

    # 2. who plays when, and who inherits
    plays, unverified = {}, {}
    for pid, p in skill.items():
        plays[pid], unverified[pid] = _plays(p, weeks, first_week, absences)
    inherited = {pid: [0.0] * len(weeks) for pid in skill}
    if use_depth:
        charts = _charts_by_usage(players, xppg)
        pos_factor = config.INHERITANCE_BY_POSITION
        for pid, p in skill.items():
            for ahead, dist in players_ahead(pid, players, charts):
                share = config.INHERITANCE.get(dist, 0.0)
                if not share or ahead not in plays:
                    continue
                gain = ppg_guess(ahead) * share * pos_factor.get(p["position"], 0.5)
                if gain <= 0:
                    continue
                for i, w in enumerate(weeks):
                    missing = 1.0 - plays[ahead][i]
                    if missing > 0:
                        inherited[pid][i] += gain * missing

    # 3. + 4. project, shrink
    rows: dict = {}
    for pid, p in skill.items():
        team, pos = p["team"], p["position"]
        base = xppg.get(pid, 0.0)
        games = use.get(pid, {}).get("games", 0)
        ours = ours_healthy = inh = 0.0
        sched_sum = sched_n = 0.0
        for i, w in enumerate(weeks):
            m = sched.mult(team, pos, w)
            if m <= 0:
                continue
            wt = weight(w)
            sched_sum += m * wt
            sched_n += wt
            ours += base * m * wt * plays[pid][i]
            ours_healthy += base * m * wt
            inh += inherited[pid][i] * m * wt * plays[pid][i]
        pr = prior.get(pid, {}).get("ros") if use_prior else None
        if pr is not None:
            ros = (games * ours + prior_games * pr) / (games + prior_games) + inh
            if games:
                ros_healthy = (games * ours_healthy + prior_games * pr) / (games + prior_games) + inh
            else:
                # Nothing this season to say what he is when healthy; last
                # season's usage is the least injury-adjusted evidence there is.
                healthy_base = last.get(pid, {}).get("xppg", 0.0) * sched_sum
                ros_healthy = max(pr, healthy_base) + inh
        else:
            ros = ours + inh
            ros_healthy = (ours_healthy if games else last.get(pid, {}).get("xppg", 0.0) * sched_sum) + inh
        rows[pid] = {
            "ros": ros, "ros_healthy": ros_healthy, "prior": pr, "ours": ours, "inherited": inh,
            "games": games, "xppg": base,
            "usage_ppg": use.get(pid, {}).get("usage_ppg"), "actual_ppg": use.get(pid, {}).get("actual_ppg"),
            "snap_share": use.get(pid, {}).get("snap_share"),
            "sched": round(sched_sum / sched_n, 3) if sched_n else None,
            "weeks": round(sum(plays[pid][i] * weight(w) for i, w in enumerate(weeks)
                               if sched.mult(team, pos, w) > 0), 1),
            "unverified": unverified[pid] and (p.get("injury_status") in config.RESERVE_STATUSES
                                                or p.get("injury_status") in ("Out", "Doubtful")),
        }
        log = logs.get(pid) or []
        rows[pid]["games_logged"] = len(log)
        if len(log) >= config.CONSISTENCY_MIN_GAMES:
            q = statistics.quantiles(log, n=4)
            rows[pid]["floor"] = round(q[0], 1)
            rows[pid]["ceiling"] = round(q[2], 1)
            rows[pid]["bust_rate"] = round(sum(1 for v in log if v < config.BUST_POINTS) / len(log), 2)
        else:
            rows[pid]["floor"] = rows[pid]["ceiling"] = rows[pid]["bust_rate"] = None

    # 5. replacement level, points over replacement, rank, curve
    repl = {}
    for pos in config.SKILL_POSITIONS:
        vals = sorted((r["ros"] for pid, r in rows.items() if skill[pid]["position"] == pos), reverse=True)
        n = config.REPLACEMENT_RANK.get(pos, 24)
        repl[pos] = vals[n - 1] if len(vals) >= n else (vals[-1] if vals else 0.0)
    for pid, r in rows.items():
        pos = skill[pid]["position"]
        r["vorp"] = r["ros"] - repl[pos]
        r["vorp_healthy"] = r["ros_healthy"] - repl[pos]
    order = sorted(rows, key=lambda pid: (-rows[pid]["vorp"], -rows[pid]["ros"]))
    vorps_desc = [rows[pid]["vorp"] for pid in order]
    neg_sorted = [-v for v in vorps_desc]           # ascending, for bisect
    for i, pid in enumerate(order, start=1):
        rows[pid]["rank"] = i
        rows[pid]["value"] = _rank_curve(i) * config.POSITION_MULTIPLIER.get(skill[pid]["position"], 1.0)
        hr = bisect.bisect_left(neg_sorted, -rows[pid]["vorp_healthy"]) + 1
        rows[pid]["healthy_rank"] = hr
        rows[pid]["healthy_value"] = _rank_curve(hr) * config.POSITION_MULTIPLIER.get(skill[pid]["position"], 1.0)

    _META.update(
        season=season, played_weeks=played, first_week=first_week, horizon=sched.describe(),
        schedule_available=sched.available, alpha=alpha, prior_games=prior_games,
        sources=sources.meta() if use_prior else {"used": [], "failed": ["prior disabled"], "static": [], "weights": {}},
        players=len(rows), with_usage=len(use), unverified=sum(1 for r in rows.values() if r["unverified"]),
        replacement={k: round(v, 1) for k, v in repl.items()},
        usage_fit={pos: {"oos_r": coefs[pos].get("oos_r"), "ppg_only": coefs[pos].get("oos_r_ppg_only"),
                         "n": coefs[pos].get("n")} for pos in config.SKILL_POSITIONS},
        usage_fit_seasons=fit_seasons,
    )
    if through_week is None and not force and key[1:] == (config.USAGE_ALPHA, config.PRIOR_GAMES,
                                                            config.SCHEDULE_K, True, True, True, None, ()):
        _TABLE, _TABLE_KEY = rows, key
    elif _TABLE is None:
        _TABLE, _TABLE_KEY = rows, key
    return rows


def value_meta() -> dict:
    value_table()
    return dict(_META)


def player_value(p: dict) -> float:
    """Standalone fantasy value on a 0-100ish scale: rest-of-season expected
    points over replacement, ranked across all skill players, on the curve."""
    if not p:
        return 0.0
    row = value_table().get(p.get("player_id") or "")
    return row["value"] if row else 0.0


def healthy_value(p: dict) -> float:
    """What a player is worth when he is playing, ignoring a current absence.

    The stash question is what he is worth once he is back, not what he is
    worth while he sits. Same pipeline with the absence removed; a player with
    no games this season is projected from last season's usage.
    """
    if not p:
        return 0.0
    row = value_table().get(p.get("player_id") or "")
    return row["healthy_value"] if row else 0.0


def effective_rank(p: dict) -> float:
    """The overall rank behind a player's value -- what credibility reads."""
    if not p:
        return float(config.UNRANKED_RANK)
    row = value_table().get(p.get("player_id") or "")
    return float(row["rank"]) if row else float(config.UNRANKED_RANK)


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
