"""League-wide roster analysis and trade partner matching.

A trade happens when two managers are short of different things. So rather than
ranking teams by strength, this scores every pair on complementary need: what I
have spare that they lack, and what they have spare that I lack.
"""

from __future__ import annotations

from . import config
from .model import League, Team, player_value


def replacement_level(lg: League) -> dict:
    """Value of the best player at each position who is *not* on any roster.

    This is the number that makes a bench player tradeable or not. Holding a
    fourth running back only counts as surplus if he is meaningfully better
    than what anyone could claim off waivers for a dollar.
    """
    rostered = lg.rostered
    out = {}
    for pos in config.SKILL_POSITIONS:
        pool = sorted(
            (
                player_value(p)
                for pid, p in lg.players.items()
                if pid not in rostered
                and p.get("position") == pos
                and p.get("team")
                and p.get("injury_status") not in ("IR", "PUP", "Sus", "DNR")
            ),
            reverse=True,
        )
        # Second-best rather than best: one outlier free agent should not move
        # the baseline for a whole position.
        out[pos] = round(pool[1], 2) if len(pool) > 1 else (round(pool[0], 2) if pool else 0.0)
    return out


def positional_profile(lg: League, team: Team, replacement: dict | None = None) -> dict:
    """Per-position starter quality and surplus for one roster.

    starter_value -- combined value of the players who actually start
    surplus       -- bench value *above replacement level*, i.e. what this team
                     could actually trade away and still be whole
    depth_after   -- value of the best player past the starting requirement
    """
    replacement = replacement if replacement is not None else replacement_level(lg)
    out = {}
    for pos in config.SKILL_POSITIONS:
        need = config.STARTERS[pos]
        ranked = [lg.players[pid] for pid in lg.roster_of(team, pos)]
        values = [player_value(p) for p in ranked]
        starters = values[:need]
        bench = values[need:]
        # A missing starter is a hole, not a zero -- pad so short rosters score badly.
        starters += [0.0] * (need - len(starters))
        repl = replacement.get(pos, 0.0)
        out[pos] = {
            "count": len(ranked),
            "starter_value": round(sum(starters), 2),
            "surplus": round(sum(max(0.0, v - repl) for v in bench), 2),
            "depth_after": round(bench[0], 2) if bench else 0.0,
            "players": ranked,
        }
    return out


def league_profiles(lg: League) -> dict:
    repl = replacement_level(lg)
    return {t.roster_id: positional_profile(lg, t, repl) for t in lg.teams}


def _league_baseline(profiles: dict) -> dict:
    """Median starter value per position -- the bar for 'normal'."""
    base = {}
    for pos in config.SKILL_POSITIONS:
        vals = sorted(p[pos]["starter_value"] for p in profiles.values())
        base[pos] = vals[len(vals) // 2]
    return base


def needs_and_surpluses(lg: League, profiles: dict | None = None) -> dict:
    """{roster_id: {pos: {'need': x, 'surplus': y}}} measured against the league."""
    profiles = profiles or league_profiles(lg)
    base = _league_baseline(profiles)
    out = {}
    for rid, prof in profiles.items():
        row = {}
        for pos in config.SKILL_POSITIONS:
            gap = base[pos] - prof[pos]["starter_value"]
            row[pos] = {
                "need": round(max(gap, 0.0), 2),
                "surplus": round(prof[pos]["surplus"], 2),
                "starter_value": prof[pos]["starter_value"],
                "count": prof[pos]["count"],
            }
        out[rid] = row
    return out


def _tradeable(lg: League, team: Team, pos: str, desperation: float = 0.0) -> list:
    """Players this team could move at `pos` without breaking its lineup.

    A team that is badly short somewhere will trade a starter to fix it, so
    the more desperate they are the deeper we reach into their lineup.
    """
    need = config.STARTERS[pos]
    if desperation >= 25:
        need = max(1, need - 1)
    return lg.roster_of(team, pos)[need:]


def _balanced(give_value: float, get_value: float, tolerance: float = 0.45) -> bool:
    hi = max(give_value, get_value)
    return hi > 0 and abs(give_value - get_value) / hi <= tolerance


def find_partners(lg: League, top: int = 5) -> list:
    """Rank the other 11 teams by two-way trade fit with me."""
    profiles = league_profiles(lg)
    ns = needs_and_surpluses(lg, profiles)
    me = lg.me
    my = ns[me.roster_id]

    results = []
    for team in lg.teams:
        if team.is_me:
            continue
        theirs = ns[team.roster_id]

        # What I can send: my surplus at a position they are short of.
        send_fit = {
            pos: min(my[pos]["surplus"], theirs[pos]["need"])
            for pos in config.SKILL_POSITIONS
        }
        # What I want back: their surplus at a position I am short of.
        recv_fit = {
            pos: min(theirs[pos]["surplus"], my[pos]["need"])
            for pos in config.SKILL_POSITIONS
        }

        # Never offer to ship out a position I am myself below median at, no
        # matter how badly the other team needs it.
        sendable = {p: v for p, v in send_fit.items() if my[p]["need"] <= 0}
        if not sendable or max(sendable.values()) <= 0:
            continue
        send_pos = max(sendable, key=sendable.get)
        recv_pos = max(recv_fit, key=recv_fit.get)
        score = sum(sendable.values()) + sum(recv_fit.values())

        results.append(
            {
                "team": team,
                "score": round(score, 2),
                "send_position": send_pos,
                "recv_position": recv_pos,
                "their_needs": {
                    p: theirs[p]["need"] for p in config.SKILL_POSITIONS if theirs[p]["need"] > 0
                },
                "their_surplus": {
                    p: theirs[p]["surplus"] for p in config.SKILL_POSITIONS if theirs[p]["surplus"] > 0
                },
                "offers": _build_offers(
                    lg, team, send_pos, recv_pos, profiles,
                    their_need=theirs[send_pos]["need"],
                ),
                "counts": {p: theirs[p]["count"] for p in config.SKILL_POSITIONS},
            }
        )

    results.sort(key=lambda r: -r["score"])
    return results[:top]


def _build_offers(lg: League, them: Team, send_pos: str, recv_pos: str, profiles: dict,
                  their_need: float = 0.0) -> list:
    """Concrete, roughly value-balanced suggestions -- 1-for-1 and 2-for-1."""
    mine = _tradeable(lg, lg.me, send_pos)
    theirs = _tradeable(lg, them, recv_pos, desperation=their_need)
    offers = []

    for give in mine[:4]:
        gv = player_value(lg.players[give])
        if gv <= 0:
            continue
        for get in theirs[:5]:
            rv = player_value(lg.players[get])
            if rv <= 0 or not _balanced(gv, rv):
                continue
            offers.append({
                "give": lg.describe(give), "get": lg.describe(get),
                "give_value": round(gv, 1), "get_value": round(rv, 1),
                "delta": round(rv - gv, 1), "shape": "1-for-1",
            })

    # Two of my spare parts for one player who actually starts for me. This is
    # how a deep-but-flat roster converts quantity into quality.
    for i in range(len(mine[:4])):
        for j in range(i + 1, len(mine[:4])):
            a, b = mine[i], mine[j]
            gv = player_value(lg.players[a]) + player_value(lg.players[b])
            if gv <= 0:
                continue
            for get in theirs[:5]:
                rv = player_value(lg.players[get])
                # Only worth a 2-for-1 if the return is better than either piece.
                if rv <= max(player_value(lg.players[a]), player_value(lg.players[b])):
                    continue
                if not _balanced(gv, rv, tolerance=0.35):
                    continue
                offers.append({
                    "give": f"{lg.describe(a)} + {lg.describe(b)}",
                    "get": lg.describe(get),
                    "give_value": round(gv, 1), "get_value": round(rv, 1),
                    "delta": round(rv - gv, 1), "shape": "2-for-1",
                })

    offers.sort(key=lambda o: (o["shape"] != "2-for-1", -o["delta"]))
    return offers[:5]


def my_summary(lg: League) -> dict:
    """Where I actually stand, position by position, against the league median."""
    profiles = league_profiles(lg)
    base = _league_baseline(profiles)
    mine = profiles[lg.me.roster_id]
    rows = {}
    for pos in config.SKILL_POSITIONS:
        sv = mine[pos]["starter_value"]
        ranks = sorted(
            (profiles[t.roster_id][pos]["starter_value"] for t in lg.teams), reverse=True
        )
        rows[pos] = {
            "starter_value": sv,
            "median": round(base[pos], 2),
            "rank": ranks.index(sv) + 1,
            "count": mine[pos]["count"],
            "surplus": mine[pos]["surplus"],
            "required": config.STARTERS[pos],
        }
    return rows
