"""National news scanning, filtered down to players this league cares about."""

from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

FEEDS = [
    "https://site.api.espn.com/apis/site/v2/sports/football/nfl/news?limit=50",
]
USER_AGENT = "zebras-waiver-monitor/1.0"
# RotoWire serves its player pages only to browser-shaped clients.
BROWSER_UA = "Mozilla/5.0 (compatible; zebras-waiver-monitor/1.0)"


def _fetch_text(url: str) -> str:
    """Raw body of a page, or "" if anything goes wrong. Never raises."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf8", "ignore")
    except (urllib.error.URLError, TimeoutError, ValueError):
        return ""


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


# ---------------------------------------------------------------------------
# Per-player lookup
#
# The national feeds answer "what happened in the NFL today". They cannot
# answer "how bad is it", which is the only question that matters once a
# designation changes. RotoWire keeps a per-player blurb history -- headline,
# body, beat-writer attribution, date -- keyed by an id Sleeper already carries
# for 796 of 813 skill players, so we look a player up by name rather than
# trying to catch him going past in a five-item firehose.
# ---------------------------------------------------------------------------

PLAYER_URL = "https://www.rotowire.com/football/player.php?id={rotowire_id}"

_HEADLINE = re.compile(r'class="news-update__headline[^"]*"[^>]*>(.*?)<', re.S)
_BODY = re.compile(r'class="news-update__news[^"]*"[^>]*>(.*?)</div>', re.S)
_STAMP = re.compile(r'news-update__timestamp[^>]*>([^<]+)')

# Language that means "this is not a real injury".
TRIVIAL = re.compile(
    r"\b(cramp|cramping|precaution|precautionary|veteran (?:day|rest)|rest day|"
    r"maintenance|load management|not injury[- ]related|personal reasons|"
    r"illness|scheduled day off)\b", re.IGNORECASE)

# Language that means it is.
SERIOUS = re.compile(
    r"\b(MRI|torn|tear|rupture|fracture|broken|surgery|out (?:for )?(?:the )?season|"
    r"placed on injured reserve|injured reserve|IR\b|multiple weeks|week[- ]to[- ]week|"
    r"weeks?\b|carted|crutches|walking boot|sprain|strain|concussion protocol)\b",
    re.IGNORECASE)

# Practice participation is the single best predictor of Sunday availability.
PRACTICE = [
    (re.compile(r"\b(did ?n[o']?t practice|non[- ]participant|missed (?:Wednesday|Thursday|"
                r"Friday|Saturday|Sunday|the)?\s*practice|out of practice)\b", re.I), "DNP"),
    (re.compile(r"\blimited (?:participant|practice|in practice)\b", re.I), "LIMITED"),
    (re.compile(r"\b(full (?:participant|practice|go)|practiced fully|"
                r"no limitations)\b", re.I), "FULL"),
]


def _strip(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", fragment)).replace("\xa0", " ").strip()


def player_news(rotowire_id, limit: int = 4) -> list:
    """Recent blurbs for one player, newest first. Empty list on any failure."""
    if not rotowire_id:
        return []
    raw = _fetch_text(PLAYER_URL.format(rotowire_id=rotowire_id))
    if not raw:
        return []
    heads = [_strip(x) for x in _HEADLINE.findall(raw)]
    bodies = [_strip(x) for x in _BODY.findall(raw)]
    stamps = [x.strip() for x in _STAMP.findall(raw)]
    out = []
    for i in range(min(len(heads), len(bodies), limit)):
        out.append({
            "headline": heads[i],
            "body": re.sub(r"\s*Visit RotoWire\.com.*$", "", bodies[i]).strip(),
            "date": stamps[i] if i < len(stamps) else "",
        })
    return out


def assess(blurbs: list) -> dict:
    """Read the prose for the two things a designation cannot tell you:
    how serious it sounds, and whether he practiced."""
    text = " ".join(f"{b['headline']} {b['body']}" for b in blurbs[:2])
    practice = ""
    for pattern, label in PRACTICE:
        if pattern.search(text):
            practice = label
            break
    trivial = bool(TRIVIAL.search(text))
    serious = bool(SERIOUS.search(text))
    if serious:
        verdict = "serious"
    elif trivial:
        verdict = "likely minor"
    else:
        verdict = "unclear"
    return {
        "verdict": verdict,
        "practice": practice,
        "headline": blurbs[0]["headline"] if blurbs else "",
        "body": blurbs[0]["body"] if blurbs else "",
        "date": blurbs[0]["date"] if blurbs else "",
    }
