"""Thin, cached client for the public Sleeper API (no auth required)."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from . import config

BASE = "https://api.sleeper.app/v1"
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")
USER_AGENT = "zebras-waiver-monitor/1.0"


def _cache_path(key: str) -> str:
    return os.path.join(CACHE_DIR, key.replace("/", "_") + ".json")


def _read_cache(key: str, ttl: int):
    path = _cache_path(key)
    try:
        age = time.time() - os.path.getmtime(path)
    except OSError:
        return None
    if age > ttl:
        return None
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _write_cache(key: str, payload) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = _cache_path(key) + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(payload, fh)
    os.replace(tmp, _cache_path(key))


def get(path: str, cache_key: str | None = None, ttl: int = 900, retries: int = 3):
    """GET a Sleeper endpoint, serving from disk cache when it is still fresh.

    On a network failure we fall back to stale cache rather than blowing up --
    an hourly monitor that dies on one flaky request is worse than one that
    reports slightly old data and says so.
    """
    if cache_key:
        hit = _read_cache(cache_key, ttl)
        if hit is not None:
            return hit

    url = f"{BASE}/{path.lstrip('/')}"
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=45) as resp:
                payload = json.load(resp)
            if cache_key:
                _write_cache(cache_key, payload)
            return payload
        except (urllib.error.URLError, TimeoutError, ValueError) as err:
            last_err = err
            time.sleep(2 ** attempt)

    if cache_key:
        stale = _read_cache(cache_key, ttl=10 ** 9)
        if stale is not None:
            return stale
    raise RuntimeError(f"Sleeper request failed: {url} ({last_err})")


def nfl_state():
    return get("state/nfl", "state", config.CACHE_TTL["state"])


def league(league_id: str = config.LEAGUE_ID):
    return get(f"league/{league_id}", "league", config.CACHE_TTL["league"])


def rosters(league_id: str = config.LEAGUE_ID):
    return get(f"league/{league_id}/rosters", "rosters", config.CACHE_TTL["rosters"])


def users(league_id: str = config.LEAGUE_ID):
    return get(f"league/{league_id}/users", "users", config.CACHE_TTL["users"])


def players():
    """The full NFL player dictionary (~15MB). Cached hard -- it changes slowly."""
    return get("players/nfl", "players", config.CACHE_TTL["players"])


def trending(kind: str = "add", lookback_hours: int = 24, limit: int = 200):
    path = f"players/nfl/trending/{kind}?lookback_hours={lookback_hours}&limit={limit}"
    return get(path, f"trending_{kind}_{lookback_hours}", config.CACHE_TTL["trending"])


def transactions(week: int, league_id: str = config.LEAGUE_ID):
    return get(f"league/{league_id}/transactions/{week}", f"tx_{week}", 600)
