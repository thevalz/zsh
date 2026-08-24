"""Keeper surplus model for the Zebras Shooting Heroin superflex league.

Surplus = (overall pick you'd pay) - (player's superflex ADP).
Positive means you get the player later than the market takes him.

Keeper rule: keep at two rounds earlier than drafted; waiver adds cost
round 11; players drafted in rounds 1-2 cannot be kept at all.

Usage:  python3 analysis/keeper_math.py
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TEAMS = 12
WAIVER_KEEP_ROUND = 11
MAX_KEEPERS = 2


def load():
    with open(os.path.join(ROOT, "data", "rosters_2025.json")) as f:
        rosters = json.load(f)
    with open(os.path.join(ROOT, "data", "players_sf_adp.json")) as f:
        adp = {p["n"]: (p["a"], p["p"]) for p in json.load(f)["players"]}
    return rosters, adp


def keeper_round(drafted_round):
    """None means ineligible. Waiver adds (round None) cost round 11."""
    if drafted_round is None:
        return WAIVER_KEEP_ROUND
    if drafted_round < 3:
        return None
    return drafted_round - 2


def cost_pick(keep_round, slot=None):
    """Overall pick number a keeper consumes.

    Without a draft slot we use the round midpoint, which keeps the
    comparison slot-agnostic across teams.
    """
    if slot is None:
        return (keep_round - 1) * TEAMS + (TEAMS + 1) / 2
    in_round = slot if keep_round % 2 else TEAMS - slot + 1
    return (keep_round - 1) * TEAMS + in_round


def candidates(roster, adp, slot=None):
    out = []
    for pl in roster:
        kr = keeper_round(pl["round"])
        if kr is None or pl["name"] not in adp:
            continue
        value, pos = adp[pl["name"]]
        cost = cost_pick(kr, slot)
        out.append(
            {
                "name": pl["name"],
                "pos": pos,
                "keep_round": kr,
                "cost_pick": cost,
                "adp": value,
                "surplus": cost - value,
            }
        )
    out.sort(key=lambda c: -c["surplus"])
    return out


def main():
    rosters, adp = load()
    print(f"{'TEAM':<24}{'KEEPER 1':<38}KEEPER 2")
    print("-" * 100)
    for team, roster in rosters.items():
        # slot 2 is our own team; everyone else uses the round midpoint
        slot = 2 if team == "Made America 2024" else None
        top = candidates(roster, adp, slot)[:MAX_KEEPERS]
        cells = [
            f"{c['name']} ({c['pos']}) R{c['keep_round']} adp{c['adp']:.0f} {c['surplus']:+.0f}"
            for c in top
        ]
        print(f"{team[:23]:<24}{cells[0]:<38}{cells[1] if len(cells) > 1 else ''}")


if __name__ == "__main__":
    main()
