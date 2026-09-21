"""Opportunity: what each player is actually being used for, and what that is worth.

Touchdowns are noise; targets, carries, shares of the team's work and the
team's own scoring chances are the signal. This module turns Sleeper's weekly
stat lines -- player rows and the TEAM_* rows they sit inside -- into per-game
opportunity for every skill player, and converts that into *expected future
points per game* with per-position coefficients.

The coefficients are fit to predict, not to explain: on the two previous
seasons, features averaged over the first part of a season are regressed on
league-scored points per game over the rest of it. That target discounts a
hot touchdown month by itself and rewards the things that carry forward. The
feature set per position is what a forward-stepwise screen on 2024 and 2025
kept out of sample, with first-part points per game as the floor every set
had to beat; where points per game is itself a feature, the fit decides how
much of it to trust.

Snap share comes from Sleeper when it has it; Sleeper posts snaps a day or
two after the game, so nflverse's snap_counts file fills the gap.
"""

from __future__ import annotations

import sys

from . import config, sleeper

NFLVERSE = "https://github.com/nflverse/nflverse-data/releases/download"


def league_points(line: dict, scoring: dict) -> float:
    """Score one stat line under the league's scoring settings."""
    return sum(w * line.get(stat, 0.0) for stat, w in scoring.items() if stat in line)


# ---------------------------------------------------------------------------
# features
# ---------------------------------------------------------------------------

def _f(line: dict, k: str) -> float:
    return float(line.get(k) or 0.0)


def _share(line: dict, team: dict, num: str, den: str):
    d = _f(team, den)
    return _f(line, num) / d if d else None


# name -> function(line, team_line, scoring) -> float | None (None = unknown this game)
DERIVED = {
    "pts": lambda l, t, s: league_points(l, s),
    "touches": lambda l, t, s: _f(l, "rec_tgt") + _f(l, "rush_att"),
    "tgt_share": lambda l, t, s: _share(l, t, "rec_tgt", "rec_tgt"),
    "air_share": lambda l, t, s: _share(l, t, "rec_air_yd", "pass_air_yd"),
    "rz_rush_share": lambda l, t, s: _share(l, t, "rush_rz_att", "rush_rz_att"),
    "team_rz_att": lambda l, t, s: _f(t, "rz_att") or None,
    "team_ypp": lambda l, t, s: _f(t, "off_yd_per_play") or None,
    "team_air_yd": lambda l, t, s: _f(t, "pass_air_yd") or None,
}

# The feature set per position, chosen by honest out-of-sample correlation
# with rest-of-season PPG (fit on 2024, test on 2025, and the reverse; mean of
# the two). Points per game alone is the floor a set had to beat:
#   QB  pts + rushing first downs + team red-zone attempts   0.463 vs 0.424
#   RB  pts + touches + share of team red-zone carries       0.847 vs 0.847 (tie)
#   WR  pts + targets + receiving first downs                0.780 vs 0.777
#   TE  target share + air-yards share + first downs         0.841 vs 0.804
# Wider sets (red-zone targets, yards, team air yards, team yards per play)
# scored lower out of sample. Order is the coefficient order.
FEATURES = {
    "QB": ("pts", "rush_fd", "team_rz_att"),
    "RB": ("pts", "touches", "rz_rush_share"),
    "WR": ("pts", "rec_tgt", "rec_fd"),
    "TE": ("tgt_share", "air_share", "rec_fd"),
}
SNAP = "snap_share"

# Train on features averaged over weeks 1..a, target = league PPG over weeks
# a+1..17, at three points in the season so the fit sees small samples too.
SPLITS = ((1, 6), (1, 9), (1, 12))
MIN_GAMES = 4


def features_for(line: dict, team: dict, pos: str, scoring: dict) -> dict:
    """Per-game feature values for one stat line; a missing share is None."""
    out = {"pts": league_points(line, scoring)}
    for name in FEATURES[pos]:
        out[name] = DERIVED[name](line, team, scoring) if name in DERIVED else _f(line, name)
    out[SNAP] = _snap_share(line)
    return out


def _snap_share(line: dict) -> float | None:
    off, tm = line.get("off_snp"), line.get("tm_off_snp")
    if off is None or not tm:
        return None
    return min(1.0, float(off) / float(tm))


# ---------------------------------------------------------------------------
# least squares, stdlib only
# ---------------------------------------------------------------------------

def _solve(a: list, b: list) -> list:
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


def _fit(rows: list, ncol: int, ridge: float = 1e-2) -> list:
    """Ridge-regularised least squares with an intercept in column 0."""
    xtx = [[0.0] * ncol for _ in range(ncol)]
    xty = [0.0] * ncol
    for x, y in rows:
        for i in range(ncol):
            xty[i] += x[i] * y
            for j in range(ncol):
                xtx[i][j] += x[i] * x[j]
    for i in range(1, ncol):
        xtx[i][i] += ridge * max(1.0, xtx[i][i] / max(1, len(rows)))
    return _solve(xtx, xty)


def _pearson(xs: list, ys: list) -> float | None:
    n = len(xs)
    if n < 8:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sx = sum((x - mx) ** 2 for x in xs) ** 0.5
    sy = sum((y - my) ** 2 for y in ys) ** 0.5
    return sxy / (sx * sy) if sx and sy else None


# ---------------------------------------------------------------------------
# season windows
# ---------------------------------------------------------------------------

def _weekly(season: str, week: int, ttl: int) -> dict:
    try:
        return sleeper.get(f"stats/nfl/regular/{season}/{week}", f"stats_{season}_{week}", ttl) or {}
    except RuntimeError:
        return {}


def _window(lines_by_week: dict, weeks, players: dict, scoring: dict,
            snaps_fallback: dict | None = None) -> dict:
    """{pid: {"games", "feat": {name: mean}, "ppg"}} over the given weeks."""
    acc: dict = {}
    for wk in weeks:
        lines = lines_by_week.get(wk) or {}
        for pid, line in lines.items():
            p = players.get(pid) or {}
            pos = p.get("position")
            if pos not in FEATURES or not p.get("team") or not line.get("gp"):
                continue
            team = lines.get(f"TEAM_{p['team']}") or {}
            f = features_for(line, team, pos, scoring)
            if f[SNAP] is None and snaps_fallback:
                f[SNAP] = snaps_fallback.get((pid, wk))
            t = acc.setdefault(pid, {"games": 0, "pts": 0.0, "sum": {}, "n": {}})
            t["games"] += 1
            t["pts"] += f["pts"]
            for k, v in f.items():
                if v is None:
                    continue
                t["sum"][k] = t["sum"].get(k, 0.0) + v
                t["n"][k] = t["n"].get(k, 0) + 1
    out = {}
    for pid, t in acc.items():
        out[pid] = {
            "games": t["games"],
            "ppg": t["pts"] / t["games"],
            "feat": {k: t["sum"][k] / t["n"][k] for k in t["sum"]},
        }
    return out


def _x(feat: dict, pos: str, with_snap: bool) -> list | None:
    x = [1.0]
    for name in FEATURES[pos]:
        v = feat.get(name)
        if v is None:
            return None
        x.append(v)
    if with_snap:
        if feat.get(SNAP) is None:
            return None
        x.append(feat[SNAP])
    return x


# ---------------------------------------------------------------------------
# the fit
# ---------------------------------------------------------------------------

def fit_coefficients(seasons: list, scoring: dict, players: dict, force: bool = False) -> dict:
    """{pos: {"with_snaps": [...] | None, "no_snaps": [...], "n": rows,
    "oos_r": {pos: r}}} fit to predict rest-of-season PPG.

    Trained on every listed season. `oos_r` is the out-of-sample check: fit on
    all but the last season, test on the last, so the number is honest.
    """
    key = "usage_fit_" + "_".join(seasons)
    hit = None if force else sleeper._read_cache(key, config.CACHE_TTL["usage_fit"])
    if hit:
        return hit
    rows_by_season: dict = {}
    for season in seasons:
        by_week = {wk: _weekly(season, wk, config.CACHE_TTL["stats_prior_season"]) for wk in range(1, 18)}
        rows = {pos: {"with": [], "without": [], "ppg": []} for pos in FEATURES}
        for a, b in SPLITS:
            train = _window(by_week, range(a, b + 1), players, scoring)
            test = _window(by_week, range(b + 1, 18), players, scoring)
            for pid, tr in train.items():
                te = test.get(pid)
                if not te or tr["games"] < MIN_GAMES or te["games"] < MIN_GAMES:
                    continue
                pos = players[pid]["position"]
                x0 = _x(tr["feat"], pos, False)
                if x0:
                    rows[pos]["without"].append((x0, te["ppg"]))
                    rows[pos]["ppg"].append((tr["ppg"], te["ppg"]))
                x1 = _x(tr["feat"], pos, True)
                if x1:
                    rows[pos]["with"].append((x1, te["ppg"]))
        rows_by_season[season] = rows

    def fit_on(season_list):
        out = {}
        for pos in FEATURES:
            k = len(FEATURES[pos]) + 1
            wo = [r for s in season_list for r in rows_by_season[s][pos]["without"]]
            wi = [r for s in season_list for r in rows_by_season[s][pos]["with"]]
            out[pos] = {
                "no_snaps": _fit(wo, k) if len(wo) > 2 * k else None,
                "with_snaps": _fit(wi, k + 1) if len(wi) > 4 * (k + 1) else None,
                "n": len(wo),
            }
        return out

    result = fit_on(seasons)
    # Honest check: fit on the earlier season(s), score on the latest, against
    # the floor every feature set had to beat -- first-part PPG alone.
    oos = {}
    if len(seasons) > 1:
        held = seasons[-1]
        trial = fit_on(seasons[:-1])
        for pos in FEATURES:
            coef = trial[pos]["no_snaps"]
            test = rows_by_season[held][pos]["without"]
            if coef and test:
                pred = [sum(c * xi for c, xi in zip(coef, x)) for x, _ in test]
                oos[pos] = _pearson(pred, [y for _, y in test])
            base = rows_by_season[held][pos]["ppg"]
            if base:
                oos[pos + "_ppg_only"] = _pearson([a for a, _ in base], [b for _, b in base])
    for pos in FEATURES:
        result[pos]["oos_r"] = oos.get(pos)
        result[pos]["oos_r_ppg_only"] = oos.get(pos + "_ppg_only")
    result["_seasons"] = seasons
    sleeper._write_cache(key, result)
    return result


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
                fit_seasons: list | None = None, prior_season_ttl: bool = False) -> dict:
    """Per player: games, per-game opportunity, snap share, actual ppg,
    usage-predicted ppg, xppg.

    Uses weeks 1..through_week only, so the backtest can build the table as it
    stood before any later week.
    """
    fit_seasons = fit_seasons or [str(int(season) - 2), str(int(season) - 1)]
    coefs = coefs or fit_coefficients(fit_seasons, scoring, players)
    ttl = config.CACHE_TTL["stats_prior_season"] if prior_season_ttl else None
    by_week = {}
    for wk in range(1, through_week + 1):
        if ttl:
            by_week[wk] = _weekly(season, wk, ttl)
        else:
            try:
                by_week[wk] = sleeper.weekly_stats(season, wk, final=wk < current_week) or {}
            except RuntimeError:
                by_week[wk] = {}
    need_snaps = any(
        line.get("gp") and _snap_share(line) is None
        for wk, lines in by_week.items() if wk < current_week
        for pid, line in lines.items() if not pid.startswith("TEAM_")
    )
    fallback = nflverse_snaps(season) if need_snaps and not prior_season_ttl else None
    win = _window(by_week, range(1, through_week + 1), players, scoring, fallback)
    out = {}
    for pid, t in win.items():
        if t["games"] < config.USAGE_MIN_GAMES:
            continue
        pos = players[pid]["position"]
        c = coefs[pos]
        x = _x(t["feat"], pos, True) if c.get("with_snaps") else None
        if x:
            pred = sum(a * b for a, b in zip(c["with_snaps"], x))
        else:
            x = _x(t["feat"], pos, False)
            pred = sum(a * b for a, b in zip(c["no_snaps"], x)) if (x and c.get("no_snaps")) else t["ppg"]
        pred = max(0.0, pred)
        out[pid] = {
            "games": t["games"],
            "actual_ppg": round(t["ppg"], 2),
            "usage_ppg": round(pred, 2),
            "xppg": round(alpha * pred + (1.0 - alpha) * t["ppg"], 2),
            "snap_share": round(t["feat"][SNAP], 3) if t["feat"].get(SNAP) is not None else None,
            "per_game": {k: round(v, 2) for k, v in t["feat"].items() if k not in ("pts", SNAP)},
        }
    return out
