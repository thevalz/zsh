"""Broad-sourced consensus ranks: the prior that production is blended into.

Sleeper's `search_rank` is one site's opinion with no visible update cadence.
This module builds the prior from three independent lists instead, each
re-ranked among skill players only so they live on one scale:

- FantasyPros rest-of-season PPR expert consensus (ECR). The live page is
  used when enough experts have submitted this week; early in the week it can
  carry two, so below FP_MIN_EXPERTS we fall back to the DynastyProcess
  mirror, which is scraped weekly with the full expert set.
- ESPN season projections, which are injury-adjusted in season.
- RotoWire season projections, served through Sleeper's projection feed.

IDs are reconciled through the DynastyProcess crosswalk (FantasyPros, ESPN
and Sleeper ids for every player). Each source is fetched read-only from a
public endpoint; nothing needs a key. A source that fails is *reported*, not
silently skipped -- the report header names what the prior was built from.
"""

from __future__ import annotations

import csv
import io
import json
import re
import urllib.error
import urllib.request

from . import config, sleeper

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
ESPN_POSITIONS = {1: "QB", 2: "RB", 3: "WR", 4: "TE"}
SLEEPER_PROJ_URL = (
    "https://api.sleeper.com/projections/nfl/{season}?season_type=regular"
    "&position%5B%5D=QB&position%5B%5D=RB&position%5B%5D=WR&position%5B%5D=TE&order_by=pts_ppr"
)

_CONSENSUS: dict | None = None
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


def _rerank(scored: dict, reverse: bool) -> dict:
    """Dense 1..N ranks from {sleeper_id: metric}. reverse=True ranks high metric first."""
    order = sorted(scored.items(), key=lambda kv: -kv[1] if reverse else kv[1])
    return {sid: i for i, (sid, _) in enumerate(order, start=1)}


def _fantasypros(xw: dict, skill: dict) -> tuple[dict, str]:
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
        return _rerank(scored, reverse=False), f"FantasyPros ROS ECR live ({experts} experts, {data.get('last_updated')})"
    # Too few experts on the live page: use the weekly mirror's full set.
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
    return _rerank(scored, reverse=False), (
        f"FantasyPros ROS ECR via DynastyProcess mirror (scraped {scraped}; "
        f"live page had only {experts} experts)"
    )


def _espn(xw: dict, skill: dict, season: str) -> tuple[dict, str]:
    data = _fetch(ESPN_URL.format(season=season), f"espn_kona_{season}",
                  config.CACHE_TTL["projections"], headers={"X-Fantasy-Filter": ESPN_FILTER})
    scored = {}
    for entry in data.get("players", []):
        p = entry.get("player") or {}
        sid = xw["espn"].get(str(p.get("id")))
        if sid not in skill:
            continue
        for s in p.get("stats", []):
            # statSourceId 1 = projection; scoringPeriodId 0 = full season.
            # The payload also carries last season's projection under the same
            # ids, so the season must match or a 2025 line masquerades as 2026.
            if (s.get("statSourceId") == 1 and s.get("scoringPeriodId") == 0
                    and str(s.get("seasonId")) == str(season)):
                scored[sid] = float(s.get("appliedTotal") or 0.0)
                break
    scored = {k: v for k, v in scored.items() if v > 0}
    if not scored:
        raise ValueError("ESPN returned no season projections")
    return _rerank(scored, reverse=True), f"ESPN season projections ({len(scored)} players)"


def _rotowire(skill: dict, season: str) -> tuple[dict, str]:
    rows = _fetch(SLEEPER_PROJ_URL.format(season=season), f"sleeper_proj_{season}",
                  config.CACHE_TTL["projections"])
    scored = {}
    for r in rows:
        sid = r.get("player_id")
        pts = ((r.get("stats") or {}).get("pts_ppr") or 0.0)
        if sid in skill and pts > 0:
            scored[sid] = float(pts)
    if not scored:
        raise ValueError("Sleeper projection feed was empty")
    return _rerank(scored, reverse=True), f"RotoWire season projections via Sleeper ({len(scored)} players)"


def consensus_ranks(force: bool = False) -> dict:
    """{sleeper_id: {"rank": mean rank, "sources": {name: rank}}} across skill players.

    A player's consensus rank is the mean of his rank in every source that
    lists him. Sources are weighted by config.PRIOR_SOURCES; a source that
    cannot be fetched is dropped and named in meta()["failed"].
    """
    global _CONSENSUS
    if _CONSENSUS is not None and not force:
        return _CONSENSUS
    players = sleeper.players()
    skill = {pid: p for pid, p in players.items()
             if p.get("team") and p.get("position") in config.SKILL_POSITIONS}
    season = str((sleeper.nfl_state() or {}).get("season") or config.SEASON)
    used, failed, lists = [], [], {}
    try:
        xw = _crosswalk()
    except Exception as err:  # noqa: BLE001
        xw = None
        failed.append(f"id crosswalk ({err})")
    builders = {
        "fantasypros": (lambda: _fantasypros(xw, skill)) if xw else None,
        "espn": (lambda: _espn(xw, skill, season)) if xw else None,
        "rotowire": lambda: _rotowire(skill, season),
    }
    for name, weight in config.PRIOR_SOURCES.items():
        build = builders.get(name)
        if not build or weight <= 0:
            continue
        try:
            ranks, label = build()
            lists[name] = (ranks, weight)
            used.append(label)
        except Exception as err:  # noqa: BLE001
            failed.append(f"{name} ({err})")
    out: dict = {}
    for name, (ranks, weight) in lists.items():
        for sid, rank in ranks.items():
            row = out.setdefault(sid, {"sources": {}, "_w": 0.0, "_sum": 0.0})
            row["sources"][name] = rank
            row["_w"] += weight
            row["_sum"] += weight * rank
    for row in out.values():
        row["rank"] = row.pop("_sum") / row.pop("_w")
    _META.update(used=used, failed=failed, players=len(out))
    _CONSENSUS = out
    return out


def meta() -> dict:
    consensus_ranks()
    return dict(_META)
