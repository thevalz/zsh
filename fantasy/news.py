"""National news scanning, filtered down to players this league cares about."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

FEEDS = [
    "https://site.api.espn.com/apis/site/v2/sports/football/nfl/news?limit=50",
]
USER_AGENT = "zebras-waiver-monitor/1.0"


def _fetch(url: str):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except (urllib.error.URLError, TimeoutError, ValueError):
        return {}


def _articles():
    out = []
    for url in FEEDS:
        data = _fetch(url)
        for a in data.get("articles", []) or []:
            out.append(
                {
                    "headline": a.get("headline") or "",
                    "description": a.get("description") or "",
                    "published": a.get("published") or "",
                    "link": ((a.get("links") or {}).get("web") or {}).get("href", ""),
                    "athletes": [
                        c.get("description")
                        for c in (a.get("categories") or [])
                        if c.get("type") == "athlete" and c.get("description")
                    ],
                }
            )
    return out


INJURY_WORDS = re.compile(
    r"\b(injur|hurt|out |ruled out|questionable|doubtful|IR\b|placed on|MRI|"
    r"strain|sprain|concussion|hamstring|ankle|knee|surgery|carted|"
    r"activated|return|starter|start(?:ing)? (?:at|job)|snap|workload|"
    r"benched|demot|promot|trade|release|waive|suspend|inactive|limited)\b",
    re.IGNORECASE,
)


def relevant_news(names_of_interest: dict, hours: int = 24, limit: int = 20) -> list:
    """Return recent articles that name a player we track.

    `names_of_interest` maps a player's full name to a short context label
    ("my roster", "free agent — Skattebo handcuff", ...) so the alert can say
    why the story matters instead of just relaying a headline.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    hits = []
    for art in _articles():
        try:
            when = datetime.fromisoformat(art["published"].replace("Z", "+00:00"))
        except ValueError:
            when = datetime.now(timezone.utc)
        if when < cutoff:
            continue
        blob = f"{art['headline']} {art['description']} {' '.join(art['athletes'])}"
        matched = [n for n in names_of_interest if n and n in blob]
        if not matched:
            continue
        hits.append(
            {
                "headline": art["headline"],
                "link": art["link"],
                "published": art["published"],
                "players": matched,
                "context": [names_of_interest[n] for n in matched],
                "injury_flavored": bool(INJURY_WORDS.search(blob)),
            }
        )
    hits.sort(key=lambda h: (not h["injury_flavored"], h["published"]), reverse=False)
    hits.sort(key=lambda h: h["injury_flavored"], reverse=True)
    return hits[:limit]
