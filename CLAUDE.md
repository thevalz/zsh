# Working in this repo

Fantasy football tooling for **Zebras Shooting Heroin**, a Sleeper league.
Read this before making changes; most of it exists because getting it wrong
already cost someone a day.

## The league (these facts change recommendations)

- Sleeper league `1390899489962741761`, 12 teams, **full PPR**, `$100` FAAB.
- Lineup: `QB / RB / RB / WR / WR / TE / FLEX / FLEX / SUPER_FLEX / K / DEF` + 5 bench.
- **Superflex is the single most important fact.** Nearly every team starts a
  second quarterback, so 12 teams chase ~24 startable QBs and a QB is worth more
  than his raw rank implies. Never reason about this league as if it were 1-QB.
- Trade deadline week 12, playoffs start week 15.
- Our manager is `thevalz`, team **supervillain**, `roster_id` 1.

## The one rule

**Never state a player's status, prognosis, or return timeline without quoting
the blurb you read.** If you cannot cite it, do not say it.

Run `python3 -m fantasy.monitor player --name "..."` and paste the line that
decided it. This is checkable after the fact, which the older wording
("always check the blurb") was not — that version was written into this file
and broken within the hour.

Why it keeps mattering: Sleeper's `injury_status` and `injury_body_part` carry
**no date**. Tank Dell read as `IR (Knee - ACL + MCL)` and was called done for
the season; the injury was December 2024, he is designated to return, and the
Texans had just restructured his deal to keep him. The tag was accurate and the
conclusion drawn from it was wrong. Patrick Mahomes currently reads
`Questionable (Knee - ACL)` for the same reason — the field is a body part, not
a diagnosis, and not a date.

The tools now enforce parts of this so it is harder to skip:

- A designation with no blurb behind it renders as `Questionable · unverified`.
  If you see `unverified`, you have not checked, and you may not conclude.
- Reserve-list players are no longer dropped from the waiver board. They are
  resolved against their news into a `STASH` tier (designated to return) or
  filtered out (finished for the year). The old silent filter hid James Conner,
  a 21-value back sitting unrostered on IR.
- `assess()` reports `blurb_age_days` and flags `stale` past 14 days, so a
  designation whose newest news is weeks old announces itself.

## Branch and PR protocol

Each Claude session is handed a `claude/<slug>-<id>` branch and told to push
only there. Nothing merges on its own and **no PR is opened unless you ask for
one**. Left alone that produces parallel branches that silently diverge — this
repo had four, with one session hand-merging another's work into its own
feature branch, after which "is this on main?" had no clean answer.

So:

1. **Start from `main`.** Your branch is created automatically but not
   necessarily from current `main`. First thing, every session:
   `git fetch origin main && git checkout -B <your-branch> origin/main`
2. **One session → one branch → one PR into `main`.** Ask for the PR explicitly.
3. **Merge and delete promptly.** Two branches living in parallel for days is
   how two different `docs/index.html` files got authored.
4. **Never merge another session's branch into yours.** `main` is the only
   integration point.
5. **Never point automation at a `claude/*` branch.** It breaks silently the day
   that branch is deleted, and silence looks exactly like "nothing happened."

Branch *deletion* returns HTTP 403 from web sessions — pushes work, deletes do
not. Ask the human to delete branches from the GitHub UI.

## Shared mutable state

`fantasy/state/snapshot.json` is **live runtime state tracked in git**. It
records injury designations, depth-chart order and league ownership so each run
can diff against the last; it is committed because containers are ephemeral and
the diff needs a baseline that outlives them.

**Only the GitHub Action writes it.** It runs `fantasy.monitor report` hourly
and commits the snapshot to `main`. The Routines run `fantasy.monitor watch`,
which is read-only: it diffs live state against the committed baseline and
prints the baseline's age under "Since last run".

Why this is the rule and not just the habit: Routine sessions are created with
no git source or outcome, so they have no push credential for `main`. From
2026-09-06 to 2026-09-14 the hourly Routine tried `git push origin main` every
hour and not one snapshot commit ever landed, while it reported the push as a
failed merge. The GitHub Action's copy was the real baseline the whole time.
Do not put a `git push` back into a Routine prompt.

- **Never hand-edit it.** Let the tools own it.
- **A feature branch should leave it alone entirely**, and so should a session
  running `watch`. If `git status` shows it modified after `watch`, something
  passed `--save` that should not have.
- If a session does have to advance it by hand (a rebuilt model, a debug run
  of `report`), `git pull --rebase` first, and on conflict take `main`'s copy.
  Losing one hour of diff beats a wedged branch.
- A baseline older than ~2h means the Action is lagging (GitHub delays
  scheduled runs under load). The report says so; expect some alerts to
  repeat from the previous hour and do not treat them as new.

## What runs on a schedule

Two of these live outside the repo, so you cannot see them in the tree:

| What | Where | When |
|---|---|---|
| Site rebuild + snapshot commit | `.github/workflows/site.yml` | hourly at `:05`, plus Tue + Sun 12:00 UTC |
| Waiver/trade monitor | Claude Routine (account-level) | hourly, `:50` (read-only, after the Action) |
| Sunday inactives + lineup | Claude Routine (account-level) | Sun 11:35am ET |

All three read `main`; only the Action writes to it. If you rename or restructure anything they invoke —
`fantasy.monitor`, `matchups.py`, `build_site.py` — the Routines will keep
running the old commands and fail quietly. Flag it to the human so the Routine
prompts get updated in the same change.

## Repository settings a workflow cannot change

- *Settings → General → Default branch*: `main`. GitHub only fires `schedule`
  triggers from the default branch.
- *Settings → Pages*: Deploy from a branch, **`main`**, folder **`/docs`**.

## Running the tools

No dependencies beyond the Python standard library. No API keys — Sleeper's API
is public and RotoWire is scraped read-only.

```bash
python3 -m fantasy.monitor report              # full waiver + trade analysis
python3 -m fantasy.monitor watch               # hourly mode: what changed
python3 -m fantasy.monitor player --name "..."  # beat-reporter news for one player
python3 -m fantasy.backtest                    # score the value model on completed weeks
python3 matchups.py --full --markdown --out-dir matchups
python3 build_site.py                          # renders docs/ from the reports
```

`fantasy/README.md` documents the scoring model and every tunable weight.

## Analysis lessons worth not relearning

Every error made in this repo so far has one shape: **a structured field was
read as the answer when the answer was in the prose.** The tool was roughly
right each time and the narration went past it. When a number and a sentence
disagree, the sentence wins — go read it.

- **Practice participation beats the designation.** Questionable + full Friday
  practice means he plays. Questionable + DNP is a coin flip.
- **Consensus rank is stale exactly when it matters.** A player whose situation
  changed this week still carries last month's rank. Heavy add volume is the
  market re-ranking him live — but the market also over-reacts to headlines that
  later reports walk back. Read the news before endorsing a bid.
- **Standalone and conditional value are different questions.** On a roster this
  deep a handcuff who will never crack the lineup has no standalone value; only
  the payoff *if* the starter goes down counts. Insure the biggest asset, not
  the best available backup.
- **Waivers cannot fix the WR hole.** The best free-agent receiver has been worse
  than our WR4 all season. Only a trade fixes it — stop proposing claims for it.
- **`assess()`'s serious/likely-minor verdict is keyword matching** and errs both
  ways. It is a pointer to the text, never the answer.
- **A change to how value is computed shows its backtest number.** Run
  `python3 -m fantasy.backtest` before and after, and cite the ρ in the commit.
  Hand-set overrides and "the number feels low" are not evidence; the week 1
  and week 2 tables in `reports/backtest.md` are. An in-progress week is
  labelled as such and never used to retune a constant.
- **Value and lineup are one question.** A lineup of the highest-value active
  players should score the most, and if it does not the value model is wrong.
  The one fair way to check is to exclude players who did not dress that week
  for every candidate; the first version of the check let the model "start"
  inactives, concluded value and lineups were different things, and was wrong
  by about eight points a team.
- **A static outside list is the stale-rank problem in a different coat.**
  Sleeper's season-total projection never moved after August (Tank Dell 52
  points on IR). Every prior source is fingerprinted and dropped to weight 0
  when it stops changing; the header says which.

Silent filters are the dangerous bugs here. An empty result looks like "nothing
there" and a prior fills the vacuum. If you exclude a class of player, say so in
the output.
