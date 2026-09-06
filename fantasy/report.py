"""Markdown rendering for the monitor's output."""

from __future__ import annotations

from . import config
from .model import League, player_value

TIER_ICON = {
    "URGENT": "🚨",
    "BUY EARLY": "🟢",
    "INSURANCE": "🛡️",
    "STARTER FA": "🔹",
    "SPECULATIVE": "·",
}


def header(lg: League, week: int, generated: str) -> str:
    me = lg.me
    return (
        f"# {config.LEAGUE_NAME} — waiver & trade monitor\n\n"
        f"**Week {week}** · {me.label} ({me.wins}-{me.losses}) · "
        f"FAAB left **${me.faab_left}** · generated {generated}\n"
    )


def alerts_section(alerts: list) -> str:
    if not alerts:
        return "## Since last run\n\nNothing changed.\n"
    out = ["## Since last run\n"]
    for a in alerts:
        out.append(f"- {a['icon']} **{a['title']}** — {a['detail']}")
    return "\n".join(out) + "\n"


def board_section(board: list, faab: int) -> str:
    if not board:
        return "## Waiver board\n\nNo free agent clears the value threshold right now.\n"
    out = ["## Waiver board — best available opportunity\n"]
    out.append("| | Player | Tier | Why | Adds/24h | Bid |")
    out.append("|:--|:--|:--|:--|--:|--:|")
    for c in board:
        icon = TIER_ICON.get(c.tier, "·")
        why = "; ".join(c.reasons[:2]) or "depth"
        out.append(
            f"| {icon} | **{c.label}** | {c.tier} | {why} | "
            f"{c.market_adds:,} | {suggest_bid(c, faab)} |"
        )
    return "\n".join(out) + "\n"


def suggest_bid(cand, faab: int) -> str:
    """FAAB guidance scaled to opportunity, standing value, and competition."""
    if cand.tier == "URGENT":
        pct = 0.22 if cand.opportunity > 15 else 0.12
    elif cand.tier == "BUY EARLY":
        pct = 0.06
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

    # Contested players cost more.
    if cand.market_adds > config.MARKET_HOT:
        pct *= 1.6

    dollars = max(1, min(faab, round(faab * pct)))
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
        inj = r["injury"] or "healthy"
        out.append(f"| {r['label']} | {inj} | {b['label']} | {avail} |")
    return "\n".join(out) + "\n"


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
