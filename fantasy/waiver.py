"""Waiver-wire opportunity engine.

The question this answers is not "who is good" -- it is "whose workload is
about to change, and does anybody else know yet". A free agent earns a spot on
the board when the men in front of him are hurt, or when he is the direct
handcuff to a starter somebody in this league is relying on.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import config, model, sleeper
from .model import League, player_value, vacancy


@dataclass
class Candidate:
    pid: str
    name: str
    position: str
    nfl_team: str
    score: float = 0.0
    opportunity: float = 0.0
    handcuff_value: float = 0.0
    market_adds: int = 0
    my_stake: bool = False
    starter_available: bool = False
    own_value: float = 0.0
    reasons: list = field(default_factory=list)
    blocks: list = field(default_factory=list)   # who is ahead of him
    tier: str = "SPECULATIVE"

    @property
    def label(self) -> str:
        return f"{self.name} ({self.position}-{self.nfl_team})"


def _market(limit: int = 400) -> dict:
    try:
        rows = sleeper.trending("add", lookback_hours=24, limit=limit)
    except RuntimeError:
        return {}
    return {r["player_id"]: r.get("count", 0) for r in rows}


def build_board(lg: League, charts: dict | None = None, top: int = 15) -> list:
    """Score every unrostered skill player and return the best opportunities."""
    charts = charts or model.depth_charts(lg.players)
    rostered = lg.rostered
    market = _market()
    my_needs = _my_thin_positions(lg)

    board = []
    for pid, p in lg.players.items():
        if pid in rostered or not model.is_available_body(p):
            continue
        # A free agent who is himself out for the season is not an opportunity.
        if p.get("injury_status") in ("IR", "PUP", "Sus", "DNR"):
            continue

        cand = Candidate(
            pid=pid,
            name=lg.name(pid),
            position=p["position"],
            nfl_team=p["team"],
            market_adds=int(market.get(pid, 0)),
            own_value=round(player_value(p), 1),
        )

        ahead = model.players_ahead(pid, lg.players, charts)
        cand.blocks = [a for a, _ in ahead]

        pos = p["position"]
        pos_factor = config.INHERITANCE_BY_POSITION.get(pos, 0.5)
        cred = model.credibility(p)

        opportunity = 0.0
        handcuff = 0.0
        for ahead_pid, distance in ahead:
            share = config.INHERITANCE.get(distance, 0.0)
            if not share:
                continue
            ahead_p = lg.players.get(ahead_pid) or {}
            starter_value = player_value(ahead_p)
            if starter_value <= 0:
                continue
            owner = lg.owner_of(ahead_pid)
            transfer = share * pos_factor * cred

            hurt = vacancy(ahead_p)
            if hurt:
                gain = starter_value * transfer * hurt
                if owner and owner.is_me:
                    gain *= config.W_OWN_STAKE
                    cand.my_stake = True
                opportunity += gain
                cand.reasons.append(
                    f"{lg.name(ahead_pid)} is {ahead_p.get('injury_status')}"
                    + (" — MY player" if owner and owner.is_me else "")
                )
            elif distance == 1 and pos in config.HANDCUFF_POSITIONS:
                # Healthy starter in front: this is insurance, not opportunity.
                ins = starter_value * transfer
                if owner and owner.is_me:
                    ins *= config.W_OWN_STAKE
                    cand.my_stake = True
                    cand.reasons.append(f"direct handcuff to MY {lg.name(ahead_pid)}")
                elif owner:
                    cand.reasons.append(
                        f"handcuff to {lg.name(ahead_pid)} ({owner.label})"
                    )
                    ins *= 0.5   # somebody else's insurance is worth less to me
                else:
                    cand.reasons.append(f"handcuff to {lg.name(ahead_pid)} (free agent)")
                    ins *= 0.3
                handcuff += ins

        if not ahead and player_value(p) > 0:
            # Listed as his team's starter and nobody rosters him. Worth
            # knowing about, but it is standing quality rather than a
            # workload change, so it is damped and tiered separately below.
            opportunity += player_value(p) * 0.35
            cand.starter_available = True
            cand.reasons.append("starting for his NFL team and unrostered")

        cand.opportunity = opportunity
        cand.handcuff_value = handcuff

        fit = config.W_FIT * (opportunity + handcuff) if pos in my_needs else 0.0
        if fit:
            cand.reasons.append(f"fills my thin {pos} room")

        heat = config.W_MARKET * min(cand.market_adds / config.MARKET_HOT, 1.0) * 10.0

        cand.score = (
            config.W_OPPORTUNITY * opportunity
            + config.W_HANDCUFF * handcuff
            + fit
            - heat
        )
        cand.tier = _tier(cand)

        if cand.score >= config.MIN_SCORE:
            board.append(cand)

    board.sort(key=lambda c: -c.score)

    # Cap each position so a deep pool at one spot (usually TE, where 12 teams
    # start one) cannot crowd the genuine opportunities off the board.
    per_pos: dict = {}
    trimmed = []
    for c in board:
        if per_pos.get(c.position, 0) >= 4:
            continue
        per_pos[c.position] = per_pos.get(c.position, 0) + 1
        trimmed.append(c)
    return trimmed[:top]


def _tier(c: Candidate) -> str:
    """URGENT   -- the job is open right now.
    BUY EARLY   -- the job is not open, and the market has not priced the risk.
    INSURANCE   -- protects a starter of mine specifically.
    STARTER FA  -- simply a good player nobody rostered.
    """
    starter_hurt = any(
        "is Out" in r or "is IR" in r or "is Doubtful" in r for r in c.reasons
    )
    if starter_hurt and c.opportunity >= 8:
        return "URGENT"
    if c.starter_available and not c.blocks:
        return "STARTER FA"
    if c.opportunity >= 6 and c.market_adds < config.MARKET_HOT * 0.25:
        return "BUY EARLY"
    if c.handcuff_value >= 8 and c.market_adds < config.MARKET_HOT * 0.15:
        return "BUY EARLY"
    if c.my_stake:
        return "INSURANCE"
    return "SPECULATIVE"


def _my_thin_positions(lg: League) -> set:
    """Positions where my own starters are below the league's median."""
    from .trades import league_profiles

    profiles = league_profiles(lg)
    mine = profiles[lg.me.roster_id]
    thin = set()
    for pos in config.SKILL_POSITIONS:
        others = sorted(profiles[t.roster_id][pos]["starter_value"] for t in lg.teams)
        median = others[len(others) // 2]
        if mine[pos]["starter_value"] < median:
            thin.add(pos)
    return thin


def handcuff_report(lg: League, charts: dict | None = None) -> list:
    """For every RB/QB I roster, who inherits the job if he goes down?

    This is the 'should I buy Skattebo's backup' question asked systematically
    across my whole roster instead of one player at a time.
    """
    charts = charts or model.depth_charts(lg.players)
    rostered = lg.rostered
    rows = []
    for pid in lg.roster_of(lg.me):
        p = lg.players.get(pid) or {}
        if p.get("position") not in ("RB", "QB", "TE"):
            continue
        if player_value(p) < 5:
            continue
        backups = model.backups_of(pid, lg.players, charts, limit=2)
        entries = []
        for b in backups:
            owner = lg.owner_of(b)
            bp = lg.players.get(b) or {}
            entries.append(
                {
                    "pid": b,
                    "label": lg.describe(b),
                    "available": b not in rostered,
                    "owner": owner.label if owner else None,
                    "injury": bp.get("injury_status"),
                    "credibility": round(model.credibility(bp), 2),
                }
            )
        rows.append(
            {
                "pid": pid,
                "label": lg.describe(pid),
                "value": round(player_value(p), 1),
                "injury": p.get("injury_status"),
                "backups": entries,
            }
        )
    rows.sort(key=lambda r: -r["value"])
    return rows
