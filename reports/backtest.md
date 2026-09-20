# Zebras Shooting Heroin — value model backtest

_generated 2026-09-20 20:46 UTC_

Each candidate predicts every skill player's value using only what was knowable before the week, and is scored against actual league-scored points for players who played (`gp = 1`). **ρ** is Spearman rank correlation (1 = perfect ordering, 0 = noise). **top-N** is the share of the week's actual top-N at the position the model also had in its top-N (N = QB 24, RB 24, WR 24, TE 12). A player a candidate does not rank counts as value 0, so coverage gaps cost the candidate.

> ⚠️ The consensus prior is **today's** list (FantasyPros ROS ECR via DynastyProcess mirror (scraped 2026-09-18; live page had only 6 experts); ESPN season projections (368 players); RotoWire season projections via Sleeper (506 players)), not the one that existed before each week — no source publishes history. Production and Sleeper's projection are point-in-time.

## Week 1

418 skill players logged a game.

_Week 1 has no production to blend, so every blend setting equals the prior; only the prior, the old search_rank curve and Sleeper's projection are scored._

| Candidate | ALL ρ | QB ρ | QB top-24 | QB cov | RB ρ | RB top-24 | RB cov | WR ρ | WR top-24 | WR cov | TE ρ | TE top-12 | TE cov |
|:--|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| consensus prior only | 0.734 | 0.573 | 79% | 39/39 | 0.774 | 79% | 98/99 | 0.713 | 42% | 169/169 | 0.691 | 25% | 110/111 |
| sleeper search_rank (old model) | 0.631 | 0.531 | 75% | 39/39 | 0.759 | 75% | 98/99 | 0.588 | 42% | 169/169 | 0.474 | 17% | 110/111 |
| sleeper projection wk1 | 0.735 | 0.482 | 71% | 32/39 | 0.774 | 75% | 90/99 | 0.689 | 42% | 154/169 | 0.707 | 33% | 97/111 |

**My lineup, week 1:** optimal skill-slot lineup scored 173.4; the lineup actually started scored 167.4. Points each candidate's lineup would have left on the bench:

| Candidate | Left on bench |
|:--|--:|
| consensus prior only | 0.0 |
| sleeper search_rank (old model) | 5.7 |
| sleeper projection wk1 | 11.4 |

_Scoring recompute vs Sleeper's own points for my roster: max drift 0.0 pts._


## Summary

Only week 1 is available, and week 1 cannot separate the blend settings (no production exists before it). The prior, the old search_rank curve and Sleeper's projection are compared above; rerun once week 2 is final.
