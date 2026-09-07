"""Entry point: build the report, diff against the last run, emit alerts.

    python3 -m fantasy.monitor report     # full standing analysis
    python3 -m fantasy.monitor watch      # hourly mode: only what changed
    python3 -m fantasy.monitor trades     # trade targets only
    python3 -m fantasy.monitor waiver     # waiver board only
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from . import config, model, news, report, sleeper, trades, waiver
from .model import player_value

STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")
SNAPSHOT = os.path.join(STATE_DIR, "snapshot.json")


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------

def _interesting(lg, charts) -> set:
    """Players worth tracking: anyone rostered, plus every direct backup of one.

    Watching all ~800 skill players would make every roster churn look like news.
    """
    keep = set(lg.rostered)
    for pid in list(keep):
        for b in model.backups_of(pid, lg.players, charts, limit=2):
            keep.add(b)
    return keep


def take_snapshot(lg, charts) -> dict:
    watch = _interesting(lg, charts)
    ownership = {}
    for t in lg.teams:
        for pid in t.player_ids:
            ownership[pid] = t.roster_id
    # Deliberately no timestamp: the file should change only when league state
    # actually changes, so an hourly job can `git diff --quiet` and skip the
    # commit on a quiet hour instead of churning the history.
    return {
        "injuries": {
            pid: (lg.players.get(pid) or {}).get("injury_status")
            for pid in watch
            if (lg.players.get(pid) or {}).get("injury_status")
        },
        "depth": {
            pid: (lg.players.get(pid) or {}).get("depth_chart_order")
            for pid in watch
            if (lg.players.get(pid) or {}).get("depth_chart_order") is not None
        },
        "ownership": ownership,
    }


def load_snapshot() -> dict | None:
    try:
        with open(SNAPSHOT) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def save_snapshot(snap: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = SNAPSHOT + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(snap, fh, indent=1, sort_keys=True)
    os.replace(tmp, SNAPSHOT)


# --------------------------------------------------------------------------
# diffing
# --------------------------------------------------------------------------

def diff(lg, charts, old: dict | None, new: dict) -> list:
    """Turn two snapshots into a list of things a manager would want paged about."""
    if not old:
        return []
    alerts = []

    old_inj, new_inj = old.get("injuries", {}), new.get("injuries", {})
    for pid in set(old_inj) | set(new_inj):
        before, after = old_inj.get(pid), new_inj.get(pid)
        if before == after:
            continue
        p = lg.players.get(pid) or {}
        owner = lg.owner_of(pid)
        mine = owner and owner.is_me
        val = player_value(p)

        # Only page for designations that change a workload.
        significant = after in config.ALERT_STATUSES or before in config.ALERT_STATUSES
        if not significant and val < 10 and not mine:
            continue

        who = "MY " if mine else (f"{owner.label}'s " if owner else "FREE AGENT ")
        if after and not before:
            title = f"{lg.describe(pid)} → {after}"
            detail = f"{who}player newly listed {after}."
        elif before and not after:
            title = f"{lg.describe(pid)} cleared ({before} → healthy)"
            detail = f"{who}player is off the report."
        else:
            title = f"{lg.describe(pid)} {before} → {after}"
            detail = f"{who}player's status changed."

        # Name the beneficiary -- that is the actionable half of an injury alert.
        if after in config.ALERT_STATUSES or after == "Questionable":
            backups = model.backups_of(pid, lg.players, charts, limit=1)
            if backups:
                b = backups[0]
                free = b not in lg.rostered
                detail += (
                    f" Next up: **{lg.describe(b)}** "
                    + ("— FREE AGENT, claim him." if free else f"— rostered by {lg.owner_of(b).label}.")
                )
        alerts.append(
            {
                "pid": pid,
                "icon": "🚨" if mine else "⚠️",
                "title": title,
                "detail": detail,
                "priority": (2 if mine else 1) + (1 if after in config.ALERT_STATUSES else 0),
            }
        )

    old_own, new_own = old.get("ownership", {}), new.get("ownership", {})
    for pid in set(old_own) | set(new_own):
        before, after = old_own.get(pid), new_own.get(pid)
        if before == after:
            continue
        p = lg.players.get(pid) or {}
        if player_value(p) < 6:
            continue
        if before is None:
            t = next((x for x in lg.teams if x.roster_id == after), None)
            alerts.append({
                "pid": pid, "icon": "📥", "priority": 1,
                "title": f"{lg.describe(pid)} was added",
                "detail": f"Picked up by {t.label if t else '?'}.",
            })
        elif after is None:
            alerts.append({
                "pid": pid, "icon": "📤", "priority": 2,
                "title": f"{lg.describe(pid)} was dropped",
                "detail": "Now a free agent — check the waiver board below.",
            })
        else:
            a = next((x for x in lg.teams if x.roster_id == before), None)
            b = next((x for x in lg.teams if x.roster_id == after), None)
            alerts.append({
                "pid": pid, "icon": "🔁", "priority": 1,
                "title": f"{lg.describe(pid)} traded",
                "detail": f"{a.label if a else '?'} → {b.label if b else '?'}.",
            })

    old_depth, new_depth = old.get("depth", {}), new.get("depth", {})
    for pid in set(old_depth) & set(new_depth):
        before, after = old_depth[pid], new_depth[pid]
        if before == after or after is None or before is None:
            continue
        p = lg.players.get(pid) or {}
        if after >= before or player_value(p) < 4:
            continue   # only promotions are news
        alerts.append({
            "pid": pid, "icon": "📈", "priority": 2,
            "title": f"{lg.describe(pid)} moved up the depth chart ({before} → {after})",
            "detail": "Workload is trending his way."
                      + ("" if pid in lg.rostered else " He is a free agent."),
        })

    alerts.sort(key=lambda a: -a["priority"])
    return alerts


# --------------------------------------------------------------------------
# enrichment
# --------------------------------------------------------------------------

def enrich(lg, alerts: list, limit: int = 8) -> list:
    """Look up the beat-reporter blurb for each flagged player.

    The diff knows a designation changed; it cannot know whether the change is
    a cramp or an MRI, and those warrant opposite responses. Only players the
    diff already surfaced are looked up, so this stays a handful of requests an
    hour rather than a firehose we would mostly discard.
    """
    looked_up = 0
    for a in alerts:
        pid = a.get("pid")
        p = lg.players.get(pid) if pid else None
        if not p or looked_up >= limit:
            continue
        blurbs = news.player_news(p.get("rotowire_id"))
        if not blurbs:
            continue
        looked_up += 1
        info = news.assess(blurbs)
        a["news"] = info

        bits = []
        if info["practice"]:
            bits.append(f"practice: **{info['practice']}**")
        if info["verdict"] != "unclear":
            bits.append(info["verdict"])
        a["detail"] += (
            f"\n  ↳ _{info['date']} — {info['headline']}:_ {info['body']}"
            + (f" ({', '.join(bits)})" if bits else "")
        )

        # A cramp is not news. Drop it below the notification threshold so the
        # hourly job stops paging on precautionary tags.
        if info["verdict"] == "likely minor" and info["practice"] != "DNP":
            a["priority"] -= 2
            a["icon"] = "·"
        elif info["verdict"] == "serious" or info["practice"] == "DNP":
            a["priority"] += 1

    alerts.sort(key=lambda x: -x["priority"])
    return alerts


# --------------------------------------------------------------------------
# assembly
# --------------------------------------------------------------------------

def build(mode: str = "report") -> tuple:
    lg = model.load()
    charts = model.depth_charts(lg.players)
    state = sleeper.nfl_state()
    week = state.get("week", 1)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    snap = take_snapshot(lg, charts)
    prev = load_snapshot()
    alerts = enrich(lg, diff(lg, charts, prev, snap))

    board = waiver.build_board(lg, charts, top=15)
    handcuffs = waiver.handcuff_report(lg, charts)
    partners = trades.find_partners(lg, top=5)
    summary = trades.my_summary(lg)

    interest = {}
    for pid in lg.me.player_ids:
        interest[lg.name(pid)] = "my roster"
    for c in board[:12]:
        interest[c.name] = f"waiver target — {c.tier.lower()}"
    for row in handcuffs[:8]:
        for b in row["backups"][:1]:
            if b["available"]:
                interest[b["label"].split(" (")[0]] = f"handcuff to my {row['label'].split(' (')[0]}"
    hits = news.relevant_news(interest, hours=36)

    parts = [report.header(lg, week, now)]
    if mode in ("report", "watch"):
        parts.append(report.alerts_section(alerts))
    if mode in ("report", "waiver", "watch"):
        parts.append(report.board_section(board, lg.me.faab_left))
    if mode in ("report", "waiver"):
        parts.append(report.handcuff_section(handcuffs))
    if mode in ("report", "trades"):
        parts.append(report.my_position_section(summary))
        parts.append(report.trades_section(partners, lg))
    if mode in ("report", "watch"):
        parts.append(report.news_section(hits))

    return "\n".join(parts), alerts, snap, board


def summarize_for_push(alerts: list, board: list) -> str:
    """One line, under 200 chars, for a phone notification."""
    worth_waking = [a for a in alerts if a.get("priority", 0) >= 2]
    if worth_waking:
        top = worth_waking[0]
        extra = f" (+{len(worth_waking)-1} more)" if len(worth_waking) > 1 else ""
        note = (top.get("news") or {}).get("headline", "")
        line = f"{top['title']}{extra}" + (f" — {note}" if note else "")
        return line[:190]
    if board and board[0].tier in ("URGENT", "BUY EARLY"):
        c = board[0]
        return f"Waiver: {c.label} — {c.tier}. {c.reasons[0] if c.reasons else ''}"[:190]
    return ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Zebras Shooting Heroin monitor")
    ap.add_argument("mode", nargs="?", default="report",
                    choices=["report", "watch", "trades", "waiver", "player"])
    ap.add_argument("--name", help="player mode: whose news to look up")
    ap.add_argument("--no-save", action="store_true",
                    help="do not update the stored snapshot")
    ap.add_argument("--out", help="write the report to this file as well")
    args = ap.parse_args(argv)

    if args.mode == "player":
        if not args.name:
            ap.error("player mode needs --name")
        lg = model.load()
        hits = [p for p in lg.players.values()
                if (p.get("full_name") or "").lower() == args.name.lower()]
        if not hits:
            print(f"No player named {args.name!r}")
            return 1
        p = hits[0]
        blurbs = news.player_news(p.get("rotowire_id"))
        print(f"{p['full_name']} ({p.get('position')}-{p.get('team')}) — "
              f"tag: {model.injury_label(p) or 'healthy'}")
        if not blurbs:
            print("  no recent news")
            return 0
        info = news.assess(blurbs)
        print(f"  read: {info['verdict']}"
              + (f", practice {info['practice']}" if info["practice"] else ""))
        for b in blurbs:
            print(f"\n  [{b['date']}] {b['headline']}\n    {b['body']}")
        return 0

    text, alerts, snap, board = build(args.mode)
    print(text)

    if args.out:
        with open(args.out, "w") as fh:
            fh.write(text)

    if args.mode == "watch":
        line = summarize_for_push(alerts, board)
        if line:
            print(f"\n::PUSH:: {line}")
        else:
            print("\n::PUSH:: (nothing worth a notification)")

    if not args.no_save:
        save_snapshot(snap)
    return 0


if __name__ == "__main__":
    sys.exit(main())
