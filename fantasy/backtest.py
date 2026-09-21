"""Backtest: does the value model predict who scores?

For each week N, every candidate predicts a value for every skill player
using only what was knowable before week N kicked off -- usage and team
defense through week N-1, the outside prior -- and is then scored against
what actually happened in week N under this league's scoring settings.

Candidates:

- the full model at its live settings, and with each part switched off
  (no schedule, no depth-chart/injury projection, no prior)
- the full model across grids of PRIOR_GAMES, USAGE_ALPHA and SCHEDULE_K
- the prior alone, and each prior source alone
- Sleeper's `search_rank` curve -- the model this repo used before PR #3
- Sleeper's own pre-game projection for week N, re-scored under league scoring

Two lineup checks: for every roster in the league, how many points the lineup
each candidate would have set left on the bench, with players who did not
dress that week excluded for every candidate (managers have the injury
report; a candidate that "starts" an inactive is measuring the report, not
the model). The raw, unfiltered number is kept for the live model so the
cost of skipping the injury report stays visible.

One honesty caveat, printed on every table: the outside lists publish no
history, so the prior is today's list, not the one that existed before week
N. FantasyPros' mirror is dated, so its scrape date is printed; usage, team
defense and Sleeper's weekly projections are point-in-time.

    python3 -m fantasy.backtest                 # every completed week
    python3 -m fantasy.backtest --weeks 1 2     # include the in-progress week
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from datetime import datetime, timezone

from . import config, model, sleeper
from .model import _rank_curve, is_available_body
from .usage import league_points

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(ROOT, "reports", "backtest.md")

# How many of a position start league-wide in a 12-team superflex lineup;
# the top-N hit rate asks whether the model finds *those* players.
TOP_N = {"QB": 24, "RB": 24, "WR": 24, "TE": 12}
PRIOR_GAMES_GRID = (1.0, 2.0, 4.0, 8.0)
ALPHA_GRID = (0.4, 0.6, 0.8)
SCHEDULE_K_GRID = (0.0, 0.15, 0.30)
LIVE = "full model (live settings)"


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

    This is how a manager sets a lineup; the eligibility sets nest (FLEX
    within SUPER_FLEX) so it is also the optimum here.
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

def _values(table: dict) -> dict:
    return {pid: r["value"] for pid, r in table.items() if r["value"] > 0}


def build_candidates(week: int, season: str, scoring: dict, skill: dict,
                     grids: dict) -> tuple[dict, list]:
    """{candidate name: {pid: predicted value}} for week `week`, plus notes."""
    notes = []
    cands: dict = {}
    through = week - 1
    huge = 1e9   # prior_games large enough that usage has no say: prior only

    cands[LIVE] = _values(model.value_table(through_week=through))
    if through >= 1:
        cands["full model, no schedule adjustment"] = _values(
            model.value_table(through_week=through, use_schedule=False))
        cands["full model, no depth-chart/injury projection"] = _values(
            model.value_table(through_week=through, use_depth=False))
        cands["usage only (no prior)"] = _values(
            model.value_table(through_week=through, use_prior=False))
        for k in grids["prior_games"]:
            if k == config.PRIOR_GAMES:
                continue
            cands[f"full model, PRIOR_GAMES={k:g}"] = _values(
                model.value_table(through_week=through, prior_games=k))
        for a in grids["alpha"]:
            if a == config.USAGE_ALPHA:
                continue
            cands[f"full model, USAGE_ALPHA={a:g}"] = _values(
                model.value_table(through_week=through, alpha=a))
        for sk in grids["schedule_k"]:
            if sk == config.SCHEDULE_K:
                continue
            cands[f"full model, SCHEDULE_K={sk:g}"] = _values(
                model.value_table(through_week=through, schedule_k=sk))
    else:
        notes.append(
            "Week 1 has no usage to project from, so the full model equals the prior; "
            "the grids and ablations are skipped and only the prior, its sources, the old "
            "search_rank curve and Sleeper's projection are scored."
        )
    cands["prior only"] = _values(model.value_table(through_week=through, prior_games=huge))
    for name in config.PRIOR_SOURCES:
        try:
            vals = _values(model.value_table(through_week=through, prior_games=huge, prior_only=name))
            if vals:
                cands[f"prior: {name} alone"] = vals
        except Exception as err:  # noqa: BLE001
            notes.append(f"{name} alone could not be scored ({err}).")

    cands["sleeper search_rank curve (pre-PR #3 model)"] = {
        pid: _rank_curve(p["search_rank"])
        for pid, p in skill.items()
        if p.get("search_rank") and p["search_rank"] < config.UNRANKED_RANK
    }
    try:
        proj = sleeper.weekly_projections(season, week) or {}
        cands[f"sleeper projection for week {week}"] = {
            pid: league_points(line, scoring)
            for pid, line in proj.items()
            if pid in skill and league_points(line, scoring) > 0
        }
    except Exception as err:  # noqa: BLE001
        notes.append(f"Sleeper projection for week {week} unavailable ({err}).")
    return cands, notes


# ---------------------------------------------------------------------------
# one week
# ---------------------------------------------------------------------------

def evaluate_week(week: int, season: str, current_week: int, scoring: dict,
                  players: dict, roster_positions: list, grids: dict) -> dict:
    skill = {pid: p for pid, p in players.items() if is_available_body(p)}
    lines = sleeper.weekly_stats(season, week, final=week < current_week) or {}
    actual = {
        pid: league_points(line, scoring)
        for pid, line in lines.items()
        if pid in skill and line.get("gp")
    }
    played = set(actual)
    cands, notes = build_candidates(week, season, scoring, skill, grids)

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

    # Every roster: what would each candidate's lineup have left on the bench?
    league = None
    try:
        lg = model.load()
        teams = {t.roster_id: t for t in lg.teams}
        matchups = sleeper.get(f"league/{config.LEAGUE_ID}/matchups/{week}",
                               f"matchups_{week}", 600) or []
        slots = skill_slots(roster_positions)
        live = cands[LIVE]
        team_rows, drift = [], 0.0
        for m in matchups:
            t = teams.get(m.get("roster_id"))
            if not t:
                continue
            pts = m.get("players_points") or {}
            roster = [pid for pid in (m.get("players") or []) if pid in skill]
            active = [pid for pid in roster if pid in played]
            optimal = sum(pts.get(pid, 0.0) for pid in pick_lineup(pts, roster, players, slots))
            started = sum(pts.get(pid, 0.0) for pid in (m.get("starters") or []) if pid in skill)
            left = {name: round(optimal - sum(pts.get(pid, 0.0)
                                              for pid in pick_lineup(pred, active, players, slots)), 1)
                    for name, pred in cands.items()}
            raw_live = round(optimal - sum(pts.get(pid, 0.0)
                                           for pid in pick_lineup(live, roster, players, slots)), 1)
            pred_value = sum(live.get(pid, 0.0) for pid in pick_lineup(live, active, players, slots))
            drift = max(drift, max((abs(actual.get(pid, 0.0) - pts.get(pid, 0.0))
                                    for pid in roster if pid in actual), default=0.0))
            team_rows.append({
                "team": t.label, "is_me": t.is_me, "record": f"{t.wins}-{t.losses}",
                "pred": round(pred_value, 1), "actual": round(m.get("points") or 0.0, 1),
                "optimal": round(optimal, 1), "started": round(started, 1),
                "manager_left": round(optimal - started, 1), "left": left, "raw_live_left": raw_live,
            })
        team_rows.sort(key=lambda r: -r["pred"])
        n = len(team_rows)
        league = {
            "teams": team_rows, "drift": round(drift, 2), "n": n,
            "rho_optimal": spearman([r["pred"] for r in team_rows], [r["optimal"] for r in team_rows]),
            "rho_actual": spearman([r["pred"] for r in team_rows], [r["actual"] for r in team_rows]),
            "mean_left": {name: round(sum(r["left"][name] for r in team_rows) / n, 1) for name in cands} if n else {},
            "mean_manager_left": round(sum(r["manager_left"] for r in team_rows) / n, 1) if n else None,
            "mean_raw_live_left": round(sum(r["raw_live_left"] for r in team_rows) / n, 1) if n else None,
        }
    except Exception as err:  # noqa: BLE001
        notes.append(f"League lineup check skipped ({err}).")

    return {"week": week, "in_progress": week >= current_week, "n": len(actual),
            "rows": rows, "notes": notes, "league": league}


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
                cells += [_f(s["rho"]), _f(s["hit"], pct=True), f"{s['covered']}/{s['n']}"]
            out.append("| " + " | ".join(cells) + " |")
        lg = r.get("league")
        if lg:
            out.append(f"\n### League, week {r['week']}\n")
            out.append(
                "Predicted lineup value is the live model's lineup from each roster's active "
                "players. Optimal is the best skill-slot lineup in hindsight. Left-on-bench "
                "columns exclude inactives for every candidate; the raw column lets the live "
                "model start whoever it likes, injury report unread.\n"
            )
            out.append("| Team | W-L | Pred | Actual | Optimal | Started | Manager left | Live model left | Live, raw | Sleeper proj left |")
            out.append("|:--|:--|--:|--:|--:|--:|--:|--:|--:|--:|")
            proj_name = next((k for k in lg["mean_left"] if k.startswith("sleeper projection")), None)
            for t in lg["teams"]:
                name = f"**{t['team']}**" if t["is_me"] else t["team"]
                out.append(
                    f"| {name} | {t['record']} | {t['pred']} | {t['actual']} | {t['optimal']} | "
                    f"{t['started']} | {t['manager_left']} | {t['left'][LIVE]} | {t['raw_live_left']} | "
                    f"{t['left'].get(proj_name, '—') if proj_name else '—'} |"
                )
            out.append(
                f"\nTeam-level ρ (n = {lg['n']}): predicted vs optimal **{_f(lg['rho_optimal'])}**, "
                f"predicted vs actual total **{_f(lg['rho_actual'])}**. "
                f"Mean points left on bench: managers **{lg['mean_manager_left']}**; "
                f"live model **{lg['mean_left'].get(LIVE)}** (raw, inactives allowed: {lg['mean_raw_live_left']}).\n"
            )
            out.append("| Setter | Mean left on bench |")
            out.append("|:--|--:|")
            for name, v in sorted(lg["mean_left"].items(), key=lambda kv: kv[1]):
                out.append(f"| {name} | {v} |")
            out.append(f"\n_Scoring recompute vs Sleeper's own points across all rosters: max drift {lg['drift']} pts._\n")
        out.append("")
    out.append(summary(results))
    return "\n".join(out) + "\n"


def summary(results: list) -> str:
    lines = ["## Summary\n"]
    scored = [r for r in results if r["week"] >= 2]
    if not scored:
        lines.append(
            "Only week 1 is available, and week 1 cannot separate the model's settings "
            "(no usage exists before it). The prior, its sources, the old search_rank curve and "
            "Sleeper's projection are compared above; rerun once week 2 is final."
        )
        return "\n".join(lines)
    provisional = any(r["in_progress"] for r in scored)
    means: dict = {}
    for r in scored:
        for row in r["rows"]:
            rhos = [row["pos"][pos]["rho"] for pos in config.SKILL_POSITIONS if row["pos"][pos]["rho"] is not None]
            if rhos:
                means.setdefault(row["name"], []).append(sum(rhos) / len(rhos))
    table = sorted(((sum(v) / len(v), k) for k, v in means.items()), reverse=True)
    lines.append("Mean per-position ρ over " + ", ".join(
        f"week {r['week']}" + (" (in progress)" if r["in_progress"] else "") for r in scored) + ":\n")
    lines.append("| Candidate | mean ρ |")
    lines.append("|:--|--:|")
    for m, k in table:
        lines.append(f"| {k} | {m:.3f} |")
    by = dict((k, m) for m, k in table)
    live = by.get(LIVE)
    if live is not None:
        checks = []
        for label, key in (("the prior alone", "prior only"),
                           ("the old search_rank curve", next((k for k in by if k.startswith("sleeper search_rank")), "")),
                           ("Sleeper's weekly projection", next((k for k in by if k.startswith("sleeper projection")), ""))):
            if key in by:
                checks.append(("beats " if live > by[key] else "does **not** beat ") + label + f" ({by[key]:.3f})")
        best_src = max(((m, k) for k, m in by.items() if k.startswith("prior: ")), default=None)
        if best_src:
            checks.append(("beats " if live > best_src[0] else "does **not** beat ")
                          + f"the best single source, {best_src[1][7:]} ({best_src[0]:.3f})")
        lines.append(f"\n**Live model** mean ρ {live:.3f}: " + "; ".join(checks) + ".")
        # Grid winners.
        for prefix, const in (("full model, PRIOR_GAMES=", f"PRIOR_GAMES (live {config.PRIOR_GAMES:g})"),
                              ("full model, USAGE_ALPHA=", f"USAGE_ALPHA (live {config.USAGE_ALPHA:g})"),
                              ("full model, SCHEDULE_K=", f"SCHEDULE_K (live {config.SCHEDULE_K:g})")):
            alts = [(m, k[len(prefix):]) for k, m in by.items() if k.startswith(prefix)]
            if alts:
                best = max(alts + [(live, "live")])
                lines.append(f"- {const}: best is **{best[1]}** at ρ {best[0]:.3f}"
                             + (" — the live setting" if best[1] == "live" else ""))
        for label, key in (("no schedule adjustment", "full model, no schedule adjustment"),
                           ("no depth-chart/injury projection", "full model, no depth-chart/injury projection"),
                           ("no prior", "usage only (no prior)")):
            if key in by:
                d = live - by[key]
                lines.append(f"- {label}: ρ {by[key]:.3f} ({'+' if d >= 0 else ''}{d:.3f} for keeping it)")
    # Source weights the backtest recommends.
    src = {k[7:-6]: m for k, m in by.items() if k.startswith("prior: ") and k.endswith(" alone")}
    if src:
        raw = {k: max(v - 0.5, 0.0) for k, v in src.items()}
        tot = sum(raw.values())
        rec = {k: (v / tot * len(raw)) if tot else 1.0 for k, v in raw.items()}
        lines.append("\nRecommended `PRIOR_SOURCES` weights (∝ ρ − 0.5, mean 1): "
                     + ", ".join(f"{k} {v:.2f} (ρ {src[k]:.3f})" for k, v in rec.items()) + ".")
    if provisional:
        lines.append(
            "\n**Provisional.** An in-progress week is included, so the ordering above can "
            "still change. Do not retune any constant until every scored week is final."
        )
    lines.append("\nLineup verdict, inactives excluded (mean points left on bench per team): " + "; ".join(
        f"week {r['week']}: managers {r['league']['mean_manager_left']}, live model {r['league']['mean_left'].get(LIVE)}"
        for r in results if r.get("league")) + ".")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Backtest the value model against past weeks")
    ap.add_argument("--weeks", nargs="*", type=int,
                    help="weeks to score (default: every completed week)")
    ap.add_argument("--prior-games", nargs="*", type=float, default=list(PRIOR_GAMES_GRID))
    ap.add_argument("--alpha", nargs="*", type=float, default=list(ALPHA_GRID))
    ap.add_argument("--schedule-k", nargs="*", type=float, default=list(SCHEDULE_K_GRID))
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
    grids = {"prior_games": args.prior_games, "alpha": args.alpha, "schedule_k": args.schedule_k}

    league = sleeper.league()
    scoring = league.get("scoring_settings") or {}
    roster_positions = league.get("roster_positions") or []
    players = sleeper.players()

    results = [evaluate_week(w, season, current, scoring, players, roster_positions, grids)
               for w in weeks if 1 <= w <= current]
    src = model.value_meta().get("sources") or {}
    prior_note = (
        "The outside prior is **today's** list (" + "; ".join(src.get("used") or ["none"]) + "), "
        "not the one that existed before each week — no source publishes history. Usage, team "
        "defense grades and Sleeper's weekly projections are point-in-time."
    )
    if src.get("static"):
        prior_note += " Static, weight 0: " + "; ".join(src["static"]) + "."
    if src.get("failed"):
        prior_note += " Unavailable this run: " + "; ".join(src["failed"]) + "."
    text = render(results, prior_note, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
