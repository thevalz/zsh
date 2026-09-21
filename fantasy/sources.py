"""Outside priors: what the rest of the world expects for the rest of the season.

Only lists that move during the season are used, because a preseason number
that never updates is the stale-rank problem in a different coat. Each source
is turned into *weighted rest-of-season points* over the same horizon the
model projects (playoff weeks count extra), so the prior and our own estimate
live on one scale:

- FantasyPros rest-of-season PPR expert consensus (ECR), re-ranked weekly.
  The live page is used when enough experts have submitted; early in the week
  it can carry two, so below FP_MIN_EXPERTS the DynastyProcess mirror (scraped
  weekly with the full set) is used instead. A rank, so it is converted to
  points through the ladder the other sources define.
- ESPN's week-by-week projections, summed over the remaining weeks. They are
  injury-adjusted in season (a player on IR projects to zero until he is back).
- Sleeper's week-by-week projections, summed the same way under this league's
  scoring settings.

Sleeper's static season-total feed was dropped: its numbers were preseason and
never moved. A source whose payload has not changed in SOURCE_STALE_DAYS is
flagged static and given weight 0, and the report header says so. A source
that cannot be fetched is named, never silently skipped.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import urllib.request
from datetime import date

from . import config, sleeper
from .usage import league_points

BROWSER_UA = "Mozilla/5.0 (compatible; zebras-waiver-monitor/1.0)"

FP_ROS_URL = "https://www.fantasypros.com/nfl/rankings/ros-ppr-overall.php"
DP_ECR_URL = "https://raw.githubusercontent.com/dynastyprocess/data/master/files/db_fpecr_latest.csv"
DP_IDS_URL = "https://raw.githubusercontent.com/dynastyprocess/data/master/files/db_playerids.csv"
ESPN_URL = (
    "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}"
    "/segments/0/leaguedefaults/3?view=kona_player_info"
)
ESPN_FILTER = json.dumps({"players": {
    "limit": 1500,
    "sortPercOwned": {"sortAsc": False, "sortPriority": 1},
    "filterSlotIds": {"value": [0, 2, 4, 6]},   # QB, RB, WR, TE
}})

_PRIOR: dict | None = None
_PRIOR_KEY: tuple | None = None
_META: dict = {}


def _fetch(url: str, key: str, ttl: int, headers: dict | None = None, text: bool = False):
    """GET with the shared disk cache. Raises on failure so callers can report it."""
    hit = sleeper._read_cache(key, ttl)
    if hit is not None:
        return hit
    req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = resp.read().decode("utf8", "ignore")
    payload = body if text else json.loads(body)
    sleeper._write_cache(key, payload)
    return payload


def _freshness(name: str, fingerprint: str) -> tuple[bool, str]:
    """(static, since): has this source's content changed within SOURCE_STALE_DAYS?"""
    key = f"seen_{name}"
    seen = sleeper._read_cache(key, 10 ** 9) or {}
    today = date.today().isoformat()
    if seen.get("hash") != fingerprint:
        sleeper._write_cache(key, {"hash": fingerprint, "since": today})
        return False, today
    since = seen.get("since") or today
    age = (date.today() - date.fromisoformat(since)).days
    return age > config.SOURCE_STALE_DAYS, since


def _fp_hash(obj) -> str:
    return hashlib.sha1(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def _crosswalk() -> dict:
    """{fantasypros_id: sleeper_id}, {espn_id: sleeper_id}."""
    body = _fetch(DP_IDS_URL, "dp_playerids", config.CACHE_TTL["crosswalk"], text=True)
    fp, espn = {}, {}
    for row in csv.DictReader(io.StringIO(body)):
        sid = row.get("sleeper_id") or ""
        if sid in ("", "NA"):
            continue
        if row.get("fantasypros_id") not in ("", "NA", None):
            fp[row["fantasypros_id"]] = sid
        if row.get("espn_id") not in ("", "NA", None):
            espn[row["espn_id"]] = sid
    return {"fp": fp, "espn": espn}


# ---------------------------------------------------------------------------
# sources
# ---------------------------------------------------------------------------

def _fantasypros(xw: dict, skill: dict) -> tuple[dict, str, str]:
    """({sid: ECR rank}, label, fingerprint)."""
    page = _fetch(FP_ROS_URL, "fp_ros_ppr_overall", config.CACHE_TTL["fantasypros"], text=True)
    m = re.search(r"var ecrData = (\{.*?\});", page, re.S)
    if not m:
        raise ValueError("FantasyPros page had no ecrData block")
    data = json.loads(m.group(1))
    experts = int(data.get("total_experts") or 0)
    if experts >= config.FP_MIN_EXPERTS:
        scored = {}
        for p in data.get("players", []):
            sid = xw["fp"].get(str(p.get("player_id")))
            if sid in skill and p.get("rank_ecr"):
                scored[sid] = float(p["rank_ecr"])
        return (scored, f"FantasyPros ROS ECR live ({experts} experts, {data.get('last_updated')})",
                _fp_hash(scored))
    body = _fetch(DP_ECR_URL, "dp_fpecr_latest", config.CACHE_TTL["fantasypros_mirror"], text=True)
    scored, scraped = {}, ""
    for row in csv.DictReader(io.StringIO(body)):
        if row.get("ecr_type") != "ro":
            continue
        sid = xw["fp"].get(row.get("id") or "")
        if sid in skill and row.get("ecr"):
            scored[sid] = float(row["ecr"])
            scraped = row.get("scrape_date") or scraped
    if not scored:
        raise ValueError("FantasyPros mirror had no ROS rows")
    return (scored, f"FantasyPros ROS ECR via DynastyProcess mirror (scraped {scraped}; "
                    f"live page had only {experts} experts)", _fp_hash(scored))


def _espn(xw: dict, skill: dict, season: str, weeks: list, weight) -> tuple[dict, str, str]:
    """({sid: weighted ROS points}, label, fingerprint) from ESPN's weekly projections."""
    data = _fetch(ESPN_URL.format(season=season), f"espn_kona_{season}",
                  config.CACHE_TTL["projections"], headers={"X-Fantasy-Filter": ESPN_FILTER})
    wanted = set(weeks)
    scored = {}
    for entry in data.get("players", []):
        p = entry.get("player") or {}
        sid = xw["espn"].get(str(p.get("id")))
        if sid not in skill:
            continue
        total = 0.0
        found = False
        for s in p.get("stats", []):
            if (s.get("statSourceId") == 1 and str(s.get("seasonId")) == str(season)
                    and s.get("scoringPeriodId") in wanted):
                total += float(s.get("appliedTotal") or 0.0) * weight(int(s["scoringPeriodId"]))
                found = True
        if found and total > 0:
            scored[sid] = total
    if not scored:
        raise ValueError("ESPN returned no weekly projections for the horizon")
    return scored, f"ESPN weekly projections summed over weeks {weeks[0]}–{weeks[-1]} ({len(scored)} players)", _fp_hash(scored)


def _sleeper(skill: dict, season: str, weeks: list, weight, scoring: dict) -> tuple[dict, str, str]:
    """({sid: weighted ROS league points}, label, fingerprint) from Sleeper's weekly projections."""
    scored: dict = {}
    got = 0
    for wk in weeks:
        try:
            lines = sleeper.weekly_projections(season, wk) or {}
        except RuntimeError:
            continue
        if not lines:
            continue
        got += 1
        for sid, line in lines.items():
            if sid in skill:
                scored[sid] = scored.get(sid, 0.0) + league_points(line, scoring) * weight(wk)
    scored = {k: v for k, v in scored.items() if v > 0}
    if not got or not scored:
        raise ValueError("Sleeper weekly projections were empty for the horizon")
    return scored, f"Sleeper weekly projections summed over {got} remaining weeks ({len(scored)} players)", _fp_hash(scored)


def _ladder(points_lists: list) -> list:
    """Descending points implied by overall rank, from the mean of the point sources."""
    if not points_lists:
        return []
    merged: dict = {}
    for pts in points_lists:
        for sid, v in pts.items():
            merged.setdefault(sid, []).append(v)
    return sorted((sum(v) / len(v) for v in merged.values()), reverse=True)


# ---------------------------------------------------------------------------
# the prior
# ---------------------------------------------------------------------------

def prior(season: str, weeks: list, weight, scoring: dict, players: dict,
          only: str | None = None, force: bool = False) -> dict:
    """{sleeper_id: {"ros": weighted ROS points, "sources": {name: points}}}.

    `weight(week)` is the horizon weighting (playoff weeks count extra), so the
    prior is on the same scale as the model's own projection. `only` restricts
    the prior to one named source, for the backtest.
    """
    global _PRIOR, _PRIOR_KEY
    key = (season, tuple(weeks), only)
    if _PRIOR is not None and _PRIOR_KEY == key and not force:
        return _PRIOR
    skill = {pid: p for pid, p in players.items()
             if p.get("team") and p.get("position") in config.SKILL_POSITIONS}
    used, failed, static, weights = [], [], [], {}
    try:
        xw = _crosswalk()
    except Exception as err:  # noqa: BLE001
        xw = None
        failed.append(f"id crosswalk ({err})")

    points: dict = {}          # name -> {sid: pts}
    fp_ranks: dict | None = None
    for name, w in config.PRIOR_SOURCES.items():
        # A rank needs a points ladder to convert through, so when FantasyPros
        # is scored alone the point sources are still fetched, at weight 0.
        ladder_only = bool(only) and name != only and only == "fantasypros" and name != "fantasypros"
        if only and name != only and not ladder_only:
            continue
        if w <= 0 and not only:
            continue
        try:
            if name == "fantasypros":
                if not xw:
                    raise ValueError("no id crosswalk")
                fp_ranks, label, fingerprint = _fantasypros(xw, skill)
            elif name == "espn":
                if not xw:
                    raise ValueError("no id crosswalk")
                points[name], label, fingerprint = _espn(xw, skill, season, weeks, weight)
            elif name == "sleeper":
                points[name], label, fingerprint = _sleeper(skill, season, weeks, weight, scoring)
            else:
                continue
        except Exception as err:  # noqa: BLE001
            failed.append(f"{name} ({err})")
            continue
        is_static, since = _freshness(name, fingerprint)
        if is_static:
            static.append(f"{name} (unchanged since {since})")
            weights[name] = 0.0
            if name == "fantasypros":
                fp_ranks = None
            else:
                points.pop(name, None)
            continue
        if ladder_only:
            weights[name] = 0.0
            continue
        weights[name] = 1.0 if only else w
        used.append(label)

    # FantasyPros is a rank; convert through the ladder the point sources define.
    if fp_ranks is not None:
        ladder = _ladder(list(points.values()))
        if ladder:
            points["fantasypros"] = {
                sid: ladder[min(int(round(r)) - 1, len(ladder) - 1)] for sid, r in fp_ranks.items() if r >= 1
            }
        else:
            failed.append("fantasypros (rank only; no point source to convert through)")
            weights.pop("fantasypros", None)

    out: dict = {}
    for name, pts in points.items():
        w = weights.get(name, 0.0)
        if w <= 0:
            continue
        for sid, v in pts.items():
            row = out.setdefault(sid, {"sources": {}, "_w": 0.0, "_sum": 0.0})
            row["sources"][name] = round(v, 1)
            row["_w"] += w
            row["_sum"] += w * v
    for row in out.values():
        row["ros"] = row.pop("_sum") / row.pop("_w")
    _META.update(used=used, failed=failed, static=static, weights=weights, players=len(out))
    _PRIOR, _PRIOR_KEY = out, key
    return out


def meta() -> dict:
    return dict(_META)
