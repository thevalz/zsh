#!/usr/bin/env python3
"""Build the live draft console into a single self-contained HTML file.

    python3 app/build.py                 # refresh ADP, rebuild dist
    python3 app/build.py --no-fetch      # rebuild from cached data/
    python3 app/build.py --refresh-ids   # also rebuild the Sleeper id map

Re-run this the morning of the draft so the ADP is current — that is the whole
point of having a build step rather than hand-edited data.
"""
import argparse
import json
import os
import re
import sys
import unicodedata
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
DIST = os.path.join(HERE, "dist")

ADP_URL = "https://fantasyfootballcalculator.com/api/v1/adp/2qb?teams=12&year=2026&position=all"
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


def fetch_adp():
    payload = get(ADP_URL)
    meta = payload["meta"]
    players = [
        {"n": p["name"], "p": p["position"], "t": p["team"],
         "a": p["adp"], "b": p["bye"], "sd": p["stdev"]}
        for p in payload["players"]
    ]
    src = "FFC 2QB ADP, {} drafts, {} to {}".format(
        meta["total_drafts"], meta["start_date"], meta["end_date"])
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
    out, missing = {}, []
    for p in players:
        pid = idx.get((norm(p["n"]), p["p"]))
        if not pid and p["p"] == "DEF":
            pid = idx.get(("def:" + p["t"], "DEF"))
        if not pid and p["p"] == "PK":
            pid = idx.get((norm(p["n"]), "K"))
        if pid:
            out[pid] = p["n"]
        else:
            missing.append(f"{p['p']} {p['n']} ({p['t']})")
    return out, missing


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
        json.dump({"source": src, "format": "superflex 2QB, 12 team, full PPR",
                   "players": players}, open(pool_path, "w"), indent=1)
        print(f"  adp      {len(players)} players — {src}")

    if args.refresh_ids or not os.path.exists(ids_path):
        id_map, missing = build_id_map(players)
        json.dump(id_map, open(ids_path, "w"), indent=1)
        print(f"  ids      {len(id_map)}/{len(players)} matched"
              + (f" — MISSING: {', '.join(missing[:8])}" if missing else ""))
    else:
        id_map = json.load(open(ids_path))
        known = {v for v in id_map.values()}
        gaps = [p["n"] for p in players if p["n"] not in known]
        print(f"  ids      cached, {len(id_map)} entries"
              + (f" — {len(gaps)} pool players unmapped, run --refresh-ids" if gaps else ""))

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

    os.makedirs(DIST, exist_ok=True)
    out = os.path.join(DIST, "draft-live.html")
    open(out, "w").write(html)
    print(f"  built    {os.path.relpath(out, ROOT)}  {len(html) / 1024:.0f} KB")


if __name__ == "__main__":
    main()
