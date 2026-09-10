#!/usr/bin/env python3
"""Weekly matchup checker for the Zebras Shooting Heroin Sleeper league.

Before you set a lineup, this answers: which of my (and my opponent's)
starters are facing a tough individual defender, and which are facing a
weak pass or run defense?

Data sources (all free, no API keys):
  * Sleeper API             -> league, rosters, this week's head-to-head
  * nflverse schedules      -> opponent, home/away, byes
  * nflverse depth charts   -> each defense's starting CBs, off-ball LBs,
                               safeties; each offense's WR1/WR2/WR3
  * nflverse PFR advstats   -> per-defender coverage stats allowed
  * nflverse team/player weekly stats -> what each defense allows, and
                               each receiver's target volume last season
  * nflverse schedules      -> Vegas spread and total (game script)
  * shadow_corners.json     -> corners documented as travelling with WR1s

Individual defender grades
  Coverage stats from this season, last season and (half weight) the
  season before are pooled. Every defender with enough targets is
  percentile-ranked *within their position group* (CB vs CBs, safeties
  vs safeties, LBs vs LBs) on yards per target, passer rating and
  completion % allowed. The average percentile is a 0-100 score
  (100 = hardest to throw on), shrunk toward 50 for small samples, and
  maps to a grade A-F. Rookies / low samples show "?". A second score
  from the last two seasons only is shown when it differs from the
  pooled one, and the matchup uses the average of the two so a corner
  who broke out last year is not dragged down by his rookie tape.

Team defense grades
  From last season plus this season (this season weighted 1.5x as it
  accrues). Pass D = yards/attempt, EPA/dropback and PPR points/game
  allowed to WR+TE; run D = yards/carry, EPA/carry and PPR points/game
  allowed to RBs. Each is a percentile among the 32 teams (100 =
  toughest) with a grade A-F.

Who faces whom
  QB  -> opponent pass D
  WR  -> boundary CBs (outside) or nickel (slot), blended with the PPR
         the defense allows *to receivers* (not the overall pass grade:
         a defense can be terrible against the pass and still hold WRs
         if the leak is at TE and RB -- the report says where it goes)
  TE  -> the two starting safeties, blended with PPR allowed to TEs
  RB  -> run D, blended with the off-ball LBs' coverage grades
  A team's WR1 facing a documented travel corner (shadow_corners.json)
  is scored against that corner alone.

Context printed with every matchup
  Vegas  -> spread, total and the team's implied points; a heavy
            favourite leans run, an underdog in a high total throws
  Volume -> last season's targets per game and target share (and this
            season's once it exists); the floor a matchup cannot take away
  Health -> Sleeper injury tags on the defenders themselves

Usage
  python3 matchups.py                    # my lineup + my opponent's, this week
  python3 matchups.py --week 3
  python3 matchups.py --all              # every team's WR1-WR3, RB1, TE1, QB1
  python3 matchups.py --player "Bijan Robinson" --player "Trey McBride"
  python3 matchups.py --defenses         # team pass/run defense table
  python3 matchups.py --cbs              # starting-CB toughness leaderboard
  python3 matchups.py --full --markdown --out-dir matchups   # writes matchups/weekNN.md
  python3 matchups.py --refresh          # ignore the 12h cache

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
import urllib.error
import urllib.request
from collections import defaultdict

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SLEEPER_USERNAME = "thevalz"
SLEEPER_LEAGUE_ID = "1390899489962741761"  # Zebras Shooting Heroin (2026)

NFLVERSE = "https://github.com/nflverse/nflverse-data/releases/download"
SLEEPER = "https://api.sleeper.app/v1"

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")
CACHE_TTL_SECONDS = 12 * 3600

MIN_TARGETS = 30          # weighted targets needed before a defender gets a grade
SHRINK_TARGETS = 20       # small-sample scores are pulled toward NEUTRAL_SCORE
TOUGH_SCORE = 68          # matchup score at/above this -> "TOUGH"
SOFT_SCORE = 38           # matchup score at/below this -> "SOFT"
NEUTRAL_SCORE = 50.0      # what an ungraded defender / unknown team counts as
CURRENT_SEASON_TEAM_WEIGHT = 1.5

# How much the individual defender vs the team defense matters per position.
BLEND = {"WR": 0.65, "TE": 0.5, "RB": 0.4}   # weight on the individual matchup

OVERRIDES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wr_alignment_overrides.json")
SHADOW_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shadow_corners.json")

RECENT_SEASONS = 2        # the "recent" defender score uses this many seasons at full weight
RECENT_DELTA = 8          # show the recent score when it differs from pooled by this much
HEAVY_FAVORITE = 7.0      # favoured by this many -> run-leaning script
LIVE_DOG = 3.0            # underdog by this many in a high total -> pass-volume script
HIGH_TOTAL = 48.0
LOW_TOTAL = 41.0
LEAK_RANK = 27            # a defense ranked here or worse (of 32) vs a position "leaks" to it
HOLD_RANK = 6             # ranked here or better "holds" it

# PFR position label -> grading group
POS_GROUP = {
    "CB": "CB", "DB": "CB", "LCB": "CB", "RCB": "CB", "NB": "CB", "SCB": "CB", "CB/S": "CB",
    "S": "S", "FS": "S", "SS": "S", "DB/S": "S",
    "LB": "LB", "MLB": "LB", "ILB": "LB", "OLB": "LB", "LILB": "LB", "RILB": "LB",
    "WLB": "LB", "SLB": "LB", "LOLB": "LB", "ROLB": "LB",
}
# depth-chart abbreviations that are off-ball (coverage) linebackers, per scheme
OFFBALL_LB = {"Base 4-3 D": ("MLB", "WLB"), "Base 3-4 D": ("LILB", "RILB")}


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
    req = urllib.request.Request(url, headers={"User-Agent": "zsh-matchups/2.0"})
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


def fetch_text_optional(url: str, refresh: bool = False) -> str | None:
    """Like fetch_text but returns None when the file does not exist yet
    (nflverse publishes a season's weekly stats after its first games)."""
    try:
        return fetch_text(url, refresh)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


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
    return (parts[0][:3] + " " + parts[-1]) if parts else ""


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
    for cut, g in ((80, "A"), (60, "B"), (40, "C"), (20, "D")):
        if score >= cut:
            return g
    return "F"


def verdict_for(score: float) -> str:
    if score >= TOUGH_SCORE:
        return "TOUGH"
    if score <= SOFT_SCORE:
        return "SOFT"
    return "NEUTRAL"


def verdict_icon(v: str) -> str:
    return {"TOUGH": "🔴", "NEUTRAL": "🟡", "SOFT": "🟢", "BYE": "⚫", "N/A": "⚪"}.get(v, "")


def percentile_lower_is_better(values: list[float], v: float) -> float:
    """Share of `values` that are worse (higher) than v, on a 0-100 scale."""
    n = len(values)
    if n < 2:
        return NEUTRAL_SCORE
    ordered = sorted(values)
    worse = n - bisect.bisect_left(ordered, v)
    if v in ordered:
        worse -= 1
    return 100.0 * worse / (n - 1)


def score_or_neutral(x: dict | None) -> float:
    return x["score"] if x and x.get("score") is not None else NEUTRAL_SCORE


def eff_score(x: dict | None) -> float:
    """A defender's score as used in the blend (pooled averaged with recent), else neutral."""
    if x and x.get("eff_score") is not None:
        return x["eff_score"]
    return score_or_neutral(x)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_state(refresh: bool) -> dict:
    return fetch_json(f"{SLEEPER}/state/nfl", refresh, ttl=600)


def load_schedule(season: int, week: int, refresh: bool) -> dict[str, dict]:
    """team -> {opp, home, gameday, gametime, fav_by, total, implied} for the week.

    nflverse's spread_line is from the home team's side: positive means the
    home team is favoured by that much. fav_by is re-expressed from each
    team's own side (negative = underdog), and implied is the team's
    implied points from the total and the spread.
    """
    rows = read_csv(fetch_text(f"{NFLVERSE}/schedules/games.csv", refresh))
    out = {}
    for r in rows:
        if r["season"] != str(season) or r["game_type"] != "REG" or r["week"] != str(week):
            continue
        base = {"gameday": r["gameday"], "gametime": r["gametime"], "game_id": r["game_id"]}
        spread = fnum(r["spread_line"]) if r.get("spread_line") else None
        total = fnum(r["total_line"]) if r.get("total_line") else None

        def vegas(fav_by):
            if fav_by is None or total is None:
                return {"fav_by": None, "total": total, "implied": None}
            return {"fav_by": fav_by, "total": total, "implied": total / 2 + fav_by / 2}

        out[r["home_team"]] = dict(base, opp=r["away_team"], home=True, **vegas(spread))
        out[r["away_team"]] = dict(base, opp=r["home_team"], home=False,
                                   **vegas(-spread if spread is not None else None))
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
    """Latest depth-chart snapshot per team.

    defense[team] = {"LCB": row, "RCB": row, "NB": row, "FS": row, "SS": row, "LB": [row, row]}
    offense[team] = {"WR1": row, "WR2": row, "WR3": row, "RB1": row, "TE1": row, "QB1": row}
    """
    rows = read_csv(fetch_text(f"{NFLVERSE}/depth_charts/depth_charts_{season}.csv.gz", refresh))
    latest = defaultdict(str)
    for r in rows:
        if r["dt"] > latest[r["team"]]:
            latest[r["team"]] = r["dt"]
    defense = defaultdict(dict)
    offense = defaultdict(dict)
    off_slots = {("WR", "1"): "WR1", ("WR", "2"): "WR2", ("WR", "8"): "WR3",
                 ("RB", "11"): "RB1", ("TE", "10"): "TE1", ("QB", "9"): "QB1"}

    def keep_best(bucket: dict, key: str, r: dict) -> None:
        cur = bucket.get(key)
        if cur is None or int(r["pos_rank"]) < int(cur["pos_rank"]):
            bucket[key] = r

    for r in rows:
        if r["dt"] != latest[r["team"]]:
            continue
        t, ab = r["team"], r["pos_abb"]
        if ab in ("LCB", "RCB", "NB", "FS", "SS"):
            keep_best(defense[t], ab, r)
        elif ab in OFFBALL_LB.get(r["pos_grp"], ()):
            keep_best(defense[t], "LB:" + ab, r)
        else:
            slot = off_slots.get((ab, r["pos_slot"]))
            if slot:
                keep_best(offense[t], slot, r)
    for t in defense:
        defense[t]["LB"] = [defense[t].pop(k) for k in sorted(defense[t]) if k.startswith("LB:")]
    snapshot = max(latest.values()) if latest else ""
    return defense, offense, snapshot[:10]


def load_defender_stats(season_weights: dict[int, float], refresh: bool) -> dict:
    """Pool PFR coverage stats for every CB / S / LB across seasons.

    Returns {key: stats}; key is the pfr_id when present, else
    'name:<normalised name>'. Scores are percentiles within the
    defender's position group.
    """
    rows = read_csv(fetch_text(f"{NFLVERSE}/pfr_advstats/advstats_season_def.csv", refresh))
    best = {}
    for r in rows:
        season = int(r["season"])
        if season not in season_weights or r["pos"] not in POS_GROUP:
            continue
        key = r["pfr_id"] or f"name:{norm_name(r['player'])}"
        cur = best.get((season, key))
        if cur is None or fnum(r["g"]) > fnum(cur["g"]):   # keep the multi-team total row
            best[(season, key)] = r

    def pool(weights: dict[int, float]) -> dict:
        pooled = {}
        for (season, key), r in best.items():
            if season not in weights:
                continue
            s = pooled.setdefault(key, {
                "name": r["player"], "pfr_id": r["pfr_id"], "team": r["tm"], "group": POS_GROUP[r["pos"]],
                "tgt": 0.0, "cmp": 0.0, "yds": 0.0, "td": 0.0, "int": 0.0, "seasons": {},
            })
            if season >= max(s["seasons"], default=-1):
                s.update(team=r["tm"], name=r["player"], group=POS_GROUP[r["pos"]])
            w = weights[season]
            for f in ("tgt", "cmp", "yds", "td", "int"):
                s[f] += w * fnum(r[f])
            tgt = fnum(r["tgt"])
            s["seasons"][season] = {
                "tgt": tgt, "g": fnum(r["g"]), "team": r["tm"],
                "cmp_pct": fnum(r["cmp"]) / tgt if tgt else None,
                "yds_tgt": fnum(r["yds"]) / tgt if tgt else None,
                "rating": passer_rating(fnum(r["cmp"]), tgt, fnum(r["yds"]), fnum(r["td"]), fnum(r["int"])),
                "td": fnum(r["td"]), "int": fnum(r["int"]),
            }

        for s in pooled.values():
            t = s["tgt"]
            s["cmp_pct"] = s["cmp"] / t if t else None
            s["yds_tgt"] = s["yds"] / t if t else None
            s["rating"] = passer_rating(s["cmp"], t, s["yds"], s["td"], s["int"])
            s["score"], s["grade"] = None, "?"

        for group in ("CB", "S", "LB"):
            qualified = [s for s in pooled.values() if s["group"] == group and s["tgt"] >= MIN_TARGETS]
            if len(qualified) < 2:
                continue
            ref = {m: [s[m] for s in qualified] for m in ("yds_tgt", "rating", "cmp_pct")}
            for s in qualified:
                raw = sum(percentile_lower_is_better(ref[m], s[m]) for m in ref) / 3
                shrink = s["tgt"] / (s["tgt"] + SHRINK_TARGETS)
                s["score"] = NEUTRAL_SCORE + (raw - NEUTRAL_SCORE) * shrink
                s["grade"] = grade_for(s["score"])
        return pooled

    pooled = pool(season_weights)
    # A second pass over the most recent seasons only, at full weight. A corner
    # who broke out last year carries his rookie tape in the pooled number; the
    # recent score is what he looks like now. Both are kept.
    recent_years = sorted(season_weights)[-RECENT_SEASONS:]
    recent = pool({y: 1.0 for y in recent_years})
    for key, s in pooled.items():
        r = recent.get(key)
        s["recent_score"] = r["score"] if r else None
        s["recent_grade"] = r["grade"] if r else "?"
        s["recent_years"] = recent_years
    return pooled


def load_team_defense(season: int, refresh: bool) -> dict[str, dict]:
    """What each defense allows, pooled over last season and this season.

    Returns team -> {pass: {...}, run: {...}, fp: {QB, RB, WR, TE}} with
    scores/grades/ranks (100 = toughest).
    """
    weights = {season - 1: 1.0, season: CURRENT_SEASON_TEAM_WEIGHT}
    acc = defaultdict(lambda: defaultdict(float))
    games = defaultdict(float)
    seasons_used = []
    for yr, w in weights.items():
        text = fetch_text_optional(f"{NFLVERSE}/stats_team/stats_team_week_{yr}.csv", refresh)
        if text is None:
            continue
        seasons_used.append(yr)
        for r in read_csv(text):
            if r["season_type"] != "REG":
                continue
            d = acc[r["opponent_team"]]          # the defense that faced this offense
            d["att"] += w * fnum(r["attempts"])
            d["sacks"] += w * fnum(r["sacks_suffered"])
            d["pass_yds"] += w * fnum(r["passing_yards"])
            d["pass_epa"] += w * fnum(r["passing_epa"])
            d["carries"] += w * fnum(r["carries"])
            d["rush_yds"] += w * fnum(r["rushing_yards"])
            d["rush_epa"] += w * fnum(r["rushing_epa"])
            games[r["opponent_team"]] += w
        ptext = fetch_text_optional(f"{NFLVERSE}/stats_player/stats_player_week_{yr}.csv", refresh)
        if ptext is None:
            continue
        for r in read_csv(ptext):
            if r["season_type"] != "REG" or r["position"] not in ("QB", "RB", "WR", "TE"):
                continue
            acc[r["opponent_team"]]["fp_" + r["position"]] += w * fnum(r["fantasy_points_ppr"])

    teams = {}
    for t, d in acc.items():
        if not games[t]:
            continue
        dropbacks = d["att"] + d["sacks"]
        teams[t] = {
            "games": games[t], "seasons": seasons_used,
            "yds_att": d["pass_yds"] / d["att"] if d["att"] else None,
            "epa_db": d["pass_epa"] / dropbacks if dropbacks else None,
            "yds_carry": d["rush_yds"] / d["carries"] if d["carries"] else None,
            "epa_rush": d["rush_epa"] / d["carries"] if d["carries"] else None,
            "fp": {p: d["fp_" + p] / games[t] for p in ("QB", "RB", "WR", "TE")},
        }
    if len(teams) < 2:
        return {}

    def rank_metric(get):
        vals = [get(v) for v in teams.values()]
        return {t: percentile_lower_is_better(vals, get(v)) for t, v in teams.items()}

    pct = {
        "yds_att": rank_metric(lambda v: v["yds_att"]),
        "epa_db": rank_metric(lambda v: v["epa_db"]),
        "fp_pass": rank_metric(lambda v: v["fp"]["WR"] + v["fp"]["TE"]),
        "yds_carry": rank_metric(lambda v: v["yds_carry"]),
        "epa_rush": rank_metric(lambda v: v["epa_rush"]),
        "fp_RB": rank_metric(lambda v: v["fp"]["RB"]),
    }
    fp_pct = {p: rank_metric(lambda v, p=p: v["fp"][p]) for p in ("QB", "RB", "WR", "TE")}
    for t, v in teams.items():
        v["pass_score"] = (pct["yds_att"][t] + pct["epa_db"][t] + pct["fp_pass"][t]) / 3
        v["run_score"] = (pct["yds_carry"][t] + pct["epa_rush"][t] + pct["fp_RB"][t]) / 3
        v["pass_grade"] = grade_for(v["pass_score"])
        v["run_grade"] = grade_for(v["run_score"])
        v["fp_score"] = {p: fp_pct[p][t] for p in fp_pct}
    for key in ("pass_score", "run_score"):
        for i, t in enumerate(sorted(teams, key=lambda t: -teams[t][key]), 1):
            teams[t][key.replace("score", "rank")] = i
    for p in ("QB", "RB", "WR", "TE"):
        for i, t in enumerate(sorted(teams, key=lambda t: -teams[t]["fp_score"][p]), 1):
            teams[t].setdefault("fp_rank", {})[p] = i
    return teams


def load_overrides() -> dict[str, str]:
    if not os.path.exists(OVERRIDES_FILE):
        return {}
    with open(OVERRIDES_FILE) as fh:
        raw = json.load(fh)
    return {norm_name(k): v for k, v in raw.get("alignment", {}).items()}


def load_shadow_corners() -> dict[str, dict]:
    """norm_name -> {name, note, source} for corners documented as travelling with WR1s."""
    if not os.path.exists(SHADOW_FILE):
        return {}
    with open(SHADOW_FILE) as fh:
        raw = json.load(fh)
    return {norm_name(c["name"]): c for c in raw.get("corners", []) if c.get("name")}


def load_sleeper_players(refresh: bool) -> dict:
    """Sleeper's player file, for injury tags on defenders (a week's cache is plenty)."""
    try:
        return fetch_json(f"{SLEEPER}/players/nfl", refresh, ttl=7 * 86400)
    except (urllib.error.URLError, TimeoutError, ValueError):
        return {}


def load_player_volume(season: int, refresh: bool) -> dict:
    """(norm_name, pos) -> {season: {g, tgt, share, rec, yds, carries, ppr}}.

    Last season plus this season as it accrues. Volume is the half of a
    matchup a corner cannot take away, so it is printed next to every grade.
    """
    out = defaultdict(dict)
    for yr in (season - 1, season):
        text = fetch_text_optional(f"{NFLVERSE}/stats_player/stats_player_week_{yr}.csv", refresh)
        if text is None:
            continue
        acc = defaultdict(lambda: {"g": 0, "tgt": 0.0, "share": 0.0, "share_n": 0, "rec": 0.0,
                                   "yds": 0.0, "carries": 0.0, "ppr": 0.0})
        for r in read_csv(text):
            if r["season_type"] != "REG" or r["position"] not in ("QB", "RB", "WR", "TE"):
                continue
            a = acc[(norm_name(r["player_display_name"]), r["position"])]
            a["g"] += 1
            a["tgt"] += fnum(r["targets"])
            a["rec"] += fnum(r["receptions"])
            a["yds"] += fnum(r["receiving_yards"])
            a["carries"] += fnum(r["carries"])
            a["ppr"] += fnum(r["fantasy_points_ppr"])
            if r.get("target_share"):
                a["share"] += fnum(r["target_share"])
                a["share_n"] += 1
        for key, a in acc.items():
            out[key][yr] = a
    return out


# ---------------------------------------------------------------------------
# Matching depth-chart defenders to their PFR stats
# ---------------------------------------------------------------------------

class DefenderIndex:
    def __init__(self, stats: dict, roster_by_gsis: dict, sleeper_players: dict | None = None):
        self.stats = stats
        self.roster = roster_by_gsis
        self.sleeper = sleeper_players or {}
        self.by_name = defaultdict(list)
        self.by_key = defaultdict(list)
        for s in stats.values():
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

    def injury(self, depth_row: dict) -> str:
        """'Questionable (Ankle)' from Sleeper's player file, or ''."""
        ros = self.roster.get(depth_row["gsis_id"], {})
        p = self.sleeper.get(ros.get("sleeper_id") or "", {})
        status = p.get("injury_status")
        if not status:
            return ""
        part = (p.get("injury_body_part") or "").strip()
        return f"{status} ({part})" if part else status

    def describe(self, depth_row: dict | None, label: str) -> dict:
        empty = {"injury": "", "seasons": [], "recent_score": None, "recent_grade": "?", "eff_score": None}
        if depth_row is None:
            return dict(empty, name=f"(no {label} listed)", label=label, grade="?", score=None, note="")
        name = depth_row["player_name"]
        stats = self.lookup(depth_row)
        ros = self.roster.get(depth_row["gsis_id"], {})
        injury = self.injury(depth_row)
        if stats is None:
            note = "rookie" if ros.get("years_exp") in ("0", "") else "no coverage data"
            return dict(empty, name=name, label=label, grade="?", score=None, note=note, tgt=0, injury=injury)
        seasons = []
        for y, v in sorted(stats["seasons"].items(), reverse=True):
            if v["tgt"] and v["cmp_pct"] is not None:
                seasons.append(f"{y}: {int(v['tgt'])} tgt, {100 * v['cmp_pct']:.0f}% cmp, "
                               f"{v['yds_tgt']:.1f} y/t, {v['rating']:.0f} rtg, "
                               f"{int(v['td'])} TD / {int(v['int'])} INT")
            else:
                seasons.append(f"{y}: {int(v['tgt'])} tgt")
        note = f"low sample ({int(stats['tgt'])} tgt)" if stats["score"] is None else ""
        # The score the matchup actually uses: pooled, averaged with the
        # recent-seasons-only score when that one is graded, so current form
        # counts without throwing the longer sample away.
        eff = stats["score"]
        rs = stats.get("recent_score")
        if eff is not None and rs is not None:
            eff = (eff + rs) / 2
        return {
            "name": name, "label": label, "grade": stats["grade"], "score": stats["score"], "note": note,
            "tgt": stats["tgt"], "yds_tgt": stats["yds_tgt"], "rating": stats["rating"],
            "cmp_pct": stats["cmp_pct"], "td": stats["td"], "int": stats["int"], "group": stats["group"],
            "injury": injury, "seasons": seasons, "recent_score": rs,
            "recent_grade": stats.get("recent_grade", "?"),
            "recent_years": [y for y in stats.get("recent_years", []) if y in stats["seasons"]],
            "eff_score": eff,
        }


# ---------------------------------------------------------------------------
# Matchup evaluation
# ---------------------------------------------------------------------------

def wr_role(name: str, team: str, offense: dict, overrides: dict) -> str:
    ov = overrides.get(norm_name(name))
    if ov in ("slot", "outside"):
        return ov
    for slot, row in offense.get(team, {}).items():
        if slot.startswith("WR") and norm_name(row["player_name"]) == norm_name(name):
            return "slot" if slot == "WR3" else "outside"
    return "outside"


class Evaluator:
    def __init__(self, week_sched, defense, offense, team_def, idx: DefenderIndex, overrides,
                 shadow: dict | None = None, volume: dict | None = None):
        self.sched, self.defense, self.offense = week_sched, defense, offense
        self.team_def, self.idx, self.overrides = team_def, idx, overrides
        self.shadow, self.volume = shadow or {}, volume or {}

    def team_line(self, opp: str, kind: str, pos: str) -> dict | None:
        """The team-defense half of a matchup.

        `score` is what the blend uses. For a QB that is the overall pass
        grade; for a WR or TE it is the percentile of PPR the defense allows
        to that position, because the overall grade can be an F while the
        boundary holds and the damage all comes at tight end and running
        back. `unit_score` is the overall pass/run grade for display, and
        `leaks`/`holds` say where the points actually go.
        """
        td = self.team_def.get(opp)
        if not td:
            return None
        ranks = td["fp_rank"]
        leaks = [f"{p} (#{ranks[p]})" for p in ("QB", "RB", "WR", "TE") if ranks[p] >= LEAK_RANK]
        holds = [f"{p} (#{ranks[p]})" for p in ("QB", "RB", "WR", "TE") if ranks[p] <= HOLD_RANK]
        common = {"fp": td["fp"][pos], "fp_rank": ranks[pos], "pos": pos, "leaks": leaks, "holds": holds}
        if kind == "pass":
            blend = td["pass_score"] if pos == "QB" else td["fp_score"][pos]
            return dict(common, kind="pass", score=blend, unit_score=td["pass_score"],
                        grade=td["pass_grade"], rank=td["pass_rank"],
                        detail=f"{td['yds_att']:.1f} yds/att, {td['epa_db']:+.2f} EPA/dropback")
        return dict(common, kind="run", score=td["run_score"], unit_score=td["run_score"],
                    grade=td["run_grade"], rank=td["run_rank"],
                    detail=f"{td['yds_carry']:.1f} yds/carry, {td['epa_rush']:+.2f} EPA/carry")

    def is_wr1(self, name: str, team: str) -> bool:
        row = self.offense.get(team, {}).get("WR1")
        return bool(row) and norm_name(row["player_name"]) == norm_name(name)

    def script(self, game: dict) -> dict:
        """Vegas line -> a one-line read on game script."""
        fav, total, implied = game.get("fav_by"), game.get("total"), game.get("implied")
        if fav is None or total is None:
            return {"fav_by": None, "total": total, "implied": None, "read": "no line yet"}
        if fav >= HEAVY_FAVORITE:
            read = "heavy favourite -- run-leaning script, fewer late throws"
        elif fav <= -LIVE_DOG and total >= HIGH_TOTAL:
            read = "underdog in a high total -- pass-volume script"
        elif fav <= -HEAVY_FAVORITE:
            read = "big underdog -- garbage-time targets, capped team total"
        elif total >= HIGH_TOTAL:
            read = "high total -- shootout script"
        elif total <= LOW_TOTAL:
            read = "low total -- few scoring chances either way"
        else:
            read = "neutral script"
        return {"fav_by": fav, "total": total, "implied": implied, "read": read}

    def volume_line(self, name: str, pos: str) -> dict | None:
        v = self.volume.get((norm_name(name), pos))
        if not v:
            return None
        out = {}
        for yr, a in sorted(v.items()):
            if not a["g"]:
                continue
            out[yr] = {
                "g": a["g"], "tgt_g": a["tgt"] / a["g"], "rec_g": a["rec"] / a["g"],
                "yds_g": a["yds"] / a["g"], "carries_g": a["carries"] / a["g"], "ppr_g": a["ppr"] / a["g"],
                "share": (a["share"] / a["share_n"]) if a["share_n"] else None,
            }
        return out or None

    def evaluate(self, player: dict) -> dict:
        """player = {name, team, pos, spot?}. Returns a matchup record."""
        rec = dict(player)
        pos, team = player["pos"], player["team"]
        game = self.sched.get(team)
        rec.update(opp=None, defenders=[], others=[], team_line=None, shadow=False, score=None, role="")
        if pos not in ("QB", "RB", "WR", "TE"):
            rec["verdict"] = "N/A"
            return rec
        if not game:
            rec["verdict"] = "BYE"
            return rec
        opp = game["opp"]
        rec.update(opp=opp, home=game["home"], gameday=game["gameday"],
                   vegas=self.script(game), volume=self.volume_line(player["name"], pos))
        d = self.defense.get(opp, {})
        desc = self.idx.describe

        if pos == "QB":
            tl = self.team_line(opp, "pass", "QB")
            score = score_or_neutral(tl)
            rec.update(team_line=tl, score=score, verdict=verdict_for(score))
            return rec

        if pos == "WR":
            role = wr_role(player["name"], team, self.offense, self.overrides)
            boundary = [desc(d.get("LCB"), "LCB"), desc(d.get("RCB"), "RCB")]
            nickel = [desc(d.get("NB"), "NB")]
            primary, others = (nickel, boundary) if role == "slot" else (boundary, nickel)
            wr1 = self.is_wr1(player["name"], team)
            # A documented travel corner on the opponent, facing this team's
            # WR1 on the boundary: score him against that corner alone.
            traveller = next((c for c in boundary if norm_name(c["name"]) in self.shadow), None)
            if traveller and wr1 and role == "outside":
                primary = [traveller]
                others = [c for c in boundary if c is not traveller] + nickel
                rec["shadow"] = "travel"
                rec["shadow_note"] = (f"{traveller['name']} travels with WR1s -- "
                                      f"{self.shadow[norm_name(traveller['name'])].get('note', '')}")
            elif wr1 and any(c["grade"] == "A" for c in boundary):
                rec["shadow"] = "risk"
                rec["shadow_note"] = "A-grade boundary CB -- shadow coverage risk for the WR1"
            tl = self.team_line(opp, "pass", "WR")
        elif pos == "TE":
            role = ""
            primary = [desc(d.get("FS"), "FS"), desc(d.get("SS"), "SS")]
            others = [desc(d.get("NB"), "NB")]
            tl = self.team_line(opp, "pass", "TE")
        else:  # RB
            role = ""
            primary = [desc(r, r["pos_abb"]) for r in d.get("LB", [])] or [desc(None, "LB")]
            others = []
            tl = self.team_line(opp, "run", "RB")

        ind = sum(eff_score(c) for c in primary) / len(primary)
        w = BLEND[pos]
        score = w * ind + (1 - w) * score_or_neutral(tl)
        rec.update(role=role, defenders=primary, others=others, team_line=tl, individual=ind,
                   score=score, verdict=verdict_for(score))
        return rec


# ---------------------------------------------------------------------------
# Sleeper: my lineup and my opponent's
# ---------------------------------------------------------------------------

def sleeper_lineups(league_id: str, username: str, week: int, roster_by_sleeper: dict,
                    refresh: bool) -> tuple[dict, dict | None]:
    """Returns (mine, theirs); each is {team_name, owner, players:[{name, team, pos, spot}]}."""
    user = fetch_json(f"{SLEEPER}/user/{username}", refresh)
    league = fetch_json(f"{SLEEPER}/league/{league_id}", refresh)
    rosters = fetch_json(f"{SLEEPER}/league/{league_id}/rosters", refresh, ttl=300)
    users = {u["user_id"]: u for u in fetch_json(f"{SLEEPER}/league/{league_id}/users", refresh)}
    matchups = fetch_json(f"{SLEEPER}/league/{league_id}/matchups/{week}", refresh, ttl=300)
    slots = league.get("roster_positions", [])

    owner_of = {r["roster_id"]: r["owner_id"] for r in rosters}
    mine_id = next((r["roster_id"] for r in rosters if r["owner_id"] == user["user_id"]), None)
    if mine_id is None:
        sys.exit(f"{username} does not own a roster in league {league_id}")
    by_roster = {m["roster_id"]: m for m in matchups}
    my_m = by_roster.get(mine_id)
    opp_id = None
    if my_m and my_m.get("matchup_id") is not None:
        opp_id = next((rid for rid, m in by_roster.items()
                       if m.get("matchup_id") == my_m["matchup_id"] and rid != mine_id), None)

    players_blob = None

    def build(roster_id: int) -> dict:
        nonlocal players_blob
        m = by_roster.get(roster_id) or next(r for r in rosters if r["roster_id"] == roster_id)
        u = users.get(owner_of[roster_id], {})
        team_name = (u.get("metadata") or {}).get("team_name") or u.get("display_name", "?")
        starters = m.get("starters") or []
        spot_of = {pid: (slots[i] if i < len(slots) else "?") for i, pid in enumerate(starters) if pid and pid != "0"}
        out = []
        for pid in m.get("players") or []:
            ros = roster_by_sleeper.get(pid)
            if ros:
                name, team, pos = ros["full_name"], ros["team"], ros["position"]
            else:
                if players_blob is None:
                    players_blob = fetch_json(f"{SLEEPER}/players/nfl", refresh, ttl=7 * 86400)
                p = players_blob.get(pid, {})
                name, team, pos = p.get("full_name", pid), p.get("team") or "FA", p.get("position", "?")
            if pos in ("QB", "RB", "WR", "TE"):
                out.append({"name": name, "team": team, "pos": pos, "spot": spot_of.get(pid, "BN")})
        return {"team_name": team_name, "owner": u.get("display_name", "?"), "players": out}

    return build(mine_id), (build(opp_id) if opp_id is not None else None)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _recent_tag(c: dict) -> str:
    rs = c.get("recent_score")
    if rs is None or c.get("score") is None or abs(rs - c["score"]) < RECENT_DELTA:
        return ""
    yrs = c.get("recent_years") or []
    span = f"{yrs[0]}-{str(yrs[-1])[-2:]}" if len(yrs) > 1 else (str(yrs[0]) if yrs else "recent")
    return f" (recent {c['recent_grade']} {rs:.0f}, {span} only)"


def fmt_defender(c: dict, long: bool = False) -> str:
    inj = f" ⚕{c['injury']}" if c.get("injury") else ""
    if c["score"] is None:
        base = f"{c['name']} [{c['grade']}]{inj}"
        return f"{base} ({c['note']})" if c.get("note") else base
    s = f"{c['name']} [{c['grade']} {c['score']:.0f}]{inj}{_recent_tag(c)}"
    if long:
        s += (f" {c['yds_tgt']:.1f} yds/tgt, {c['rating']:.0f} rtg, {100 * c['cmp_pct']:.0f}% cmp, "
              f"{int(c['td'])} TD / {int(c['int'])} INT on {int(c['tgt'])} tgt")
    return s


def fmt_seasons(c: dict) -> str:
    return " | ".join(c.get("seasons") or [])


def fmt_team_line(tl: dict | None, opp: str, long: bool = False) -> str:
    if tl is None:
        return f"{opp} {'?'} (no team data)"
    s = f"{opp} {tl['kind']} D [{tl['grade']} {tl['unit_score']:.0f}, #{tl['rank']}]"
    if long:
        s += f" {tl['detail']}, {tl['fp']:.1f} PPR/g to {tl['pos']}s (#{tl['fp_rank']})"
    return s


def fmt_leaks(tl: dict | None) -> str:
    if not tl:
        return ""
    bits = []
    if tl.get("leaks"):
        bits.append("leaks to " + ", ".join(tl["leaks"]))
    if tl.get("holds"):
        bits.append("holds " + ", ".join(tl["holds"]))
    return "; ".join(bits)


def fmt_vegas(v: dict | None) -> str:
    if not v or v.get("fav_by") is None:
        if v and v.get("total") is not None:
            return f"total {v['total']:.1f}, no spread yet"
        return "no line yet"
    if v["fav_by"] > 0:
        side = f"favoured by {v['fav_by']:.1f}"
    elif v["fav_by"] < 0:
        side = f"underdog by {-v['fav_by']:.1f}"
    else:
        side = "pick'em"
    return f"{side}, total {v['total']:.1f}, implied {v['implied']:.1f} -- {v['read']}"


def fmt_volume(vol: dict | None, pos: str) -> str:
    if not vol:
        return "no prior-season data"
    parts = []
    for yr, a in vol.items():
        if pos == "RB":
            s = f"{yr}: {a['carries_g']:.1f} car/g, {a['tgt_g']:.1f} tgt/g, {a['ppr_g']:.1f} PPR/g ({a['g']} g)"
        elif pos == "QB":
            s = f"{yr}: {a['ppr_g']:.1f} PPR/g ({a['g']} g)"
        else:
            share = f", {100 * a['share']:.0f}% share" if a.get("share") is not None else ""
            s = (f"{yr}: {a['tgt_g']:.1f} tgt/g{share}, {a['rec_g']:.1f} rec, {a['yds_g']:.0f} yds, "
                 f"{a['ppr_g']:.1f} PPR/g ({a['g']} g)")
        parts.append(s)
    return " | ".join(parts)


def sort_key(r: dict):
    order = {"QB": 0, "RB": 1, "WR": 2, "TE": 3}
    starter = 0 if r.get("spot", "BN") != "BN" else 1
    return (starter, order.get(r["pos"], 9), -(r["score"] if r["score"] is not None else -1), r["name"])


def print_matchups(recs: list[dict], header: str, markdown: bool, out) -> None:
    recs = sorted(recs, key=sort_key)
    if markdown:
        out.write(f"## {header}\n\n")
        out.write("| Player | Pos | Tm | Spot | Opp | Verdict | Score | Individual matchup | Team defense | Script / volume |\n")
        out.write("|---|---|---|---|---|---|---|---|---|---|\n")
        for r in recs:
            spot = r.get("spot", "")
            if r["verdict"] in ("BYE", "N/A"):
                out.write(f"| {r['name']} | {r['pos']} | {r['team']} | {spot} | "
                          f"{'BYE' if r['verdict'] == 'BYE' else ''} | {verdict_icon(r['verdict'])} {r['verdict']} | | | | |\n")
                continue
            opp = ("vs " if r["home"] else "@ ") + r["opp"]
            ind = "<br>".join(f"{c['label']} {fmt_defender(c)}" for c in r["defenders"])
            if r["shadow"] == "travel":
                ind += "<br>🔒 shadowed by WR1 travel corner"
            elif r["shadow"]:
                ind += "<br>⚠ shadow risk"
            if r["role"]:
                ind = f"({r['role']}) " + ind
            team = fmt_team_line(r["team_line"], r["opp"])
            leaks = fmt_leaks(r["team_line"])
            if leaks:
                team += f"<br>{leaks}"
            ctx = f"{fmt_vegas(r.get('vegas'))}<br>{fmt_volume(r.get('volume'), r['pos'])}"
            out.write(f"| {r['name']} | {r['pos']} | {r['team']} | {spot} | {opp} | "
                      f"{verdict_icon(r['verdict'])} {r['verdict']} | {r['score']:.0f} | {ind} | "
                      f"{team} | {ctx} |\n")
        out.write("\n")
        return
    out.write(f"\n{header}\n{'=' * len(header)}\n")
    for r in recs:
        spot = f" ({r['spot']})" if r.get("spot") else ""
        if r["verdict"] == "BYE":
            out.write(f"\n⚫ {r['name']} {r['pos']} {r['team']}{spot}: BYE WEEK\n")
            continue
        if r["verdict"] == "N/A":
            continue
        opp = ("vs " if r["home"] else "@ ") + r["opp"]
        role = f", {r['role']}" if r["role"] else ""
        out.write(f"\n{verdict_icon(r['verdict'])} {r['name']} {r['pos']} {r['team']}{spot} {opp} -- "
                  f"{r['verdict']} (score {r['score']:.0f}{role})\n")
        out.write(f"     team D  : {fmt_team_line(r['team_line'], r['opp'], long=True)}\n")
        leaks = fmt_leaks(r["team_line"])
        if leaks:
            out.write(f"     points  : {leaks}\n")
        for c in r["defenders"]:
            out.write(f"     {c['label']:<8}: {fmt_defender(c, long=True)}\n")
            if c.get("seasons"):
                out.write(f"     {'':<8}  {fmt_seasons(c)}\n")
        for c in r["others"]:
            out.write(f"     also {c['label']:<3}: {fmt_defender(c, long=True)}\n")
        if r["shadow"]:
            out.write(f"     shadow  : {r.get('shadow_note', '')}\n")
        out.write(f"     vegas   : {fmt_vegas(r.get('vegas'))}\n")
        out.write(f"     volume  : {fmt_volume(r.get('volume'), r['pos'])}\n")


def print_defenses(team_def: dict, markdown: bool, out) -> None:
    rows = sorted(team_def.items(), key=lambda kv: -(kv[1]["pass_score"] + kv[1]["run_score"]))
    if markdown:
        out.write("| Team | Pass D | Yds/Att | EPA/db | Run D | Yds/Carry | EPA/carry | PPR/g vs QB | vs RB | vs WR | vs TE |\n")
        out.write("|---|---|---|---|---|---|---|---|---|---|---|\n")
        for t, v in rows:
            fp = v["fp"]; fr = v["fp_rank"]
            out.write(f"| {t} | {v['pass_grade']} {v['pass_score']:.0f} (#{v['pass_rank']}) | {v['yds_att']:.1f} | "
                      f"{v['epa_db']:+.2f} | {v['run_grade']} {v['run_score']:.0f} (#{v['run_rank']}) | "
                      f"{v['yds_carry']:.1f} | {v['epa_rush']:+.2f} | "
                      f"{fp['QB']:.1f} (#{fr['QB']}) | {fp['RB']:.1f} (#{fr['RB']}) | "
                      f"{fp['WR']:.1f} (#{fr['WR']}) | {fp['TE']:.1f} (#{fr['TE']}) |\n")
        return
    out.write(f"{'Team':<5}{'PassD':>7}{'Rk':>4}{'Y/A':>6}{'EPA/db':>8}{'RunD':>7}{'Rk':>4}{'Y/C':>6}{'EPA/c':>7}"
              f"{'vsQB':>7}{'vsRB':>7}{'vsWR':>7}{'vsTE':>7}\n")
    for t, v in rows:
        fp = v["fp"]
        out.write(f"{t:<5}{v['pass_grade'] + ' ' + format(v['pass_score'], '.0f'):>7}{v['pass_rank']:>4}"
                  f"{v['yds_att']:>6.1f}{v['epa_db']:>8.2f}{v['run_grade'] + ' ' + format(v['run_score'], '.0f'):>7}"
                  f"{v['run_rank']:>4}{v['yds_carry']:>6.1f}{v['epa_rush']:>7.2f}"
                  f"{fp['QB']:>7.1f}{fp['RB']:>7.1f}{fp['WR']:>7.1f}{fp['TE']:>7.1f}\n")


def print_cb_leaderboard(defense: dict, idx: DefenderIndex, markdown: bool, out) -> None:
    rows = []
    for team in sorted(defense):
        for pos in ("LCB", "RCB", "NB"):
            if defense[team].get(pos):
                rows.append((team, pos, idx.describe(defense[team][pos], pos)))
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


LEGEND = (
    "### Legend\n\n"
    "- **Score**: 0-100, higher = tougher matchup. QB = opponent pass D. WR = CBs (outside: average of the two "
    "boundary CBs; slot: nickel) blended {wr:.0%}/{wr_t:.0%} with the PPR the defense allows *to WRs*. TE = the "
    "two starting safeties blended {te:.0%}/{te_t:.0%} with PPR allowed to TEs. RB = off-ball LB coverage blended "
    "{rb:.0%}/{rb_t:.0%} with run D. Ungraded defenders count as 50.\n"
    "- **Verdict**: TOUGH at {tough}+, SOFT at {soft} or below, NEUTRAL between.\n"
    "- **Defender grade**: A-F from percentile within their position group on yards/target, passer rating and "
    "completion % allowed, pooled over three seasons. `?` = rookie or fewer than {min_tgt} weighted targets. "
    "A `recent` tag shows the last-two-seasons-only score when it differs; the matchup uses the average of the "
    "two. ⚕ = the defender carries an injury tag.\n"
    "- **Team defense**: grade, score and rank (#1 = toughest of 32) of the overall pass or run D, then where the "
    "points go: a defense *leaks to* positions it ranks {leak}th or worse against and *holds* those it ranks "
    "top {hold} against.\n"
    "- **🔒 Shadowed**: the opponent has a corner documented as travelling with WR1s (`shadow_corners.json`) and "
    "this is his team's WR1, so the matchup is scored against that corner alone. **⚠ Shadow risk**: an A-grade "
    "boundary CB who may travel.\n"
    "- **Script / volume**: the Vegas spread, total and implied team points with a one-line read on game "
    "script; then last season's targets per game, target share and PPR per game (this season is added as it "
    "accrues). Volume is the half of a matchup a corner cannot take away.\n"
)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--week", type=int, help="NFL week (default: current week from Sleeper)")
    ap.add_argument("--season", type=int, help="NFL season (default: current season from Sleeper)")
    ap.add_argument("--username", default=SLEEPER_USERNAME, help="Sleeper username")
    ap.add_argument("--league", default=SLEEPER_LEAGUE_ID, help="Sleeper league id")
    ap.add_argument("--all", action="store_true", help="grade every NFL team's QB1, RB1, WR1-WR3 and TE1")
    ap.add_argument("--player", action="append", default=[], help="grade a specific player (repeatable)")
    ap.add_argument("--defenses", action="store_true", help="print the team pass/run defense table")
    ap.add_argument("--cbs", action="store_true", help="print the starting-CB toughness leaderboard")
    ap.add_argument("--full", action="store_true",
                    help="weekly report: both lineups, every team's starters, defenses, CB leaderboard")
    ap.add_argument("--markdown", action="store_true", help="emit Markdown instead of plain text")
    ap.add_argument("-o", "--out", help="write output to this file")
    ap.add_argument("--out-dir", help="write output to <dir>/weekNN.md (or .txt), named for the week")
    ap.add_argument("--refresh", action="store_true", help="re-download everything, ignoring the cache")
    args = ap.parse_args(argv)

    state = load_state(args.refresh)
    season = args.season or int(state["season"])
    week = args.week or int(state.get("display_week") or state.get("week") or 1)

    week_sched = load_schedule(season, week, args.refresh)
    roster_by_gsis, roster_by_sleeper = load_rosters(season, args.refresh)
    defense, offense, snapshot = load_depth_charts(season, args.refresh)
    stats = load_defender_stats({season: 1.0, season - 1: 1.0, season - 2: 0.5}, args.refresh)
    team_def = load_team_defense(season, args.refresh)
    idx = DefenderIndex(stats, roster_by_gsis, load_sleeper_players(args.refresh))
    ev = Evaluator(week_sched, defense, offense, team_def, idx, load_overrides(),
                   shadow=load_shadow_corners(), volume=load_player_volume(season, args.refresh))

    def all_starters() -> list[dict]:
        out = []
        for team in sorted(offense):
            for slot in ("QB1", "RB1", "WR1", "WR2", "WR3", "TE1"):
                row = offense[team].get(slot)
                if row:
                    out.append({"name": row["player_name"], "team": team, "pos": slot[:2], "spot": slot})
        return out

    if args.out_dir:
        os.makedirs(args.out_dir, exist_ok=True)
        args.out = os.path.join(args.out_dir, f"week{week:02d}." + ("md" if args.markdown else "txt"))
    out = open(args.out, "w") if args.out else sys.stdout
    try:
        title = f"Matchups -- {season} week {week}"
        td_seasons = ", ".join(str(s) for s in next(iter(team_def.values()))["seasons"]) if team_def else "n/a"
        sub = (f"depth charts as of {snapshot}; defender grades pool PFR coverage stats from {season - 2} "
               f"(half weight), {season - 1} and {season}; team defense from {td_seasons}")
        out.write(f"# {title}\n\n_{sub}_\n\n" if args.markdown else f"{title}\n{sub}\n")

        if args.cbs:
            out.write("## Starting CB toughness leaderboard\n\n" if args.markdown else "\n")
            print_cb_leaderboard(defense, idx, args.markdown, out)
            return 0
        if args.defenses:
            out.write("## Team defenses\n\n" if args.markdown else "\n")
            print_defenses(team_def, args.markdown, out)
            return 0

        if args.player:
            players = []
            for name in args.player:
                m = next((r for r in roster_by_gsis.values() if r["position"] in ("QB", "RB", "WR", "TE")
                          and norm_name(r["full_name"]) == norm_name(name)), None)
                if m is None:
                    print(f"warning: could not find '{name}' on any {season} roster", file=sys.stderr)
                    continue
                players.append({"name": m["full_name"], "team": m["team"], "pos": m["position"]})
            print_matchups([ev.evaluate(p) for p in players], "Requested players", args.markdown, out)
            return 0

        if args.all:
            print_matchups([ev.evaluate(p) for p in all_starters()], "All NFL starters", args.markdown, out)
            return 0

        mine, theirs = sleeper_lineups(args.league, args.username, week, roster_by_sleeper, args.refresh)
        print_matchups([ev.evaluate(p) for p in mine["players"]],
                       f"{mine['team_name']} ({mine['owner']}) -- my lineup", args.markdown, out)
        if theirs:
            print_matchups([ev.evaluate(p) for p in theirs["players"]],
                           f"{theirs['team_name']} ({theirs['owner']}) -- this week's opponent", args.markdown, out)
        else:
            out.write("\n(no head-to-head opponent found for this week)\n\n")

        if args.full:
            print_matchups([ev.evaluate(p) for p in all_starters()],
                           "All NFL starters (waiver / trade targets)", args.markdown, out)
            out.write("## Team defenses\n\n" if args.markdown else "\nTeam defenses\n=============\n")
            print_defenses(team_def, args.markdown, out)
            out.write("\n## Starting CB toughness leaderboard\n\n" if args.markdown
                      else "\nStarting CB toughness leaderboard\n=================================\n")
            print_cb_leaderboard(defense, idx, args.markdown, out)
            out.write("\n")

        if args.markdown:
            out.write(LEGEND.format(wr=BLEND["WR"], wr_t=1 - BLEND["WR"], te=BLEND["TE"], te_t=1 - BLEND["TE"],
                                    rb=BLEND["RB"], rb_t=1 - BLEND["RB"], tough=TOUGH_SCORE, soft=SOFT_SCORE,
                                    min_tgt=MIN_TARGETS, leak=LEAK_RANK, hold=HOLD_RANK))
        return 0
    finally:
        if args.out:
            out.close()


if __name__ == "__main__":
    sys.exit(main())
