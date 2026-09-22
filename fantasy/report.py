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


# ---------------------------------------------------------------------------
# Rest-of-season values: the model's reasoning for every roster
# ---------------------------------------------------------------------------

VALUE_COLUMNS = (
    "| | Pos | Player | Value | Rank | ROS | ROS/wk | Usage xPPG (g) | Actual PPG | Floor / Ceil | Bust% | Prior | Sched | Weeks | Status |\n"
    "|:--|:--|:--|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|:--|"
)


def _value_row(lg: League, pid: str, row: dict, starter: bool = False, healthy: bool = False) -> str:
    p = lg.players.get(pid) or {}
    status = p.get("injury_status") or ""
    if row.get("unverified"):
        status += " · `unverified`"
    if healthy and status:
        status += f" · healthy {row['healthy_value']:.1f}"
    weeks = row.get("weeks") or 0.0
    per_wk = row["ros"] / weeks if weeks else 0.0
    xppg = f"{row['xppg']:.1f} ({row['games']})" if row.get("games") else "—"
    actual = f"{row['actual_ppg']:.1f}" if row.get("actual_ppg") is not None else "—"
    prior = f"{row['prior']:.0f}" if row.get("prior") is not None else "—"
    sched = f"{row['sched']:.2f}" if row.get("sched") is not None else "—"
    if row.get("floor") is not None:
        fc = f"{row['floor']:.1f} / {row['ceiling']:.1f}"
        bust = f"{row['bust_rate']:.0%}"
    else:
        fc = f"— ({row.get('games_logged', 0)} g)"
        bust = "—"
    return (
        f"| {'★' if starter else ''} | {p.get('position')} | {lg.name(pid)} ({p.get('team')}) | "
        f"{row['value']:.1f} | {row['rank']} | {row['ros']:.0f} | {per_wk:.1f} | {xppg} | {actual} | "
        f"{fc} | {bust} | {prior} | {sched} | {weeks:.0f} | {status.strip(' ·')} |"
    )


def _starters(lg: League, team, table: dict) -> set:
    """The lineup league_strength() assumes: positional starters plus the best
    three leftovers for FLEX, FLEX and SUPER_FLEX."""
    chosen: set = set()
    leftovers = []
    for pos in config.SKILL_POSITIONS:
        ranked = lg.roster_of(team, pos)
        chosen.update(ranked[: config.STARTERS[pos]])
        leftovers += ranked[config.STARTERS[pos]:]
    leftovers.sort(key=lambda pid: -table.get(pid, {}).get("value", 0.0))
    chosen.update(leftovers[:3])
    return chosen


def values_report(lg: League, week: int, generated: str) -> str:
    from . import model as _model
    from .trades import league_strength

    table = _model.value_table()
    meta = _model.value_meta()
    head = header(lg, week, generated).replace("waiver & trade monitor", "rest-of-season values")
    out = [head]
    out.append(
        "*Every number the waiver board, trade finder and roster-strength table run on, "
        "for every roster. **Value** is rest-of-season points over replacement, ranked "
        "across all skill players and put on the 0–100 curve. **ROS** is weighted "
        "rest-of-season points (playoff weeks count double); **ROS/wk** divides it by "
        "the weighted games he is projected to play. **Usage xPPG** is what his own "
        "targets, carries, shares and red-zone looks predict per game, with games "
        "played in brackets; **Actual PPG** is what he has scored. **Prior** is the "
        "outside lists' weighted rest-of-season points. **Sched** is his mean "
        "remaining-opponent multiplier (1.00 = league average). **Weeks** is weighted "
        "games he is projected to play. **Floor / Ceil** are the 25th and 75th percentiles "
        "of his league points per game played over last season and this one, and "
        "**Bust%** the share of those games under 8 points; they describe consistency and "
        "are not priced into value. ★ marks the lineup the strength table assumes. "
        "Kickers and defenses are streamed and not valued.*\n"
    )
    repl = meta.get("replacement") or {}
    if repl:
        out.append("Replacement level (ROS points): " + " · ".join(f"{k} {v}" for k, v in repl.items()) + "\n")

    rows = league_strength(lg)
    rows.sort(key=lambda r: (not r["team"].is_me, -r["lineup"]))
    for r in rows:
        t = r["team"]
        out.append(f"## {t.label} ({t.wins}-{t.losses}) — lineup {r['lineup']} · bench {r['bench']}\n")
        starters = _starters(lg, t, table)
        pids = sorted(
            (pid for pid in t.player_ids if pid in table),
            key=lambda pid: -table[pid]["value"],
        )
        out.append(VALUE_COLUMNS)
        for pid in pids:
            out.append(_value_row(lg, pid, table[pid], starter=pid in starters, healthy=True))
        out.append("")

    rostered = lg.rostered
    free = sorted((pid for pid in table if pid not in rostered), key=lambda pid: -table[pid]["value"])
    out.append("## Best available\n")
    out.append(
        f"Top 25 of {len(free)} unrostered skill players by value. A reserve-list player "
        "shows what he is worth when back (`healthy`); the waiver board resolves whether "
        "he is designated to return.\n"
    )
    out.append(VALUE_COLUMNS)
    for pid in free[:25]:
        out.append(_value_row(lg, pid, table[pid], healthy=True))
    out.append("")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Trade evaluation: score an offer the way the model scores everything else
# ---------------------------------------------------------------------------

def resolve_player(lg: League, name: str) -> str:
    """Sleeper player_id for a name; raises ValueError naming near matches."""
    want = name.strip().lower()
    exact = [pid for pid, p in lg.players.items()
             if p.get("team") and (p.get("full_name") or "").lower() == want]
    if len(exact) > 1:
        rostered = [pid for pid in exact if pid in lg.rostered]
        exact = rostered or exact[:1]
    if len(exact) == 1:
        return exact[0]
    tokens = want.split()
    near = [pid for pid, p in lg.players.items()
            if p.get("team") and p.get("position") in config.SKILL_POSITIONS
            and all(t in (p.get("full_name") or "").lower() for t in tokens)]
    if len(near) == 1:
        return near[0]
    hint = ", ".join(lg.describe(pid) for pid in near[:6]) or "no skill player matches"
    raise ValueError(f"'{name}': {hint}")


def _floor_of(table: dict, chosen: list, horizon: float) -> float:
    """Sum of the lineup's floors; a thin log falls back to 60% of his mean."""
    total = 0.0
    for p in chosen:
        r = table[p]
        total += r["floor"] if r.get("floor") is not None else 0.6 * r["ros"] / horizon
    return total


def _lineup(lg: League, table: dict, pids: list) -> tuple[float, list, float]:
    """(weighted ROS/wk of the lineup, the lineup, bench cover) for a roster."""
    from .backtest import pick_lineup, skill_slots
    slots = skill_slots(lg.settings.get("roster_positions") or [])
    # Per horizon week, not per week he plays: a man out until midseason has a
    # high per-game number and no games, and must not be "started" here.
    horizon = max((r.get("weeks") or 0.0) for r in table.values()) or 1.0
    vals = {}
    for pid in pids:
        r = table.get(pid)
        if r:
            vals[pid] = r["ros"] / horizon
    chosen = pick_lineup(vals, list(vals), lg.players, slots)
    rest = sorted((vals[p] for p in vals if p not in chosen
                   and lg.players[p].get("position") in ("RB", "WR")), reverse=True)
    return sum(vals[p] for p in chosen), chosen, sum(rest[:2])


def _fmt_lineup(lg: League, table: dict, chosen: list) -> str:
    horizon = max((r.get("weeks") or 0.0) for r in table.values()) or 1.0
    return ", ".join(
        f"{lg.players[p]['position']} {lg.name(p)} {table[p]['ros'] / horizon:.1f}"
        for p in chosen
    )


def trade_report(lg: League, give: list, get: list, partner=None, absences: dict | None = None) -> str:
    """`absences` maps player_id -> first week he is back, from blurbs the caller
    read; it overrides the tag-default absence for those players."""
    from . import model as _model, news
    from .trades import _balanced

    table = _model.value_table(absences=absences) if absences else _model.value_table()
    meta = _model.value_meta()
    me = lg.me
    partner = partner or lg.owner_of(get[0])
    if partner is None:
        raise ValueError(f"{lg.name(get[0])} is not on any roster; use --with to name the other team")
    for pid in give:
        if pid not in me.player_ids:
            raise ValueError(f"{lg.name(pid)} is not on my roster")
    for pid in get:
        if pid not in partner.player_ids:
            raise ValueError(f"{lg.name(pid)} is not on {partner.label}'s roster")

    out = [f"# Trade: {' + '.join(lg.name(p) for p in give)} for "
           f"{' + '.join(lg.name(p) for p in get)} (with {partner.label})\n"]
    out.append(VALUE_COLUMNS)
    for pid in give:
        out.append(_value_row(lg, pid, table[pid], healthy=True).replace("|  |", "| give |", 1))
    for pid in get:
        out.append(_value_row(lg, pid, table[pid], healthy=True).replace("|  |", "| get |", 1))

    vg = sum(table[p]["value"] for p in give)
    vr = sum(table[p]["value"] for p in get)
    tol = 0.45 if len(give) == len(get) == 1 else 0.35
    verdict = "inside" if _balanced(vg, vr, tol) else "outside"
    repl = meta.get("replacement") or {}
    out.append(
        f"\n**Standalone value:** give {vg:.1f}, get {vr:.1f} ({vr - vg:+.1f}); {verdict} the "
        f"{tol:.0%} balance band the trade finder uses. Replacement level (ROS points): "
        + " · ".join(f"{k} {v}" for k, v in repl.items())
        + ". Equal rest-of-season points at different positions are not equal value: the "
        "gap to replacement is what counts.\n"
    )

    mine_after = [p for p in me.player_ids if p not in give] + get
    theirs_after = [p for p in partner.player_ids if p not in get] + give
    b0, l0, c0 = _lineup(lg, table, me.player_ids)
    b1, l1, c1 = _lineup(lg, table, mine_after)
    t0, _, tc0 = _lineup(lg, table, partner.player_ids)
    t1, _, tc1 = _lineup(lg, table, theirs_after)
    horizon = max((r.get("weeks") or 0.0) for r in table.values()) or 1.0
    f0, f1 = _floor_of(table, l0, horizon), _floor_of(table, l1, horizon)
    out.append("## Lineups\n")
    out.append(f"**{me.label}:** {b0:.1f} → {b1:.1f} ROS/wk ({b1 - b0:+.1f}); floor (sum of "
               f"starters' 25th percentiles) {f0:.1f} → {f1:.1f} ({f1 - f0:+.1f}); bench cover "
               f"(two best RB/WR outside the lineup) {c0:.1f} → {c1:.1f}.  ")
    out.append(f"**{partner.label}:** {t0:.1f} → {t1:.1f} ROS/wk ({t1 - t0:+.1f}); bench cover "
               f"{tc0:.1f} → {tc1:.1f}.\n")
    if b1 - b0 > -0.5 and f1 - f0 < -1.0:
        out.append("*Equal or better on the mean, worse on the floor: this swaps consistency for "
                   "ceiling. For the league's strongest lineup that is a cost, not a wash.*\n")
    out.append(f"- my lineup now: {_fmt_lineup(lg, table, l0)}")
    out.append(f"- my lineup after: {_fmt_lineup(lg, table, l1)}\n")

    # Counters: keep what I give, vary what I get from their roster -- at the
    # positions changing hands on either side, so "Diggs plus one of their
    # backs" is a candidate when I am sending a back.
    positions = {lg.players[p]["position"] for p in list(get) + list(give)}
    pool = [p for p in partner.player_ids if p in table
            and lg.players[p].get("position") in positions and p not in get]
    combos = [[p] for p in pool + list(get)]
    combos += [[a, b] for i, a in enumerate(pool + list(get)) for b in (pool + list(get))[i + 1:]]
    scored = []
    for combo in combos:
        if sorted(combo) == sorted(get):
            continue
        v = sum(table[p]["value"] for p in combo)
        band = 0.45 if len(combo) == len(give) else 0.35
        if not _balanced(vg, v, band):
            continue
        after = [p for p in me.player_ids if p not in give] + combo
        bl, lc, cl = _lineup(lg, table, after)
        tl, _, _ = _lineup(lg, table, [p for p in partner.player_ids if p not in combo] + give)
        scored.append((bl - b0, v - vg, combo, cl, tl - t0, _floor_of(table, lc, horizon) - f0))
    scored.sort(key=lambda s: (-s[0], -s[5], -s[1]))
    out.append("## Counters worth asking for\n")
    if scored:
        out.append("| Get instead | Value get | My lineup | My floor | My bench cover | Their lineup |")
        out.append("|:--|--:|--:|--:|--:|--:|")
        for dl, dv, combo, cl, dt, df in scored[:8]:
            out.append(f"| {' + '.join(lg.name(p) for p in combo)} | {vg + dv:.1f} ({dv:+.1f}) | "
                       f"{dl:+.1f}/wk | {df:+.1f} | {cl:.1f} | {dt:+.1f}/wk |")
        out.append("\n*Same players out, different players back, filtered to the balance band and "
                   "sorted by what my lineup gains. A row that also drops their lineup is one they "
                   "will decline; the ones near zero for them are the asks.*")
    else:
        out.append("No alternative package from their roster falls inside the balance band.")

    out.append("\n## News\n")
    for pid in list(give) + list(get):
        p = lg.players[pid]
        blurbs = news.player_news(p.get("rotowire_id"), limit=2)
        if blurbs:
            b = blurbs[0]
            out.append(f"- **{lg.name(pid)}** ({_model.injury_label(p) or 'healthy'}) — "
                       f"_{b['date']}: {b['headline'] or 'no headline'}_ {b['body']}")
        else:
            out.append(f"- **{lg.name(pid)}** ({_model.injury_label(p) or 'healthy'}) — no recent news")
    return "\n".join(out) + "\n"
