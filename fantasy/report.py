"""Markdown rendering for the monitor's output."""

from __future__ import annotations

from . import config
from .model import League, player_value, value_meta

TIER_ICON = {
    "URGENT": "🚨",
    "BUY EARLY": "🟢",
    "CONTESTED": "🔥",
    "STASH": "🧊",
    "INSURANCE": "🛡️",
    "STARTER FA": "🔹",
    "SPECULATIVE": "·",
}


def header(lg: League, week: int, generated: str) -> str:
    me = lg.me
    meta = value_meta()
    played = meta.get("played_weeks", 0)
    w = played / (played + meta.get("prior_games", config.PRIOR_GAMES)) if played else 0.0
    basis = (
        f"value = rest-of-season points over replacement from usage through week {played} "
        f"({meta.get('with_usage', 0)} players with games), projected over {meta.get('horizon')}; "
        f"a player with every game played is {w:.0%} his own usage, the rest prior"
        if played else
        f"value = rest-of-season points over replacement; no games played yet, so it is the prior "
        f"projected over {meta.get('horizon')}"
    )
    src = meta.get("sources") or {}
    weights = src.get("weights") or {}
    live = [f"{name} ×{wt:g}" for name, wt in weights.items() if wt > 0]
    prior = "prior from " + ("; ".join(src.get("used") or []) or "nothing")
    if live:
        prior += f" · weights {', '.join(live)}"
    if src.get("static"):
        prior += " · ⚠️ static, weight 0: " + "; ".join(src["static"])
    if src.get("failed"):
        prior += " · ⚠️ unavailable: " + "; ".join(src["failed"])
    if not src.get("used"):
        prior += " — values rest on usage alone"
    if meta.get("unverified"):
        prior += (f" · {meta['unverified']} absences priced on default weeks, not a blurb "
                  f"(`unverified`)")
    fit = meta.get("usage_fit") or {}
    seasons = meta.get("usage_fit_seasons") or []
    fit_line = ""
    if fit and seasons:
        cells = []
        for pos, v in fit.items():
            if v.get("oos_r") is not None:
                cells.append(f"{pos} {v['oos_r']:.2f}"
                             + (f" (PPG alone {v['ppg_only']:.2f})" if v.get("ppg_only") is not None else ""))
        if cells:
            fit_line = (f"_usage fit on {', '.join(seasons)}; out-of-sample r vs rest-of-season PPG "
                        f"on {seasons[-1]}: " + ", ".join(cells) + "_\n")
    return (
        f"# {config.LEAGUE_NAME} — waiver & trade monitor\n\n"
        f"**Week {week}** · {me.label} ({me.wins}-{me.losses}) · "
        f"FAAB left **${me.faab_left}** · generated {generated}\n\n"
        f"_{basis}_  \n_{prior}_  \n{fit_line}"
    )


def alerts_section(alerts: list, baseline: str = "", have_baseline: bool = True) -> str:
    out = ["## Since last run\n"]
    if baseline:
        out.append(f"_{baseline}_\n")
    if not have_baseline:
        out.append("No committed snapshot to diff against, so nothing can be "
                   "reported as changed. This is a first run, not a quiet hour.\n")
    elif not alerts:
        out.append("Nothing changed.\n")
    for a in alerts:
        out.append(f"- {a['icon']} **{a['title']}** — {a['detail']}")
    return "\n".join(out) + "\n"


def board_section(board: list, faab: int) -> str:
    if not board:
        return "## Waiver board\n\nNo free agent clears the value threshold right now.\n"
    out = ["## Waiver board — best available opportunity\n"]
    out.append("| | Player | Tier | Own value | Why | Adds/24h | Bid |")
    out.append("|:--|:--|:--|--:|:--|--:|--:|")
    for c in board:
        icon = TIER_ICON.get(c.tier, "·")
        why = "; ".join(c.reasons[:2]) or "depth"
        out.append(
            f"| {icon} | **{c.label}** | {c.tier} | {c.own_value} | {why} | "
            f"{c.market_adds:,} | {suggest_bid(c, faab)} |"
        )
    out.append(
        "\n*Own value is the player's standalone worth, separate from the "
        "opportunity in front of him. A high tier next to a low own value means "
        "you are buying a job, not a player — check that the job is worth having "
        "before bidding against a crowd.*"
    )
    return "\n".join(out) + "\n"


def suggest_bid(cand, faab: int) -> str:
    """FAAB guidance scaled to opportunity, standing value, and competition."""
    if cand.tier == "URGENT":
        pct = 0.22 if cand.opportunity > 15 else 0.12
    elif cand.tier == "CONTESTED":
        pct = 0.30   # everyone sees it; a token bid just donates the claim
    elif cand.tier == "BUY EARLY":
        pct = 0.06
    elif cand.tier == "STASH":
        pct = 0.05   # a dated return window is worth a real but modest bid
    elif cand.tier == "STARTER FA":
        pct = 0.04
    elif cand.tier == "INSURANCE":
        pct = 0.03
    else:
        pct = 0.01
    # An elite player hitting waivers is worth a real bid on his own merits,
    # whatever the reason he became available.
    if cand.own_value >= 55:
        pct = max(pct, 0.45)
    elif cand.own_value >= 40:
        pct = max(pct, 0.25)
    elif cand.own_value >= 25:
        pct = max(pct, 0.10)

    # Contested players cost more -- but the CONTESTED tier already prices that
    # in, so do not charge the premium twice.
    if cand.market_adds > config.MARKET_HOT and cand.tier != "CONTESTED":
        pct *= 1.6

    dollars = max(1, min(faab, round(faab * pct)))

    # A pure handcuff with no standalone worth is a lottery ticket, however
    # valuable the job in front of him. Never bid real money on one -- if the
    # starter goes down the backup will still be there, or the next man will.
    if cand.own_value < 5 and cand.tier not in ("URGENT", "CONTESTED", "STASH") \
            and not cand.market_rerated:
        dollars = min(dollars, 2)

    return f"${dollars}"


def handcuff_section(rows: list) -> str:
    out = ["## My roster — who inherits the job\n"]
    out.append("| My player | Status | Next man up | Available? |")
    out.append("|:--|:--|:--|:--|")
    for r in rows[:10]:
        if not r["backups"]:
            continue
        b = r["backups"][0]
        avail = "**FREE AGENT**" if b["available"] else f"rostered ({b['owner']})"
        out.append(f"| {r['label']} | {designation(r['injury'])} | {b['label']} | {avail} |")
    out.append(
        "\n*A designation shown as `unverified` has had no beat-reporter blurb read "
        "for it. The tag alone cannot tell a cramp from a torn ACL, or a current "
        "injury from a two-year-old one — run "
        "`python3 -m fantasy.monitor player --name \"...\"` before acting on it.*"
    )
    return "\n".join(out) + "\n"


def designation(status, info: dict | None = None) -> str:
    """Render an injury designation, never as bare fact.

    Without a blurb behind it a tag is an unverified claim, and it is labelled
    as one. With a blurb, the evidence and its date travel alongside.
    """
    if not status:
        return "healthy"
    if not info:
        return f"{status} · _unverified_"
    bits = [status]
    if info.get("practice"):
        bits.append(f"practice {info['practice']}")
    if info.get("stale"):
        bits.append(f"newest news {info['blurb_age_days']}d old")
    if info.get("return_designated"):
        wk = info.get("eligible_week")
        bits.append("designated to return" + (f" wk {wk}" if wk else ""))
    tail = f" — _{info['date']}: {info['headline']}_" if info.get("headline") else ""
    return " · ".join(bits) + tail


def my_position_section(summary: dict) -> str:
    out = ["## Where I stand\n"]
    out.append("| Pos | Starters | Rostered | Starter value | League median | Rank | Tradeable surplus |")
    out.append("|:--|--:|--:|--:|--:|--:|--:|")
    for pos in config.SKILL_POSITIONS:
        s = summary[pos]
        out.append(
            f"| {pos} | {s['required']} | {s['count']} | {s['starter_value']} | "
            f"{s['median']} | **{s['rank']}/12** | {s['surplus']} |"
        )
    warnings = [
        f"**{pos}: {s['count']} rostered for {s['required']} starting slots — "
        f"one injury and there is nobody to plug in.**"
        for pos, s in summary.items()
        if s["count"] <= s["required"]
    ]
    if warnings:
        out.append("\n> ⚠️ " + "\n> ".join(warnings))
    out.append(
        "\n*Surplus is bench value above replacement level — what you could trade "
        "without weakening your starting lineup. A high positional rank with zero "
        "surplus means strong, not deep, and there is nothing there to trade.*"
    )
    return "\n".join(out) + "\n"


def league_strength_section(rows: list) -> str:
    out = ["## League roster strength\n"]
    out.append("| # | Team | W-L | Lineup | Bench | QB | RB | WR | TE |")
    out.append("|--:|:--|:--|--:|--:|--:|--:|--:|--:|")
    for i, r in enumerate(rows, start=1):
        t = r["team"]
        name = f"**{t.label}**" if t.is_me else t.label
        out.append(
            f"| {i} | {name} | {t.wins}-{t.losses} | {r['lineup']} | {r['bench']} | "
            f"{r['QB']} | {r['RB']} | {r['WR']} | {r['TE']} |"
        )
    out.append(
        "\n*Lineup is the value of the starters each roster actually plays: the "
        "positional starters plus the best three leftovers for FLEX, FLEX and "
        "SUPER_FLEX. Bench is everything after that. Values are the same rest-of-season "
        "points-over-replacement numbers used everywhere else in this report.*"
    )
    return "\n".join(out) + "\n"


def trades_section(partners: list, lg: League) -> str:
    if not partners:
        return "## Trade targets\n\nNo complementary fits found.\n"
    out = ["## Trade targets — ranked by two-way fit\n"]
    for p in partners:
        t = p["team"]
        needs = ", ".join(f"{k} ({v})" for k, v in sorted(p["their_needs"].items(), key=lambda x: -x[1])) or "none"
        counts = " / ".join(f"{k}:{p['counts'][k]}" for k in config.SKILL_POSITIONS)
        out.append(f"### {t.label} — fit score {p['score']}")
        out.append(f"- Roster counts: {counts}")
        out.append(f"- They need: **{needs}**")
        out.append(f"- Shape: I send **{p['send_position']}**, I get back **{p['recv_position']}**")
        if p["offers"]:
            out.append("- Concrete starting points:")
            for o in p["offers"]:
                out.append(
                    f"  - _{o['shape']}_ — give **{o['give']}** ({o['give_value']}) "
                    f"→ get **{o['get']}** ({o['get_value']})"
                )
        else:
            out.append("  - No value-balanced 1-for-1 found; this one needs a 2-for-1.")
        out.append("")
    return "\n".join(out) + "\n"


def news_section(hits: list) -> str:
    if not hits:
        return "## News\n\nNothing new mentioning our players.\n"
    out = ["## News touching our players\n"]
    for h in hits[:10]:
        who = ", ".join(f"{p} ({c})" for p, c in zip(h["players"], h["context"]))
        flag = "⚠️ " if h["injury_flavored"] else ""
        out.append(f"- {flag}[{h['headline']}]({h['link']}) — {who}")
    return "\n".join(out) + "\n"
