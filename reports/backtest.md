# Zebras Shooting Heroin — value model backtest

_generated 2026-09-21 00:48 UTC_

Each candidate predicts every skill player's value using only what was knowable before the week, and is scored against actual league-scored points for players who played (`gp = 1`). **ρ** is Spearman rank correlation (1 = perfect ordering, 0 = noise). **top-N** is the share of the week's actual top-N at the position the model also had in its top-N (N = QB 24, RB 24, WR 24, TE 12). A player a candidate does not rank counts as value 0, so coverage gaps cost the candidate.

> ⚠️ The outside prior is **today's** list (FantasyPros ROS ECR via DynastyProcess mirror (scraped 2026-09-18; live page had only 6 experts); ESPN weekly projections summed over weeks 3–17 (371 players); Sleeper weekly projections summed over 15 remaining weeks (437 players)), not the one that existed before each week — no source publishes history. Usage, team defense grades and Sleeper's weekly projections are point-in-time.

## Week 1

418 skill players logged a game.

_Week 1 has no usage to project from, so the full model equals the prior; the grids and ablations are skipped and only the prior, its sources, the old search_rank curve and Sleeper's projection are scored._

| Candidate | ALL ρ | QB ρ | QB top-24 | QB cov | RB ρ | RB top-24 | RB cov | WR ρ | WR top-24 | WR cov | TE ρ | TE top-12 | TE cov |
|:--|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| full model (live settings) | 0.765 | 0.641 | 79% | 39/39 | 0.841 | 79% | 99/99 | 0.763 | 46% | 169/169 | 0.757 | 33% | 111/111 |
| prior only | 0.765 | 0.641 | 79% | 39/39 | 0.841 | 79% | 99/99 | 0.763 | 46% | 169/169 | 0.757 | 33% | 111/111 |
| prior: fantasypros alone | 0.706 | 0.629 | 79% | 39/39 | 0.783 | 79% | 99/99 | 0.702 | 50% | 169/169 | 0.627 | 33% | 111/111 |
| prior: espn alone | 0.767 | 0.614 | 79% | 39/39 | 0.849 | 83% | 99/99 | 0.775 | 46% | 169/169 | 0.754 | 33% | 111/111 |
| prior: sleeper alone | 0.757 | 0.677 | 79% | 39/39 | 0.845 | 79% | 99/99 | 0.750 | 50% | 169/169 | 0.741 | 25% | 111/111 |
| sleeper search_rank curve (pre-PR #3 model) | 0.627 | 0.531 | 75% | 39/39 | 0.759 | 75% | 98/99 | 0.588 | 42% | 169/169 | 0.474 | 17% | 110/111 |
| sleeper projection for week 1 | 0.735 | 0.482 | 71% | 32/39 | 0.774 | 75% | 90/99 | 0.689 | 42% | 154/169 | 0.707 | 33% | 97/111 |

### League, week 1

Predicted lineup value is the live model's lineup from each roster's active players. Optimal is the best skill-slot lineup in hindsight. Left-on-bench columns exclude inactives for every candidate; the raw column lets the live model start whoever it likes, injury report unread.

| Team | W-L | Pred | Actual | Optimal | Started | Manager left | Live model left | Live, raw | Sleeper proj left |
|:--|:--|--:|--:|--:|--:|--:|--:|--:|--:|
| **supervillain** | 2-0 | 471.8 | 184.4 | 173.4 | 167.4 | 6.0 | 6.0 | 6.0 | 11.4 |
| Jersey Rum Hams | 1-1 | 459.5 | 159.2 | 178.1 | 142.2 | 35.9 | 31.6 | 31.6 | 9.9 |
| It's Gonna be Maye | 1-1 | 445.9 | 140.3 | 170.5 | 124.3 | 46.1 | 14.3 | 14.3 | 8.7 |
| To Infinity and Bijan | 2-0 | 416.5 | 193.8 | 188.3 | 179.8 | 8.5 | 0.0 | 0.0 | 8.5 |
| Randikulous | 2-0 | 413.4 | 197.5 | 186.6 | 170.5 | 16.1 | 11.9 | 11.9 | 27.8 |
| East Coast Wins Most | 0-2 | 409.4 | 92.2 | 100.6 | 89.2 | 11.4 | 11.4 | 11.4 | 11.4 |
| mtngoblin | 2-0 | 405.3 | 186.6 | 168.6 | 168.6 | 0.0 | 0.0 | 0.0 | 0.0 |
| Holy Turnovers, Batman! | 0-2 | 391.6 | 86.0 | 117.3 | 76.0 | 41.3 | 41.3 | 41.3 | 31.8 |
| Down to Pound | 0-2 | 377.0 | 122.3 | 102.6 | 97.3 | 5.2 | 5.2 | 13.0 | 11.6 |
| hulleywood | 1-1 | 372.1 | 127.9 | 133.5 | 107.9 | 25.6 | 17.4 | 17.4 | 25.6 |
| Brooklyn Meatpackers | 0-2 | 351.4 | 127.5 | 139.4 | 119.5 | 19.9 | 1.9 | 1.9 | 10.0 |
| NYKatSnatchers | 1-1 | 339.5 | 140.2 | 125.1 | 123.2 | 1.9 | 0.2 | 4.5 | 1.9 |

Team-level ρ (n = 12): predicted vs optimal **0.629**, predicted vs actual total **0.517**. Mean points left on bench: managers **18.2**; live model **11.8** (raw, inactives allowed: 12.8).

| Setter | Mean left on bench |
|:--|--:|
| prior: espn alone | 10.4 |
| prior: sleeper alone | 10.8 |
| full model (live settings) | 11.8 |
| prior only | 11.8 |
| sleeper projection for week 1 | 13.2 |
| prior: fantasypros alone | 14.9 |
| sleeper search_rank curve (pre-PR #3 model) | 19.7 |

_Scoring recompute vs Sleeper's own points across all rosters: max drift 0.0 pts._


## Summary

Only week 1 is available, and week 1 cannot separate the model's settings (no usage exists before it). The prior, its sources, the old search_rank curve and Sleeper's projection are compared above; rerun once week 2 is final.
