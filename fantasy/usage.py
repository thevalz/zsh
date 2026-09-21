"""Opportunity: what each player is actually being used for, and what that is worth.

Touchdowns are noise; targets, carries, snaps and red-zone looks are the
signal. This module turns Sleeper's weekly stat lines into per-game
opportunity for every skill player, and converts opportunity into expected
points with per-position coefficients fit on last season under this league's
own scoring settings. `xppg` blends that usage-implied number with actual
points per game (config.USAGE_ALPHA).

Snap share comes from Sleeper when it has it; Sleeper posts snaps a day or two
after the game, so nflverse's snap_counts file fills the gap for completed
weeks.
"""

from __future__ import annotations

import csv
import io
import os
import sys

from . import config, sleeper

# Opportunity features per position group. Order matters: it is the column
# order of the fitted coefficients.
FEATURES = {
    "QB": ("pass_att", "pass_rz_att", "rush_att", "rush_rz_att"),
    "RB": ("rush_att", "rush_rz_att", "rec_tgt", "rec_rz_tgt"),
    "WR": ("rec_tgt", "rec_rz_tgt", "rush_att"),
    "TE": ("rec_tgt", "rec_rz_tgt"),
}
SNAP = "snap_share"

NFLVERSE = "https://github.com/nflverse/nflverse-data/releases/download"


def league_points(line: dict, scoring: dict) -> float:
    """Score one stat line under the league's scoring settings."""
    return sum(w * line.get(stat, 0.0) for stat, w in scoring.items() if stat in line)


# ---------------------------------------------------------------------------
# least squares, stdlib only
# ---------------------------------------------------------------------------

def _solve(a: list, b: list) -> list:
    """Gaussian elimination with partial pivoting; a is n x n, b length n."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[piv] = m[piv], m[c]
        if abs(m[c][c]) < 1e-12:
            continue
        for r in range(n):
            if r == c:
                continue
            f = m[r][c] / m[c][c]
            if f:
                for k in range(c, n + 1):
                    m[r][k] -= f * m[c][k]
    return [m[i][n] / m[i][i] if abs(m[i][i]) > 1e-12 else 0.0 for i in range(n)]


def _fit(rows: list, ncol: int, ridge: float = 1e-3) -> list:
    """Ordinary least squares with an intercept in column 0."""
    xtx = [[0.0] * ncol for _ in range(ncol)]
    xty = [0.0] * ncol
    for x, y in rows:
        for i in range(ncol):
            xty[i] += x[i] * y
            for j in range(ncol):
                xtx[i][j] += x[i] * x[j]
    for i in range(1, ncol):
        xtx[i][i] += ridge
    return _solve(xtx, xty)


def _features(line: dict, pos: str, snap: float | None) -> list:
    x = [1.0] + [float(line.get(f) or 0.0) for f in FEATURES[pos]]
    if snap is not None:
        x.append(snap)
    return x


def fit_coefficients(prior_season: str, scoring: dict, players: dict, force: bool = False) -> dict:
    """{pos: {"with_snaps": [...], "no_snaps": [...], "n": rows}} fit on prior_season.

    Two fits per position -- with and without snap share -- so a player whose
    snaps are not in yet is still projected from his touches.
    """
    key = f"usage_fit_{prior_season}"
    hit = None if force else sleeper._read_cache(key, config.CACHE_TTL["usage_fit"])
    if hit:
        return hit
    rows = {pos: {"with": [], "without": []} for pos in FEATURES}
    for week in range(1, 19):
        try:
            lines = sleeper.get(f"stats/nfl/regular/{prior_season}/{week}",
                                f"stats_{prior_season}_{week}",
                                config.CACHE_TTL["stats_prior_season"]) or {}
        except RuntimeError:
            continue
        for pid, line in lines.items():
            p = players.get(pid) or {}
            pos = p.get("position")
            if pos not in FEATURES or not line.get("gp"):
                continue
            y = league_points(line, scoring)
            rows[pos]["without"].append((_features(line, pos, None), y))
            snap = _snap_share(line)
            if snap is not None:
                rows[pos]["with"].append((_features(line, pos, snap), y))
    out = {}
    for pos in FEATURES:
        k = len(FEATURES[pos]) + 1
        out[pos] = {
            "no_snaps": _fit(rows[pos]["without"], k) if rows[pos]["without"] else [0.0] * k,
            "with_snaps": _fit(rows[pos]["with"], k + 1) if len(rows[pos]["with"]) > 50 else None,
            "n": len(rows[pos]["without"]),
        }
    sleeper._write_cache(key, out)
    return out


def _snap_share(line: dict) -> float | None:
    off, tm = line.get("off_snp"), line.get("tm_off_snp")
    if off is None or not tm:
        return None
    return min(1.0, float(off) / float(tm))


# ---------------------------------------------------------------------------
# nflverse snap counts (fallback while Sleeper's are pending)
# ---------------------------------------------------------------------------

def nflverse_snaps(season: str) -> dict:
    """{(sleeper_id, week): offense snap share} from nflverse, or {} if unavailable."""
    try:
        import matchups  # repo-root script; importable when run from the repo
    except ImportError:
        return {}
    try:
        roster = matchups.read_csv(matchups.fetch_text(
            f"{NFLVERSE}/rosters/roster_{season}.csv", ttl=config.CACHE_TTL["nflverse"]))
        pfr_to_sid = {r["pfr_id"]: r["sleeper_id"] for r in roster if r.get("pfr_id") and r.get("sleeper_id")}
        text = matchups.fetch_text_optional(f"{NFLVERSE}/snap_counts/snap_counts_{season}.csv")
        if not text:
            return {}
        out = {}
        for r in matchups.read_csv(text):
            if r.get("game_type") != "REG":
                continue
            sid = pfr_to_sid.get(r.get("pfr_player_id") or "")
            if sid:
                out[(sid, int(r["week"]))] = float(r.get("offense_pct") or 0.0)
        return out
    except Exception as err:  # noqa: BLE001 -- a fallback that fails is just absent
        print(f"warning: nflverse snap counts unavailable ({err})", file=sys.stderr)
        return {}


# ---------------------------------------------------------------------------
# the table
# ---------------------------------------------------------------------------

def usage_table(season: str, through_week: int, current_week: int, scoring: dict,
                players: dict, alpha: float = config.USAGE_ALPHA, coefs: dict | None = None,
                prior_season: str | None = None) -> dict:
    """Per player: games, per-game opportunity, snap share, actual ppg, usage ppg, xppg.

    Uses weeks 1..through_week only, so the backtest can build the table as it
    stood before any later week.
    """
    prior_season = prior_season or str(int(season) - 1)
    coefs = coefs or fit_coefficients(prior_season, scoring, players)
    snaps_fallback = None
    acc: dict = {}
    for week in range(1, through_week + 1):
        lines = sleeper.weekly_stats(season, week, final=week < current_week) or {}
        for pid, line in lines.items():
            p = players.get(pid) or {}
            pos = p.get("position")
            if pos not in FEATURES or not p.get("team") or not line.get("gp"):
                continue
            t = acc.setdefault(pid, {"games": 0, "pts": 0.0, "feat": [0.0] * len(FEATURES[pos]),
                                     "snap_sum": 0.0, "snap_n": 0})
            t["games"] += 1
            t["pts"] += league_points(line, scoring)
            for i, f in enumerate(FEATURES[pos]):
                t["feat"][i] += float(line.get(f) or 0.0)
            snap = _snap_share(line)
            if snap is None and week < current_week:
                if snaps_fallback is None:
                    snaps_fallback = nflverse_snaps(season)
                snap = snaps_fallback.get((pid, week))
            if snap is not None:
                t["snap_sum"] += snap
                t["snap_n"] += 1
    out = {}
    for pid, t in acc.items():
        if t["games"] < config.USAGE_MIN_GAMES:
            continue
        pos = players[pid]["position"]
        g = t["games"]
        feat = [v / g for v in t["feat"]]
        snap = t["snap_sum"] / t["snap_n"] if t["snap_n"] else None
        c = coefs[pos]
        if snap is not None and c.get("with_snaps"):
            x = [1.0] + feat + [snap]
            usage_ppg = sum(a * b for a, b in zip(c["with_snaps"], x))
        else:
            x = [1.0] + feat
            usage_ppg = sum(a * b for a, b in zip(c["no_snaps"], x))
        usage_ppg = max(0.0, usage_ppg)
        actual_ppg = t["pts"] / g
        out[pid] = {
            "games": g,
            "actual_ppg": round(actual_ppg, 2),
            "usage_ppg": round(usage_ppg, 2),
            "xppg": round(alpha * usage_ppg + (1.0 - alpha) * actual_ppg, 2),
            "snap_share": round(snap, 3) if snap is not None else None,
            "per_game": {f: round(v, 2) for f, v in zip(FEATURES[pos], feat)},
        }
    return out
