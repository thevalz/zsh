"""Backtest: does the value model predict who scores?

For each week N, every candidate model predicts a value for every skill player
using only what was knowable before week N kicked off -- the consensus prior
and production through week N-1 -- and is then scored against what actually
happened in week N under this league's scoring settings.

Candidates:

- the three-source consensus prior on its own (production weight 0)
- the blend, at several settings of PRODUCTION_PRIOR_GAMES (the live model is
  one of them)
- production only (undefined for week 1, and says so)
- Sleeper's `search_rank` curve -- the model this repo used before PR #3
- Sleeper's own pre-game projection for week N, re-scored under league scoring

One honesty caveat, printed on every table: the consensus sources publish no
history, so the prior is today's list, not the one that existed before week N.
Production and the projection *are* point-in-time.

    python3 -m fantasy.backtest                 # every completed week
    python3 -m fantasy.backtest --weeks 1 2     # include the in-progress week
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from datetime import datetime, timezone

from . import config, model, sleeper, sources
from .model import _league_points, _rank_curve, is_available_body

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(ROOT, "reports", "backtest.md")

# How many of a position start league-wide in a 12-team superflex lineup;
# the top-N hit rate asks whether the model finds *those* players.
TOP_N = {"QB": 24, "RB": 24, "WR": 24, "TE": 12}
PRIOR_GAMES_GRID = (1.0, 2.0, 4.0, 8.0)


# ---------------------------------------------------------------------------
# statistics (stdlib only)
# ---------------------------------------------------------------------------

def _avg_ranks(values: list) -> list:
    """1-based ranks, highest value first, ties averaged."""
    order = sorted(range(len(values)), key=lambda i: -values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        r = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = r
        i = j + 1
    return ranks


def spearman(pred: list, actual: list) -> float | None:
    n = len(pred)
    if n < 3:
        return None
    a, b = _avg_ranks(pred), _avg_ranks(actual)
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = math.sqrt(sum((x - ma) ** 2 for x in a))
    vb = math.sqrt(sum((y - mb) ** 2 for y in b))
    if va == 0 or vb == 0:
        return None
    return cov / (va * vb)


def top_hit_rate(pred: dict, actual: dict, n: int) -> float | None:
    """Share of the actual top-n the model also put in its top-n."""
    if len(actual) < n:
        return None
    top_a = set(sorted(actual, key=lambda k: -actual[k])[:n])
    top_p = set(sorted(pred, key=lambda k: -pred[k])[:n])
    return len(top_a & top_p) / n


# ---------------------------------------------------------------------------
# lineups
# ---------------------------------------------------------------------------

def skill_slots(roster_positions: list) -> list:
    """[(slot, eligible positions)] for the lineup slots the model reasons about."""
    out = []
    for slot in roster_positions:
        if slot in config.SKILL_POSITIONS:
            out.append((slot, (slot,)))
        elif slot == "FLEX":
            out.append((slot, config.FLEX_ELIGIBLE))
        elif slot == "SUPER_FLEX":
            out.append((slot, config.SUPERFLEX_ELIGIBLE))
    return out


def pick_lineup(values: dict, roster: list, players: dict, slots: list) -> list:
    """Fill fixed slots first, then FLEX, then SUPER_FLEX, best value first.

    This is how a manager sets a lineup; it is not a global optimum in theory
    but the eligibility sets nest (FLEX within SUPER_FLEX) so it is one here.
    """
    pool = {pid for pid in roster if players.get(pid, {}).get("position") in config.SKILL_POSITIONS}
    chosen = []
    ordered = sorted(slots, key=lambda s: len(s[1]))     # fixed slots before flex
    for _slot, eligible in ordered:
        best = max(
            (pid for pid in pool if players[pid].get("position") in eligible),
            key=lambda pid: values.get(pid, 0.0),
            default=None,
        )
        if best is not None:
            chosen.append(best)
            pool.discard(best)
    return chosen


# ---------------------------------------------------------------------------
# candidates
# ---------------------------------------------------------------------------

def _mult(p: dict) -> float:
    return config.POSITION_MULTIPLIER.get(p.get("position"), 1.0)


def build_candidates(week: int, season: str, scoring: dict, players: dict,
                     skill: dict, prior_grid) -> tuple[dict, list]:
    """{candidate name: {pid: predicted value}} for week `week`, plus notes."""
    notes = []
    cands: dict = {}

    prior = {pid: model.consensus_value(p) for pid, p in skill.items()}
    cands["consensus prior only"] = {pid: v * _mult(skill[pid]) for pid, v in prior.items() if v > 0}

    cands["sleeper search_rank (old model)"] = {
        pid: _rank_curve(p["search_rank"]) * _mult(p)
        for pid, p in skill.items()
        if p.get("search_rank") and p["search_rank"] < config.UNRANKED_RANK
    }

    try:
        proj = sleeper.weekly_projections(season, week) or {}
        cands[f"sleeper projection wk{week}"] = {
            pid: _league_points(line, scoring)
            for pid, line in proj.items()
            if pid in skill and _league_points(line, scoring) > 0
        }
    except Exception as err:  # noqa: BLE001
        notes.append(f"Sleeper projection for week {week} unavailable ({err}).")

    if week >= 2:
        prod = model.production_table(through_week=week - 1)
        for k in prior_grid:
            vals = {}
            for pid, p in skill.items():
                pr = prod.get(pid)
                base = prior[pid]
                if pr:
                    w = pr["games"] / (pr["games"] + k)
                    base = (1.0 - w) * base + w * pr["value"]
                if base > 0:
                    vals[pid] = base * _mult(p)
            tag = " (live)" if k == config.PRODUCTION_PRIOR_GAMES else ""
            cands[f"blend, prior worth {k:g} games{tag}"] = vals
        cands["production only"] = {
            pid: pr["value"] * _mult(skill[pid]) for pid, pr in prod.items() if pid in skill
        }
    else:
        notes.append(
            "Week 1 has no production to blend, so every blend setting equals the "
            "prior; only the prior, the old search_rank curve and Sleeper's "
            "projection are scored."
        )
    return cands, notes


# ---------------------------------------------------------------------------
# one week
# ---------------------------------------------------------------------------

def evaluate_week(week: int, season: str, current_week: int, scoring: dict,
                  players: dict, roster_positions: list, prior_grid) -> dict:
    skill = {pid: p for pid, p in players.items() if is_available_body(p)}
    lines = sleeper.weekly_stats(season, week, final=week < current_week) or {}
    actual = {
        pid: _league_points(line, scoring)
        for pid, line in lines.items()
        if pid in skill and line.get("gp")
    }
    cands, notes = build_candidates(week, season, scoring, players, skill, prior_grid)

    rows = []
    for name, pred in cands.items():
        row = {"name": name, "pos": {}}
        for pos in config.SKILL_POSITIONS:
            ids = [pid for pid in actual if skill[pid].get("position") == pos]
            a = {pid: actual[pid] for pid in ids}
            pr = {pid: pred.get(pid, 0.0) for pid in ids}
            row["pos"][pos] = {
                "n": len(ids),
                "covered": sum(1 for pid in ids if pid in pred),
                "rho": spearman([pr[pid] for pid in ids], [a[pid] for pid in ids]),
                "hit": top_hit_rate(pr, a, TOP_N[pos]),
            }
        ids = list(actual)
        row["all"] = spearman([pred.get(pid, 0.0) for pid in ids], [actual[pid] for pid in ids])
        rows.append(row)

    # My roster that week: did the model's lineup leave points on the bench?
    lineup = None
    try:
        mine = next(t for t in model.load().teams if t.is_me)
        matchups = sleeper.get(f"league/{config.LEAGUE_ID}/matchups/{week}",
                               f"matchups_{week}", 600) or []
        m = next((x for x in matchups if x.get("roster_id") == mine.roster_id), None)
        if m:
            pts = m.get("players_points") or {}
            roster = [pid for pid in (m.get("players") or []) if pid in skill]
            slots = skill_slots(roster_positions)
            optimal = sum(pts.get(pid, 0.0) for pid in pick_lineup(pts, roster, players, slots))
            started = sum(pts.get(pid, 0.0) for pid in (m.get("starters") or []) if pid in skill)
            by_model = {}
            for name, pred in cands.items():
                got = sum(pts.get(pid, 0.0) for pid in pick_lineup(pred, roster, players, slots))
                by_model[name] = round(optimal - got, 1)
            # Cross-check the scoring recompute against Sleeper's own numbers.
            drift = max((abs(actual.get(pid, 0.0) - pts.get(pid, 0.0))
                         for pid in roster if pid in actual), default=0.0)
            lineup = {"optimal": round(optimal, 1), "started": round(started, 1),
                      "left": by_model, "drift": round(drift, 2)}
    except Exception as err:  # noqa: BLE001
        notes.append(f"Roster lineup check skipped ({err}).")

    return {"week": week, "in_progress": week >= current_week, "n": len(actual),
            "rows": rows, "notes": notes, "lineup": lineup}


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def _f(x, pct=False):
    if x is None:
        return "—"
    return f"{x:.0%}" if pct else f"{x:.3f}"


def render(results: list, prior_note: str, generated: str) -> str:
    out = [f"# {config.LEAGUE_NAME} — value model backtest\n", f"_generated {generated}_\n"]
    out.append(
        "Each candidate predicts every skill player's value using only what was "
        "knowable before the week, and is scored against actual league-scored points "
        "for players who played (`gp = 1`). **ρ** is Spearman rank correlation "
        "(1 = perfect ordering, 0 = noise). **top-N** is the share of the week's "
        "actual top-N at the position the model also had in its top-N "
        f"(N = {', '.join(f'{k} {v}' for k, v in TOP_N.items())}). A player a candidate "
        "does not rank counts as value 0, so coverage gaps cost the candidate.\n"
    )
    out.append(f"> ⚠️ {prior_note}\n")
    for r in results:
        title = f"## Week {r['week']}" + (" — IN PROGRESS, not final" if r["in_progress"] else "")
        out.append(title + "\n")
        out.append(f"{r['n']} skill players logged a game.\n")
        for n in r["notes"]:
            out.append(f"_{n}_\n")
        head = "| Candidate | ALL ρ | " + " | ".join(
            f"{pos} ρ | {pos} top-{TOP_N[pos]} | {pos} cov" for pos in config.SKILL_POSITIONS) + " |"
        out.append(head)
        out.append("|:--|--:|" + "--:|--:|--:|" * len(config.SKILL_POSITIONS))
        for row in r["rows"]:
            cells = [row["name"], _f(row["all"])]
            for pos in config.SKILL_POSITIONS:
                s = row["pos"][pos]
                cov = f"{s['covered']}/{s['n']}"
                cells += [_f(s["rho"]), _f(s["hit"], pct=True), cov]
            out.append("| " + " | ".join(cells) + " |")
        lu = r.get("lineup")
        if lu:
            out.append(
                f"\n**My lineup, week {r['week']}:** optimal skill-slot lineup scored "
                f"{lu['optimal']}; the lineup actually started scored {lu['started']}. "
                "Points each candidate's lineup would have left on the bench:\n"
            )
            out.append("| Candidate | Left on bench |")
            out.append("|:--|--:|")
            for name, left in lu["left"].items():
                out.append(f"| {name} | {left} |")
            out.append(
                f"\n_Scoring recompute vs Sleeper's own points for my roster: max drift "
                f"{lu['drift']} pts._\n"
            )
        out.append("")
    out.append(summary(results))
    return "\n".join(out) + "\n"


def summary(results: list) -> str:
    """Which setting wins, by mean per-position ρ, across weeks with production."""
    lines = ["## Summary\n"]
    scored = [r for r in results if r["week"] >= 2]
    if not scored:
        lines.append(
            "Only week 1 is available, and week 1 cannot separate the blend settings "
            "(no production exists before it). The prior, the old search_rank curve and "
            "Sleeper's projection are compared above; rerun once week 2 is final."
        )
        return "\n".join(lines)
    means: dict = {}
    for r in scored:
        for row in r["rows"]:
            rhos = [row["pos"][pos]["rho"] for pos in config.SKILL_POSITIONS if row["pos"][pos]["rho"] is not None]
            if rhos:
                means.setdefault(row["name"], []).append(sum(rhos) / len(rhos))
    table = sorted(((sum(v) / len(v), k) for k, v in means.items()), reverse=True)
    provisional = any(r["in_progress"] for r in scored)
    lines.append("Mean per-position ρ over " + ", ".join(
        f"week {r['week']}" + (" (in progress)" if r["in_progress"] else "") for r in scored) + ":\n")
    lines.append("| Candidate | mean ρ |")
    lines.append("|:--|--:|")
    for m, k in table:
        lines.append(f"| {k} | {m:.3f} |")
    best_blend = next(((m, k) for m, k in table if k.startswith("blend")), None)
    prior = next((m for m, k in table if k == "consensus prior only"), None)
    old = next((m for m, k in table if k.startswith("sleeper search_rank")), None)
    proj = next((m for m, k in table if k.startswith("sleeper projection")), None)
    if best_blend:
        m, k = best_blend
        verdict = [f"Best blend: **{k}** (ρ {m:.3f})."]
        if prior is not None:
            verdict.append("beats the prior alone" if m > prior else "does **not** beat the prior alone")
        if old is not None:
            verdict.append("beats the old search_rank model" if m > old else "does **not** beat the old search_rank model")
        if proj is not None:
            verdict.append("beats Sleeper's weekly projection" if m > proj else "trails Sleeper's weekly projection")
        lines.append("\n" + verdict[0] + " It " + "; ".join(verdict[1:]) + ".")
        live = f"blend, prior worth {config.PRODUCTION_PRIOR_GAMES:g} games (live)"
        if provisional:
            lines.append(
                "\n**Provisional.** An in-progress week is included, so the ordering above "
                "can still change. Do not retune `PRODUCTION_PRIOR_GAMES` until every scored "
                "week is final."
            )
        elif k != live:
            lines.append(
                f"\nThe live setting is `PRODUCTION_PRIOR_GAMES = {config.PRODUCTION_PRIOR_GAMES:g}`; "
                f"the backtest prefers the setting in **{k}**. Change the constant only with "
                "this number cited, and re-run after every completed week."
            )
        else:
            lines.append("\nThe live setting is the best of the settings tried.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Backtest the value model against past weeks")
    ap.add_argument("--weeks", nargs="*", type=int,
                    help="weeks to score (default: every completed week)")
    ap.add_argument("--prior-games", nargs="*", type=float, default=list(PRIOR_GAMES_GRID),
                    help="PRODUCTION_PRIOR_GAMES settings to compare")
    ap.add_argument("--out", default=DEFAULT_OUT, help="markdown report path")
    args = ap.parse_args(argv)

    state = sleeper.nfl_state()
    season = str(state.get("season") or config.SEASON)
    current = int(state.get("week") or 0)
    if state.get("season_type") != "regular" or current < 1:
        print("no regular-season weeks to score yet", file=sys.stderr)
        return 1
    weeks = args.weeks or list(range(1, current))
    if not weeks:
        print("week 1 is still in progress; pass --weeks 1 to score it anyway", file=sys.stderr)
        return 1
    grid = sorted(set(args.prior_games) | {float(config.PRODUCTION_PRIOR_GAMES)})

    scoring = sleeper.league().get("scoring_settings") or {}
    roster_positions = sleeper.league().get("roster_positions") or []
    players = sleeper.players()
    src = sources.meta()
    prior_note = (
        "The consensus prior is **today's** list (" + "; ".join(src.get("used") or ["none"]) + "), "
        "not the one that existed before each week — no source publishes history. "
        "Production and Sleeper's projection are point-in-time."
    )
    if src.get("failed"):
        prior_note += " Sources unavailable this run: " + "; ".join(src["failed"]) + "."

    results = [evaluate_week(w, season, current, scoring, players, roster_positions, grid)
               for w in weeks if 1 <= w <= current]
    text = render(results, prior_note, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
