#!/usr/bin/env python3
"""WR vs CB matchup checker for the Zebras Shooting Heroin Sleeper league.

Answers one question before you set your lineup: which of my wide receivers
are drawing a tough cornerback this week?

Data sources (all free, no API keys):
  * Sleeper API           -> your league, roster, current NFL week
  * nflverse schedules    -> who plays whom, home/away, bye weeks
  * nflverse depth charts -> each defense's starting LCB / RCB / nickel,
                             and each offense's WR1 / WR2 / WR3
  * nflverse PFR advstats -> per-defender coverage stats (targets,
                             completions, yards, TDs, INTs allowed)

How a CB gets graded
  Coverage stats from the current season, last season, and (at half
  weight) the season before are pooled, so the grade shifts toward this
  year's play as games accumulate and a CB who missed last year still
  gets credit for the year before.
  Every CB (and safety, since safeties play nickel) with enough targets is ranked on yards allowed per target,
  passer rating allowed, and completion % allowed; the average
  percentile becomes a 0-100 "toughness" score (100 = hardest to throw
  on), pulled toward 50 when the sample is small, and a letter grade
  A-F. Rookies and low-sample CBs get "?".

How a WR is matched to a CB
  Outside WRs (depth-chart WR1/WR2) line up against both boundary CBs
  over a game, so the matchup score is the average of the opponent's
  LCB1 and RCB1. Slot WRs (depth-chart WR3, or a name listed in
  wr_alignment_overrides.json) are matched to the nickel CB. If either
  boundary CB is an A-grade corner the WR is flagged as a shadow risk.

Usage
  python3 wr_cb_matchups.py                 # my WRs, current week
  python3 wr_cb_matchups.py --week 3        # a different week
  python3 wr_cb_matchups.py --all           # every team's WR1-WR3
  python3 wr_cb_matchups.py --player "Ladd McConkey"
  python3 wr_cb_matchups.py --cbs           # CB toughness leaderboard
  python3 wr_cb_matchups.py --full --markdown -o matchups/week01.md
  python3 wr_cb_matchups.py --refresh       # ignore the 12h cache

Only the Python standard library is required.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import gzip
import io
import json
import os
import re
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SLEEPER_USERNAME = "thevalz"
SLEEPER_LEAGUE_ID = "1390899489962741761"  # Zebras Shooting Heroin (2026)

NFLVERSE = "https://github.com/nflverse/nflverse-data/releases/download"
SLEEPER = "https://api.sleeper.app/v1"

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")
CACHE_TTL_SECONDS = 12 * 3600

MIN_TARGETS = 30          # targets needed before a CB gets a grade
TOUGH_SCORE = 68          # matchup score at/above this -> "TOUGH"
SOFT_SCORE = 38           # matchup score at/below this -> "SOFT"
NEUTRAL_SCORE = 50.0      # what an ungraded CB counts as
SHRINK_TARGETS = 20       # small-sample scores are pulled toward NEUTRAL_SCORE

OVERRIDES_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "wr_alignment_overrides.json"
)

SAFETY_POSITIONS = {"S", "FS", "SS"}
CB_POSITIONS = {"CB", "DB", "LCB", "RCB", "NB", "SCB", "CB/S", "DB/S"} | SAFETY_POSITIONS


# ---------------------------------------------------------------------------
# Fetching + caching
# ---------------------------------------------------------------------------

def _cache_path(url: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", url.split("://", 1)[-1])
    return os.path.join(CACHE_DIR, name[-150:])


def fetch_bytes(url: str, refresh: bool = False, ttl: int = CACHE_TTL_SECONDS) -> bytes:
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = _cache_path(url)
    if not refresh and os.path.exists(path) and time.time() - os.path.getmtime(path) < ttl:
        with open(path, "rb") as fh:
            return fh.read()
    req = urllib.request.Request(url, headers={"User-Agent": "wr-cb-matchups/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()
    with open(path, "wb") as fh:
        fh.write(data)
    return data


def fetch_text(url: str, refresh: bool = False, ttl: int = CACHE_TTL_SECONDS) -> str:
    data = fetch_bytes(url, refresh, ttl)
    if url.endswith(".gz"):
        data = gzip.decompress(data)
    return data.decode("utf-8")


def fetch_json(url: str, refresh: bool = False, ttl: int = CACHE_TTL_SECONDS):
    return json.loads(fetch_text(url, refresh, ttl))


def read_csv(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text)))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def norm_name(name: str) -> str:
    """Normalise 'Pat Surtain II' / 'Patrick Surtain' style variants."""
    n = name.lower()
    n = re.sub(r"[.'\-]", "", n)
    n = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", n)
    return " ".join(n.split())


def name_key(name: str) -> str:
    """Looser key: first 3 letters of first name + last name."""
    parts = norm_name(name).split()
    if not parts:
        return ""
    return parts[0][:3] + " " + parts[-1]


def fnum(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def passer_rating(cmp_: float, att: float, yds: float, td: float, ints: float) -> float | None:
    if att <= 0:
        return None
    a = max(0.0, min(2.375, ((cmp_ / att) - 0.3) * 5))
    b = max(0.0, min(2.375, ((yds / att) - 3) * 0.25))
    c = max(0.0, min(2.375, (td / att) * 20))
    d = max(0.0, min(2.375, 2.375 - (ints / att) * 25))
    return (a + b + c + d) / 6 * 100


def grade_for(score: float | None) -> str:
    if score is None:
        return "?"
    if score >= 80:
        return "A"
    if score >= 60:
        return "B"
    if score >= 40:
        return "C"
    if score >= 20:
        return "D"
    return "F"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_state(refresh: bool) -> dict:
    return fetch_json(f"{SLEEPER}/state/nfl", refresh, ttl=600)


def load_schedule(season: int, week: int, refresh: bool) -> dict[str, dict]:
    """team -> {opp, home, gameday, gametime} for the given week."""
    rows = read_csv(fetch_text(f"{NFLVERSE}/schedules/games.csv", refresh))
    out = {}
    for r in rows:
        if r["season"] != str(season) or r["game_type"] != "REG" or r["week"] != str(week):
            continue
        base = {"gameday": r["gameday"], "gametime": r["gametime"], "game_id": r["game_id"]}
        out[r["home_team"]] = dict(base, opp=r["away_team"], home=True)
        out[r["away_team"]] = dict(base, opp=r["home_team"], home=False)
    return out


def load_rosters(season: int, refresh: bool) -> tuple[dict, dict]:
    """Returns (by_gsis, by_sleeper) from the nflverse roster file."""
    rows = read_csv(fetch_text(f"{NFLVERSE}/rosters/roster_{season}.csv", refresh))
    by_gsis, by_sleeper = {}, {}
    for r in rows:
        if r["gsis_id"]:
            by_gsis[r["gsis_id"]] = r
        if r["sleeper_id"]:
            by_sleeper[r["sleeper_id"]] = r
    return by_gsis, by_sleeper


def load_depth_charts(season: int, refresh: bool) -> tuple[dict, dict, str]:
    """Latest depth-chart snapshot.

    Returns (defense, offense, snapshot_date) where
      defense[team] = {"LCB": [rows...], "RCB": [...], "NB": [...]}  (sorted by rank)
      offense[team] = {"WR1": row, "WR2": row, "WR3": row}
    """
    rows = read_csv(fetch_text(f"{NFLVERSE}/depth_charts/depth_charts_{season}.csv.gz", refresh))
    latest_by_team = defaultdict(str)
    for r in rows:
        if r["dt"] > latest_by_team[r["team"]]:
            latest_by_team[r["team"]] = r["dt"]
    defense = defaultdict(lambda: defaultdict(list))
    offense = defaultdict(dict)
    for r in rows:
        if r["dt"] != latest_by_team[r["team"]]:
            continue
        t = r["team"]
        if r["pos_abb"] in ("LCB", "RCB", "NB"):
            defense[t][r["pos_abb"]].append(r)
        elif r["pos_abb"] == "WR":
            slot = {"1": "WR1", "2": "WR2", "8": "WR3"}.get(r["pos_slot"])
            if slot:
                cur = offense[t].get(slot)
                if cur is None or int(r["pos_rank"]) < int(cur["pos_rank"]):
                    offense[t][slot] = r
    for t in defense:
        for k in defense[t]:
            defense[t][k].sort(key=lambda r: int(r["pos_rank"]))
    snapshot = max(latest_by_team.values()) if latest_by_team else ""
    return defense, offense, snapshot[:10]


def load_cb_stats(season_weights: dict[int, float], refresh: bool) -> dict:
    """Pool PFR coverage stats across seasons for every CB/S.

    season_weights maps season -> weight; counting stats are multiplied by
    the weight before pooling so older seasons matter less. Returns
    {key: stats} where key is the pfr_id when present, else
    'name:<normalised name>'. Each stats dict carries pooled counting
    stats, rate stats, per-season notes, and later a score/grade.
    """
    seasons = set(season_weights)
    rows = read_csv(fetch_text(f"{NFLVERSE}/pfr_advstats/advstats_season_def.csv", refresh))
    # Per season, keep one row per player (the multi-team total when traded).
    best = {}
    for r in rows:
        season = int(r["season"])
        if season not in seasons or r["pos"] not in CB_POSITIONS:
            continue
        key = r["pfr_id"] or f"name:{norm_name(r['player'])}"
        cur = best.get((season, key))
        if cur is None or fnum(r["g"]) > fnum(cur["g"]):
            best[(season, key)] = r

    pooled = {}
    for (season, key), r in best.items():
        s = pooled.setdefault(key, {
            "name": r["player"], "pfr_id": r["pfr_id"], "team": r["tm"],
            "tgt": 0.0, "cmp": 0.0, "yds": 0.0, "td": 0.0, "int": 0.0,
            "seasons": {}, "is_cb": r["pos"] not in SAFETY_POSITIONS,
        })
        if season >= max(s["seasons"].keys(), default=-1):
            s["team"] = r["tm"]
            s["name"] = r["player"]
        w = season_weights[season]
        for f in ("tgt", "cmp", "yds", "td", "int"):
            s[f] += w * fnum(r[f])
        s["seasons"][season] = {"tgt": fnum(r["tgt"]), "g": fnum(r["g"]), "team": r["tm"]}

    for s in pooled.values():
        t = s["tgt"]
        s["cmp_pct"] = s["cmp"] / t if t else None
        s["yds_tgt"] = s["yds"] / t if t else None
        s["rating"] = passer_rating(s["cmp"], t, s["yds"], s["td"], s["int"])
        s["score"] = None
        s["grade"] = "?"

    # Percentile-rank every qualified defender against the population of
    # qualified *cornerbacks*, so a safety playing nickel is graded on the
    # same scale as the CBs and does not shift the CB scale. Lower allowed
    # numbers = tougher.
    qualified = [s for s in pooled.values() if s["tgt"] >= MIN_TARGETS]
    reference = [s for s in qualified if s["is_cb"]]
    n = len(reference)
    if n > 1:
        for metric in ("yds_tgt", "rating", "cmp_pct"):
            ref_vals = sorted(s[metric] for s in reference)  # ascending = best first
            for s in qualified:
                worse = n - bisect.bisect_left(ref_vals, s[metric])  # CBs with a higher (worse) value
                if s[metric] in ref_vals:
                    worse -= 1  # exclude the player from their own comparison
                s.setdefault("_pct", []).append(100.0 * worse / (n - 1))
        for s in qualified:
            raw = sum(s.pop("_pct")) / 3
            # Shrink small samples toward neutral: 43 targets keeps ~68% of
            # its distance from 50, 120 targets keeps ~86%.
            shrink = s["tgt"] / (s["tgt"] + SHRINK_TARGETS)
            s["score"] = NEUTRAL_SCORE + (raw - NEUTRAL_SCORE) * shrink
            s["grade"] = grade_for(s["score"])
    return pooled


def load_overrides() -> dict[str, str]:
    if not os.path.exists(OVERRIDES_FILE):
        return {}
    with open(OVERRIDES_FILE) as fh:
        raw = json.load(fh)
    return {norm_name(k): v for k, v in raw.get("alignment", {}).items()}


# ---------------------------------------------------------------------------
# Matching CBs on the depth chart to their PFR stats
# ---------------------------------------------------------------------------

class CBIndex:
    def __init__(self, cb_stats: dict, roster_by_gsis: dict):
        self.stats = cb_stats
        self.roster = roster_by_gsis
        self.by_name = defaultdict(list)
        self.by_key = defaultdict(list)
        for s in cb_stats.values():
            self.by_name[norm_name(s["name"])].append(s)
            self.by_key[name_key(s["name"])].append(s)

    def lookup(self, depth_row: dict) -> dict | None:
        ros = self.roster.get(depth_row["gsis_id"])
        if ros and ros.get("pfr_id") and ros["pfr_id"] in self.stats:
            return self.stats[ros["pfr_id"]]
        for candidates in (self.by_name.get(norm_name(depth_row["player_name"]), []),
                           self.by_key.get(name_key(depth_row["player_name"]), [])):
            if len(candidates) == 1:
                return candidates[0]
            same_team = [c for c in candidates if c["team"] == depth_row["team"]]
            if len(same_team) == 1:
                return same_team[0]
        return None


def describe_cb(depth_row: dict | None, stats: dict | None, roster_by_gsis: dict) -> dict:
    if depth_row is None:
        return {"name": "(no CB listed)", "grade": "?", "score": None, "note": ""}
    name = depth_row["player_name"]
    ros = roster_by_gsis.get(depth_row["gsis_id"], {})
    if stats is None:
        note = "rookie" if ros.get("years_exp") in ("0", "") else "no coverage data"
        return {"name": name, "grade": "?", "score": None, "note": note, "tgt": 0}
    seasons = ", ".join(f"{y}: {int(v['tgt'])} tgt" for y, v in sorted(stats["seasons"].items()))
    note = seasons
    if stats["score"] is None:
        note = f"low sample ({int(stats['tgt'])} tgt)"
    return {
        "name": name, "grade": stats["grade"], "score": stats["score"], "note": note,
        "tgt": stats["tgt"], "yds_tgt": stats["yds_tgt"], "rating": stats["rating"],
        "cmp_pct": stats["cmp_pct"], "td": stats["td"], "int": stats["int"],
    }


# ---------------------------------------------------------------------------
# Matchup evaluation
# ---------------------------------------------------------------------------

def wr_role(wr_name: str, team: str, offense: dict, overrides: dict) -> str:
    """'outside', 'slot', or 'depth' (not in the top three on the chart)."""
    ov = overrides.get(norm_name(wr_name))
    if ov in ("slot", "outside"):
        return ov
    chart = offense.get(team, {})
    for slot, row in chart.items():
        if norm_name(row["player_name"]) == norm_name(wr_name):
            return "slot" if slot == "WR3" else "outside"
    return "depth"


def evaluate(wr: dict, week_sched: dict, defense: dict, offense: dict,
             cb_index: CBIndex, roster_by_gsis: dict, overrides: dict) -> dict:
    """wr = {name, team, ...}. Returns a matchup record."""
    team = wr["team"]
    rec = dict(wr)
    game = week_sched.get(team)
    if not game:
        rec.update(opp=None, verdict="BYE", score=None, role=wr_role(wr["name"], team, offense, overrides),
                   cbs=[], shadow=False)
        return rec
    opp = game["opp"]
    role = wr_role(wr["name"], team, offense, overrides)
    d = defense.get(opp, {})
    lcb = d.get("LCB", [None])[0] if d.get("LCB") else None
    rcb = d.get("RCB", [None])[0] if d.get("RCB") else None
    nb = d.get("NB", [None])[0] if d.get("NB") else None

    def desc(row):
        return describe_cb(row, cb_index.lookup(row) if row else None, roster_by_gsis)

    boundary = [desc(lcb), desc(rcb)]
    nickel = desc(nb)
    if role == "slot":
        primary = [nickel]
        others = boundary
    else:
        primary = boundary
        others = [nickel]
    scores = [c["score"] if c["score"] is not None else NEUTRAL_SCORE for c in primary]
    score = sum(scores) / len(scores)
    shadow = any(c["grade"] == "A" for c in boundary)
    if score >= TOUGH_SCORE:
        verdict = "TOUGH"
    elif score <= SOFT_SCORE:
        verdict = "SOFT"
    else:
        verdict = "NEUTRAL"
    rec.update(opp=opp, home=game["home"], gameday=game["gameday"], role=role,
               cbs=primary, other_cbs=others, score=score, verdict=verdict, shadow=shadow,
               unit_avg=sum(c["score"] if c["score"] is not None else NEUTRAL_SCORE
                            for c in boundary + [nickel]) / 3)
    return rec


# ---------------------------------------------------------------------------
# Sleeper roster
# ---------------------------------------------------------------------------

def my_wrs(league_id: str, username: str, roster_by_sleeper: dict, refresh: bool) -> tuple[list[dict], str]:
    user = fetch_json(f"{SLEEPER}/user/{username}", refresh)
    league = fetch_json(f"{SLEEPER}/league/{league_id}", refresh)
    rosters = fetch_json(f"{SLEEPER}/league/{league_id}/rosters", refresh, ttl=300)
    users = fetch_json(f"{SLEEPER}/league/{league_id}/users", refresh)
    mine = next((r for r in rosters if r["owner_id"] == user["user_id"]), None)
    if mine is None:
        sys.exit(f"{username} does not own a roster in league {league_id}")
    me = next((u for u in users if u["user_id"] == user["user_id"]), {})
    team_name = (me.get("metadata") or {}).get("team_name") or me.get("display_name", username)

    slots = league.get("roster_positions", [])
    starters = mine.get("starters") or []
    slot_of = {}
    for i, pid in enumerate(starters):
        if pid and pid != "0":
            slot_of[pid] = slots[i] if i < len(slots) else "?"

    players_blob = None
    wrs = []
    for pid in mine.get("players") or []:
        ros = roster_by_sleeper.get(pid)
        if ros is None:
            if players_blob is None:
                players_blob = fetch_json(f"{SLEEPER}/players/nfl", refresh, ttl=7 * 86400)
            p = players_blob.get(pid, {})
            if p.get("position") != "WR":
                continue
            wrs.append({"name": p.get("full_name", pid), "team": p.get("team") or "FA",
                        "slot": slot_of.get(pid, "BN")})
            continue
        if ros["position"] != "WR":
            continue
        wrs.append({"name": ros["full_name"], "team": ros["team"], "slot": slot_of.get(pid, "BN")})
    return wrs, team_name


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def fmt_cb(c: dict, long: bool = False) -> str:
    if c["score"] is None:
        base = f"{c['name']} [{c['grade']}]"
        return f"{base} ({c['note']})" if c.get("note") else base
    s = f"{c['name']} [{c['grade']} {c['score']:.0f}]"
    if long:
        s += (f" {c['yds_tgt']:.1f} yds/tgt, {c['rating']:.0f} rtg, "
              f"{100 * c['cmp_pct']:.0f}% cmp, {int(c['td'])} TD / {int(c['int'])} INT "
              f"on {int(c['tgt'])} tgt")
    return s


def verdict_icon(v: str) -> str:
    return {"TOUGH": "🔴", "NEUTRAL": "🟡", "SOFT": "🟢", "BYE": "⚫"}.get(v, "")


def print_matchups(recs: list[dict], header: str, markdown: bool, out) -> None:
    recs = sorted(recs, key=lambda r: (-(r["score"] if r["score"] is not None else -1), r["name"]))
    if markdown:
        out.write(f"## {header}\n\n")
        out.write("| WR | Team | Spot | Opp | Role | Verdict | Score | Primary CB(s) | Shadow risk |\n")
        out.write("|---|---|---|---|---|---|---|---|---|\n")
        for r in recs:
            if r["verdict"] == "BYE":
                out.write(f"| {r['name']} | {r['team']} | {r.get('slot', '')} | BYE | | ⚫ BYE | | | |\n")
                continue
            opp = ("vs " if r["home"] else "@ ") + r["opp"]
            cbs = "<br>".join(fmt_cb(c) for c in r["cbs"])
            out.write(f"| {r['name']} | {r['team']} | {r.get('slot', '')} | {opp} | {r['role']} | "
                      f"{verdict_icon(r['verdict'])} {r['verdict']} | {r['score']:.0f} | {cbs} | "
                      f"{'yes' if r['shadow'] else ''} |\n")
        out.write("\n")
        return
    out.write(f"\n{header}\n{'=' * len(header)}\n")
    for r in recs:
        slot = f" ({r['slot']})" if r.get("slot") else ""
        if r["verdict"] == "BYE":
            out.write(f"\n{verdict_icon('BYE')} {r['name']} {r['team']}{slot}: BYE WEEK\n")
            continue
        opp = ("vs " if r["home"] else "@ ") + r["opp"]
        out.write(f"\n{verdict_icon(r['verdict'])} {r['name']} {r['team']}{slot} {opp} -- "
                  f"{r['verdict']} (score {r['score']:.0f}, {r['role']} WR)\n")
        for c in r["cbs"]:
            out.write(f"     primary : {fmt_cb(c, long=True)}\n")
        for c in r.get("other_cbs", []):
            out.write(f"     also    : {fmt_cb(c, long=True)}\n")
        if r["shadow"]:
            out.write("     note    : A-grade boundary CB -- shadow coverage risk for the WR1\n")


def print_cb_leaderboard(cb_stats: dict, defense: dict, cb_index: CBIndex, roster_by_gsis: dict,
                         markdown: bool, out) -> None:
    rows = []
    for team in sorted(defense):
        for pos in ("LCB", "RCB", "NB"):
            for row in defense[team].get(pos, [])[:1]:
                c = describe_cb(row, cb_index.lookup(row), roster_by_gsis)
                rows.append((team, pos, c))
    rows.sort(key=lambda x: -(x[2]["score"] if x[2]["score"] is not None else -1))
    if markdown:
        out.write("| # | CB | Team | Spot | Grade | Score | Yds/Tgt | Rtg | Cmp% | TD | INT | Tgt |\n")
        out.write("|---|---|---|---|---|---|---|---|---|---|---|---|\n")
        for i, (team, pos, c) in enumerate(rows, 1):
            if c["score"] is None:
                out.write(f"| {i} | {c['name']} | {team} | {pos} | ? | | | | | | | {c.get('note', '')} |\n")
            else:
                out.write(f"| {i} | {c['name']} | {team} | {pos} | {c['grade']} | {c['score']:.0f} | "
                          f"{c['yds_tgt']:.1f} | {c['rating']:.0f} | {100 * c['cmp_pct']:.0f} | "
                          f"{int(c['td'])} | {int(c['int'])} | {int(c['tgt'])} |\n")
        return
    out.write(f"{'#':>3} {'CB':<24} {'Tm':<4} {'Spot':<4} {'Gr':<3} {'Score':>5} {'Y/T':>5} {'Rtg':>5} "
              f"{'Cmp%':>5} {'TD':>3} {'INT':>3} {'Tgt':>4}\n")
    for i, (team, pos, c) in enumerate(rows, 1):
        if c["score"] is None:
            out.write(f"{i:>3} {c['name']:<24} {team:<4} {pos:<4} {'?':<3} {'':>5} {c.get('note', '')}\n")
        else:
            out.write(f"{i:>3} {c['name']:<24} {team:<4} {pos:<4} {c['grade']:<3} {c['score']:>5.0f} "
                      f"{c['yds_tgt']:>5.1f} {c['rating']:>5.0f} {100 * c['cmp_pct']:>5.0f} "
                      f"{int(c['td']):>3} {int(c['int']):>3} {int(c['tgt']):>4}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--week", type=int, help="NFL week (default: current week from Sleeper)")
    ap.add_argument("--season", type=int, help="NFL season (default: current season from Sleeper)")
    ap.add_argument("--username", default=SLEEPER_USERNAME, help="Sleeper username")
    ap.add_argument("--league", default=SLEEPER_LEAGUE_ID, help="Sleeper league id")
    ap.add_argument("--all", action="store_true", help="grade every NFL team's WR1-WR3, not just my roster")
    ap.add_argument("--player", action="append", default=[], help="grade a specific WR (repeatable)")
    ap.add_argument("--cbs", action="store_true", help="print the starting-CB toughness leaderboard")
    ap.add_argument("--full", action="store_true",
                    help="weekly report: my WRs, then every team's WR1-WR3, then the CB leaderboard")
    ap.add_argument("--markdown", action="store_true", help="emit Markdown instead of plain text")
    ap.add_argument("-o", "--out", help="write output to this file")
    ap.add_argument("--refresh", action="store_true", help="re-download everything, ignoring the cache")
    args = ap.parse_args(argv)

    state = load_state(args.refresh)
    season = args.season or int(state["season"])
    week = args.week or int(state.get("display_week") or state.get("week") or 1)

    week_sched = load_schedule(season, week, args.refresh)
    roster_by_gsis, roster_by_sleeper = load_rosters(season, args.refresh)
    defense, offense, snapshot = load_depth_charts(season, args.refresh)
    cb_stats = load_cb_stats({season: 1.0, season - 1: 1.0, season - 2: 0.5}, args.refresh)
    cb_index = CBIndex(cb_stats, roster_by_gsis)
    overrides = load_overrides()

    out = open(args.out, "w") if args.out else sys.stdout
    try:
        title = f"WR vs CB matchups -- {season} week {week}"
        sub = (f"depth charts as of {snapshot}; CB grades pool PFR coverage stats from "
               f"{season - 2} (half weight), {season - 1} and {season} (min {MIN_TARGETS} weighted targets)")
        if args.markdown:
            out.write(f"# {title}\n\n_{sub}_\n\n")
        else:
            out.write(f"{title}\n{sub}\n")

        if args.cbs:
            if args.markdown:
                out.write("## Starting CB toughness leaderboard\n\n")
            print_cb_leaderboard(cb_stats, defense, cb_index, roster_by_gsis, args.markdown, out)
            return 0

        if args.player:
            wrs = []
            for name in args.player:
                match = next((r for r in roster_by_gsis.values()
                              if r["position"] == "WR" and norm_name(r["full_name"]) == norm_name(name)), None)
                if match is None:
                    print(f"warning: could not find WR '{name}' on any {season} roster", file=sys.stderr)
                    continue
                wrs.append({"name": match["full_name"], "team": match["team"]})
            recs = [evaluate(w, week_sched, defense, offense, cb_index, roster_by_gsis, overrides) for w in wrs]
            print_matchups(recs, "Requested WRs", args.markdown, out)
            return 0

        def all_wrs() -> list[dict]:
            wrs = []
            for team in sorted(offense):
                for slot in ("WR1", "WR2", "WR3"):
                    row = offense[team].get(slot)
                    if row:
                        wrs.append({"name": row["player_name"], "team": team, "slot": slot})
            return wrs

        if args.all:
            recs = [evaluate(w, week_sched, defense, offense, cb_index, roster_by_gsis, overrides)
                    for w in all_wrs()]
            print_matchups(recs, "All NFL WR1-WR3", args.markdown, out)
            return 0

        wrs, team_name = my_wrs(args.league, args.username, roster_by_sleeper, args.refresh)
        recs = [evaluate(w, week_sched, defense, offense, cb_index, roster_by_gsis, overrides) for w in wrs]
        print_matchups(recs, f"{team_name} ({args.username}) -- my WRs", args.markdown, out)

        if args.full:
            recs = [evaluate(w, week_sched, defense, offense, cb_index, roster_by_gsis, overrides)
                    for w in all_wrs()]
            print_matchups(recs, "All NFL WR1-WR3 (waiver / trade targets)", args.markdown, out)
            out.write("## Starting CB toughness leaderboard\n\n" if args.markdown
                      else "\nStarting CB toughness leaderboard\n=================================\n")
            print_cb_leaderboard(cb_stats, defense, cb_index, roster_by_gsis, args.markdown, out)
            out.write("\n")

        if args.markdown:
            out.write("### Legend\n\n"
                      "- **Score**: 0-100, higher = tougher CB matchup. Outside WRs average the opponent's two "
                      "boundary CBs; slot WRs use the nickel CB. Ungraded CBs count as 50.\n"
                      f"- **Verdict**: TOUGH at {TOUGH_SCORE}+, SOFT at {SOFT_SCORE} or below, NEUTRAL between.\n"
                      "- **CB grade**: A-F from the CB's percentile on yards/target, passer rating, and "
                      "completion % allowed. `?` = rookie or fewer than "
                      f"{MIN_TARGETS} targets.\n"
                      "- **Shadow risk**: the opponent has an A-grade boundary CB who may travel with the WR1.\n")
        return 0
    finally:
        if args.out:
            out.close()


if __name__ == "__main__":
    sys.exit(main())
