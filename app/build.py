#!/usr/bin/env python3
"""Build the live draft console into a single self-contained HTML file.

    python3 app/build.py                 # refresh ADP, rebuild dist
    python3 app/build.py --no-fetch      # rebuild from cached data/
    python3 app/build.py --refresh-ids   # also rebuild the Sleeper id map

Re-run this the morning of the draft so the ADP is current — that is the whole
point of having a build step rather than hand-edited data.
"""
import argparse
import datetime
import json
import os
import re
import sys
import time
import unicodedata
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
DIST = os.path.join(HERE, "dist")
DOCS = os.path.join(ROOT, "docs")

ADP_URL = "https://fantasyfootballcalculator.com/api/v1/adp/2qb?teams=12&year=2026&position=all"
PPR_URL = "https://fantasyfootballcalculator.com/api/v1/adp/ppr?teams=12&year=2026&position=all"
TREND_URL = "https://api.sleeper.app/v1/players/nfl/trending/add?lookback_hours=48&limit=300"
PROJ_URL = ("https://www.fftoday.com/rankings/playerproj.php"
            "?Season=2026&PosID={pos}&LeagueID=1&cur_page={page}")
# FFToday column order after the name link, per position. Validated by
# recomputing their own FPts column: RB, WR and TE reproduce it exactly.
PROJ_COLS = {10: ("QB", ["cmp", "att", "pyd", "ptd", "int", "ra", "ry", "rtd", "fp"]),
             20: ("RB", ["ra", "ry", "rtd", "rec", "recy", "rectd", "fp"]),
             30: ("WR", ["rec", "recy", "rectd", "ra", "ry", "rtd", "fp"]),
             40: ("TE", ["rec", "recy", "rectd", "fp"])}
HISTORY_DAYS = 10   # window for ADP velocity
SLEEPER_PLAYERS = "https://api.sleeper.app/v1/players/nfl"
# concatenation order matters: later files call into earlier ones
SRC_ORDER = ["state.js", "model.js", "sleeper.js", "ui.js"]


def get(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": "zebras-draft-build/1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[.'’]", "", s.lower())
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _row(p):
    return {"n": p["name"], "p": p["position"], "t": p["team"],
            "a": p["adp"], "b": p["bye"], "sd": p["stdev"]}


def _interp(x, xs, ys):
    """Monotone piecewise-linear lookup; clamps outside the fitted range."""
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        # extrapolate along the last segment so deep players keep spreading out
        if len(xs) > 1 and xs[-1] > xs[-2]:
            slope = (ys[-1] - ys[-2]) / (xs[-1] - xs[-2])
            return ys[-1] + slope * (x - xs[-1])
        return ys[-1]
    lo = 0
    while lo + 1 < len(xs) and xs[lo + 1] < x:
        lo += 1
    x0, x1, y0, y1 = xs[lo], xs[lo + 1], ys[lo], ys[lo + 1]
    return y0 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def fetch_adp():
    """Superflex ADP, extended with the deeper 1QB list.

    The 2QB feed only ranks ~240 players, but a 12-team 16-round draft makes
    192 picks — so the board runs dry in the late rounds exactly when you still
    need it. The PPR feed goes deeper, so anyone missing from the 2QB list gets
    a superflex ADP estimated from the two lists' overlap. Quarterbacks are
    excluded from that fit: they are precisely the players the two formats
    disagree about, so they would poison the mapping.
    """
    sf = get(ADP_URL)
    meta = sf["meta"]
    players = [_row(p) for p in sf["players"]]
    have = {p["n"] for p in players}
    src = "FFC 2QB ADP, {} drafts, {} to {}".format(
        meta["total_drafts"], meta["start_date"], meta["end_date"])

    try:
        ppr = get(PPR_URL)
    except Exception as e:                     # deeper pool is a bonus, not a requirement
        print(f"  depth    skipped, PPR feed unavailable ({e})")
        return players, src

    sf_by_name = {p["name"]: p["adp"] for p in sf["players"] if p["position"] != "QB"}
    pairs = sorted((p["adp"], sf_by_name[p["name"]])
                   for p in ppr["players"]
                   if p["position"] != "QB" and p["name"] in sf_by_name)
    if len(pairs) < 20:
        print("  depth    skipped, not enough overlap to map PPR onto superflex")
        return players, src
    xs = [a for a, _ in pairs]
    ys = [b for _, b in pairs]

    added = 0
    for p in ppr["players"]:
        if p["name"] in have:
            continue
        r = _row(p)
        r["a"] = round(_interp(p["adp"], xs, ys), 1)
        r["est"] = 1                            # value inferred, not observed
        players.append(r)
        added += 1

    players.sort(key=lambda p: p["a"])
    print(f"  depth    +{added} from the PPR list "
          f"(mapped through {len(pairs)} players in both) -> {len(players)} total")
    return players, src


def build_id_map(players):
    """Sleeper player_id -> our player name, for the pool only.

    The full Sleeper player file is ~14 MB; we keep only what we can draft so
    the app never downloads it at runtime.
    """
    sleeper = get(SLEEPER_PLAYERS, timeout=300)
    idx = {}
    for pid, v in sleeper.items():
        pos = v.get("position")
        full = v.get("full_name")
        if full and pos:
            idx.setdefault((norm(full), pos), pid)
        if pos == "DEF":
            idx.setdefault(("def:" + (v.get("team") or ""), "DEF"), pid)
    exp_by_pid = {pid: v.get("years_exp") for pid, v in sleeper.items()}
    out, meta, missing = {}, {}, []
    for p in players:
        pid = idx.get((norm(p["n"]), p["p"]))
        if not pid and p["p"] == "DEF":
            pid = idx.get(("def:" + p["t"], "DEF"))
        if not pid and p["p"] == "PK":
            pid = idx.get((norm(p["n"]), "K"))
        if pid:
            out[pid] = p["n"]
            meta[p["n"]] = {"exp": exp_by_pid.get(pid)}
        else:
            missing.append(f"{p['p']} {p['n']} ({p['t']})")
    return out, meta, missing


def get_text(url, timeout=45):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def fetch_projections():
    """FFToday's raw stat projections, keyed by normalised name."""
    import html as _html
    got = {}
    for pos_id, (pos, cols) in PROJ_COLS.items():
        for page in range(0, 6):
            h = None
            for attempt in range(3):          # they rate-limit; a 403 is often transient
                try:
                    h = get_text(PROJ_URL.format(pos=pos_id, page=page))
                    break
                except Exception as e:
                    err = e
                    time.sleep(2 ** attempt)
            if h is None:
                print(f"  proj     {pos} page {page} failed ({err})")
                break
            rows = re.findall(r'<A HREF="/stats/players/[^"]*">([^<]+)</A>(.*?)</TR>', h, re.S | re.I)
            if not rows:
                break
            for name, rest in rows:
                cells = [_html.unescape(re.sub("<[^>]+>", "", c)).strip()
                         for c in re.findall(r"<TD[^>]*>(.*?)</TD>", rest, re.S | re.I)]
                if len(cells) < len(cols) + 2:
                    continue
                vals = []
                for c in cells[2:2 + len(cols)]:
                    c = c.replace(",", "")
                    try: vals.append(float(c))
                    except ValueError: vals = None; break
                if vals is None:
                    continue
                d = dict(zip(cols, vals))
                # cells[0] is the team, cells[1] the bye — needed to work out
                # a player's share of his own offence
                d["tm"] = cells[0].upper()
                d["pos"] = pos
                got[norm(_html.unescape(name))] = d
    return got


def project(players, scoring, got):
    """Rank by points under OUR scoring, not by other people's draft habits.

    ADP is a consensus of generic superflex drafts. It cannot know that this
    league pays 4 points for a passing touchdown and a full point per
    reception. Taking FFToday's raw stat projections and scoring them with the
    league's own settings gives a ranking actually fitted to this league, and
    then value over replacement turns that into something comparable across
    positions.
    """
    sc = scoring
    scored = 0
    for p in players:
        d = got.get(norm(p["n"]))
        if not d:
            continue
        pts = (d.get("pyd", 0) * sc["pass_yd"] + d.get("ptd", 0) * sc["pass_td"]
               + d.get("int", 0) * sc["pass_int"]
               + d.get("ry", 0) * sc["rush_yd"] + d.get("rtd", 0) * sc["rush_td"]
               + d.get("rec", 0) * sc["rec"] + d.get("recy", 0) * sc["rec_yd"]
               + d.get("rectd", 0) * sc["rec_td"])
        p["pts"] = round(pts, 1)
        scored += 1
    print(f"  proj     {len(got)} projections, {scored} matched into the pool")
    return scored


def add_roles(players, got):
    """Where a player sits in his own offence, and which way that offence leans.

    Two questions the raw point total cannot answer: is this back the guy or
    one of two, and does this offence throw. Both are derived from the same
    projections, by comparing a player against his own teammates rather than
    against the league.

    Role is share of team touches (carries + receptions for backs, receptions
    for receivers), so a pass-catching back on few carries is not mislabelled
    a backup. Lean is team pass attempts over pass plus rush attempts.

    What this is NOT: an offensive coordinator's history or scheme. There is no
    coaching-staff source here. It is one projection set's implied volume
    split, which is a consequence of scheme rather than a reading of it, and it
    inherits FFToday's assumptions about who is on which roster.
    """
    from collections import defaultdict
    teams = defaultdict(lambda: defaultdict(list))
    for n, d in got.items():
        teams[d.get("tm", "?")][d.get("pos", "?")].append((n, d))

    # team pass lean, and the league spread to judge it against
    lean = {}
    for tm, byp in teams.items():
        patt = sum(d.get("att", 0) for _, d in byp.get("QB", []))
        ratt = sum(d.get("ra", 0) for pos in ("QB", "RB", "WR") for _, d in byp.get(pos, []))
        if patt + ratt >= 200:
            lean[tm] = patt / (patt + ratt)
    if lean:
        vals = sorted(lean.values())
        mid = vals[len(vals) // 2]
        sd = (sum((v - mid) ** 2 for v in vals) / len(vals)) ** 0.5
    else:
        mid, sd = 0.55, 0.03

    # share of team touches, per position
    share = {}
    for tm, byp in teams.items():
        rbs = byp.get("RB", [])
        tot = sum(d.get("ra", 0) + d.get("rec", 0) for _, d in rbs)
        if tot >= 50:
            for n, d in rbs:
                share[n] = ((d.get("ra", 0) + d.get("rec", 0)) / tot, None)
        for pos in ("WR", "TE"):
            grp = byp.get(pos, [])
            tot = sum(d.get("rec", 0) for _, d in grp)
            if tot < 30:
                continue
            for i, (n, d) in enumerate(sorted(grp, key=lambda x: -x[1].get("rec", 0))):
                share[n] = (d.get("rec", 0) / tot, i + 1)

    tagged = 0
    for p in players:
        d = got.get(norm(p["n"]))
        if not d:
            continue
        tm = d.get("tm")
        if tm in lean:
            p["lean"] = round(lean[tm], 3)
            p["leanTag"] = ("pass" if lean[tm] >= mid + 0.5 * sd
                            else "run" if lean[tm] <= mid - 0.5 * sd else "even")
        s = share.get(norm(p["n"]))
        if not s:
            continue
        frac, rank = s
        p["shr"] = round(frac, 3)
        if p["p"] == "RB":
            p["role"] = ("bell cow" if frac >= 0.60 else "lead" if frac >= 0.42
                         else "committee" if frac >= 0.25 else "backup")
        elif p["p"] in ("WR", "TE"):
            p["role"] = f"{p['p']}{min(rank, 4)}"
        tagged += 1
    print(f"  roles    {tagged} players tagged; league pass rate {mid:.3f} +/-{sd:.3f}, "
          f"{sum(1 for t in lean.values() if t >= mid + 0.5 * sd)} pass-lean / "
          f"{sum(1 for t in lean.values() if t <= mid - 0.5 * sd)} run-lean teams")
    return tagged


def blend_market(players, w):
    """Shrink one source's projections toward what the market thinks.

    ADP is not merely a record of when a player gets taken — it is the fantasy
    community's aggregated estimate of the same production these projections
    estimate. It is built from thousands of drafts; FFToday is one analyst.
    Treating them as rivals was the wrong frame: they measure the same latent
    quantity, and the sensible thing is to average them.

    Market value is read off the projected VOR distribution by ADP rank within
    a position — if the market's ordering is right, a player earns the value
    belonging to his slot. Taking it by rank rather than by fitting a curve to
    the player's own point avoids the circularity of comparing him to himself.

    The two mostly agree (median disagreement 1 rank at QB, 3-4 at RB/WR), so
    this mostly damps single-source outliers rather than reordering the board.
    """
    if not (0 < w < 1):
        return 0
    moved = 0
    for pos in ("QB", "RB", "WR", "TE"):
        pool = [p for p in players if p["p"] == pos and "vor" in p]
        if len(pool) < 8:
            continue
        vals = sorted((p["vor"] for p in pool), reverse=True)
        by_adp = sorted(pool, key=lambda x: x["a"])
        for rank, p in enumerate(by_adp):
            market = vals[rank]
            before = p["vor"]
            p["vor"] = round(w * market + (1 - w) * before, 1)
            if abs(p["vor"] - before) >= 10:
                moved += 1
    print(f"  proj     blended {int(w * 100)}% market into value; {moved} moved 10+ pts")
    return moved


def add_vorp(players, cfg):
    """Points above the last player at your position who would actually start.

    A quarterback worth 300 points is not worth the same as a receiver worth
    300 when 24 quarterbacks start in this league and only the top handful of
    receivers separate themselves.
    """
    # The pool is now persisted with vor on it, so a rebuild would otherwise
    # find nothing to interpolate and keep estimates derived from an older ADP
    # curve. Always recompute from the projections.
    for p in players:
        p.pop("vor", None)
        p.pop("vest", None)

    teams = cfg["teams"]
    slots = cfg["roster_slots"]
    dedicated = {}
    for s in slots:
        if s in ("QB", "RB", "WR", "TE"):
            dedicated[s] = dedicated.get(s, 0) + 1
    flex = slots.count("FLEX")
    sflex = slots.count("SUPER_FLEX")
    # flex goes mostly to backs and receivers; superflex is a quarterback in a
    # league where quarterbacks score like this
    share = {"RB": 0.45 * flex, "WR": 0.45 * flex, "TE": 0.10 * flex, "QB": 0.0}
    share["QB"] += sflex
    repl = {}
    for pos in ("QB", "RB", "WR", "TE"):
        rank = round((dedicated.get(pos, 0) + share.get(pos, 0)) * teams)
        pool = sorted((p for p in players if p["p"] == pos and "pts" in p),
                      key=lambda x: -x["pts"])
        if not pool:
            continue
        idx = min(max(rank - 1, 0), len(pool) - 1)
        repl[pos] = pool[idx]["pts"]
    for p in players:
        if "pts" in p and p["p"] in repl:
            p["vor"] = round(p["pts"] - repl[p["p"]], 1)

    blend_market(players, cfg.get("market_weight", 0.5))

    # Not everyone has a projection — deep bench types especially. Fill those
    # in from the ADP-to-VOR relationship the projected players describe, so
    # the board can rank on one consistent scale instead of two.
    known = sorted(((p["a"], p["vor"]) for p in players if "vor" in p))
    if len(known) >= 20:
        xs = [a for a, _ in known]
        ys = [v for _, v in known]
        filled = 0
        for p in players:
            if "vor" not in p:
                p["vor"] = round(_interp(p["a"], xs, ys), 1)
                p["vest"] = 1
                filled += 1
        print(f"  proj     {filled} without projections estimated from the ADP-VOR curve")
    print("  proj     replacement level " + ", ".join(f"{k}{int(v)}" for k, v in sorted(repl.items())))
    return repl


def attach_buzz(players, id_map, meta, today):
    """Three independent reads on who the market is waking up to.

    ADP alone is a lagging average: by the time a breakout shows up in it, the
    price has already moved. These catch the move while it is happening.

      velocity  how many ADP spots a player has gained recently. Drawn from
                archived snapshots, so it measures the market's own change of
                mind rather than anyone's opinion.
      adds      Sleeper roster adds across every league on the platform. This
                is behaviour, not sentiment — nobody is scraping tone here.
      rookie    years_exp == 0, because a rookie with buzz is a different
                proposition from a veteran with buzz.

    Corroboration is the point: any one of these is noise, and a player showing
    up in two of them at once is worth a look.
    """
    hist_path = os.path.join(DATA, "adp_history.json")
    hist = {"snapshots": [], "adp": {}}
    if os.path.exists(hist_path):
        try: hist = json.load(open(hist_path))
        except Exception: pass

    # record today, then measure against the oldest point still in the window
    for p in players:
        hist["adp"].setdefault(p["n"], {})[today] = p["a"]
    if not any(s["date"] == today for s in hist["snapshots"]):
        hist["snapshots"].append({"date": today})
    hist["snapshots"] = sorted(hist["snapshots"], key=lambda s: s["date"])[-40:]
    keep = {s["date"] for s in hist["snapshots"]}
    for n in list(hist["adp"]):
        hist["adp"][n] = {d: v for d, v in hist["adp"][n].items() if d in keep}
        if not hist["adp"][n]:
            del hist["adp"][n]

    dates = [s["date"] for s in hist["snapshots"]][-HISTORY_DAYS:]
    for p in players:
        by = hist["adp"].get(p["n"], {})
        pts = [by[d] for d in dates if d in by]
        # ADP counts down, so an earlier pick number means he is climbing
        p["vel"] = round(pts[0] - pts[-1], 1) if len(pts) >= 2 else 0.0

    try:
        trend = get(TREND_URL, timeout=30)
        by_pid = {t["player_id"]: t["count"] for t in trend}
        name_of = id_map
        adds = {}
        for pid, c in by_pid.items():
            n = name_of.get(pid)
            if n: adds[n] = c
        top = max(adds.values()) if adds else 0
        for p in players:
            p["add"] = adds.get(p["n"], 0)
        print(f"  buzz     {len(adds)} of the pool trending on Sleeper (top {top:,} adds)")
    except Exception as e:
        for p in players: p["add"] = 0
        print(f"  buzz     trending unavailable ({e})")

    for p in players:
        if meta.get(p["n"], {}).get("exp") == 0:
            p["rk"] = 1

    json.dump(hist, open(hist_path, "w"), separators=(",", ":"))
    movers = sorted((p for p in players if p.get("vel")), key=lambda p: -p["vel"])[:3]
    if movers:
        print("  buzz     risers: " + ", ".join(f"{p['n']} +{p['vel']:.0f}" for p in movers))
    print(f"  buzz     {sum(1 for p in players if p.get('rk'))} rookies flagged, "
          f"{len(hist['snapshots'])} snapshots on file")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true", help="build from cached data/")
    ap.add_argument("--refresh-ids", action="store_true", help="rebuild the Sleeper id map")
    args = ap.parse_args()

    pool_path = os.path.join(DATA, "players_sf_adp.json")
    ids_path = os.path.join(DATA, "player_ids.json")

    if args.no_fetch:
        cached = json.load(open(pool_path))
        players, src = cached["players"], cached["source"]
        print(f"  adp      cached — {src}")
    else:
        players, src = fetch_adp()
        print(f"  adp      {len(players)} players — {src}")

    meta_path = os.path.join(DATA, "player_meta.json")
    if args.refresh_ids or not os.path.exists(ids_path) or not os.path.exists(meta_path):
        id_map, meta, missing = build_id_map(players)
        json.dump(id_map, open(ids_path, "w"), indent=1)
        json.dump(meta, open(meta_path, "w"), indent=1)
        print(f"  ids      {len(id_map)}/{len(players)} matched"
              + (f" — MISSING: {', '.join(missing[:8])}" if missing else ""))
    else:
        id_map = json.load(open(ids_path))
        meta = json.load(open(meta_path))
        known = {v for v in id_map.values()}
        gaps = [p["n"] for p in players if p["n"] not in known]
        print(f"  ids      cached, {len(id_map)} entries"
              + (f" — {len(gaps)} pool players unmapped, run --refresh-ids" if gaps else ""))

    # Projections are cached so --no-fetch really is offline. Without a cache a
    # network-less rebuild would silently drop `vor`, and the board's
    # need+value sort would quietly degrade to need alone.
    cfg_early = json.load(open(os.path.join(DATA, "league_config.json")))
    proj_path = os.path.join(DATA, "projections.json")
    try:
        if args.no_fetch:
            got = json.load(open(proj_path))["stats"]
            print(f"  proj     cached, {len(got)} players")
        else:
            # FFToday rate-limits: a run can 403 on one position and succeed on
            # the rest. Merge over the cache rather than replacing it, so a
            # partial fetch never drops a position we already had — losing TE
            # would silently push every tight end onto the interpolated curve.
            got = {}
            if os.path.exists(proj_path):
                got.update(json.load(open(proj_path))["stats"])
            fresh = fetch_projections()
            kept = len(set(got) - set(fresh))
            got.update(fresh)
            if fresh:
                json.dump({"source": "FFToday season projections",
                           "fetched": datetime.date.today().isoformat(),
                           "stats": got}, open(proj_path, "w"), separators=(",", ":"))
            if kept:
                print(f"  proj     {len(fresh)} fetched, {kept} kept from cache")
        if got and project(players, cfg_early["scoring_settings"], got):
            add_vorp(players, cfg_early)
            add_roles(players, got)
    except Exception as e:
        print(f"  proj     skipped ({e})")

    # Persist points and value back into the pool so they are versioned and
    # reviewable in a diff, rather than existing only inside the built HTML.
    # Written before buzz, which is derived at build time from adp_history.
    json.dump({"source": src, "format": "superflex 2QB, 12 team, full PPR",
               "players": players}, open(pool_path, "w"), indent=1)

    attach_buzz(players, id_map, meta, datetime.date.today().isoformat())

    cfg = json.load(open(os.path.join(DATA, "league_config.json")))
    payload = {
        "players": players,
        "idToName": id_map,
        "keepers": cfg.get("modelled_keepers", []),
        "mine": cfg.get("my_keepers", []),
        "myPicks": cfg.get("my_picks", []),
        "slots": cfg["roster_slots"],
        "bench": cfg["bench"],
        "teams": cfg["teams"],
        "rounds": cfg["rounds"],
        "mySlot": cfg["my_draft_slot"],
        "myUserId": cfg.get("my_user_id"),
        "leagueId": cfg["league_id"],
        "draftId": cfg.get("draft_id"),
        "season": cfg.get("season"),
        "meta": {"src": src},
    }

    script = []
    for name in SRC_ORDER:
        path = os.path.join(HERE, "src", name)
        if not os.path.exists(path):
            print(f"  src      {name} absent, skipped")
            continue
        script.append(f"/* ===== {name} ===== */\n" + open(path).read())
    if not script:
        sys.exit("no source files found in app/src")

    template = open(os.path.join(HERE, "template.html")).read()
    for token in ("__DATA__", "__SCRIPT__"):
        if token not in template:
            sys.exit(f"template.html is missing the {token} placeholder")
    html = (template
            .replace("__DATA__", json.dumps(payload, separators=(",", ":")))
            .replace("__SCRIPT__", "\n\n".join(script)))

    # app/dist is the local build; docs/ is what GitHub Pages serves from the
    # branch. Writing both keeps them from drifting apart.
    redirect = (
        '<meta charset="utf-8"><title>Zebras Draft Console</title>\n'
        # A meta refresh would drop the query string and ?draft= is the whole
        # point, so hand off in script and keep it.
        '<script>location.replace("draft-live.html" + location.search + location.hash)</script>\n'
        '<noscript><a href="draft-live.html">Open the draft console</a></noscript>\n'
    )
    for d in (DIST, DOCS):
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "draft-live.html"), "w").write(html)
        open(os.path.join(d, "index.html"), "w").write(redirect)
        # keep Pages' Jekyll pass away from a hand-built site
        open(os.path.join(d, ".nojekyll"), "w").write("")
        print(f"  built    {os.path.relpath(d, ROOT)}/  draft-live.html {len(html) / 1024:.0f} KB + index redirect")


if __name__ == "__main__":
    main()
