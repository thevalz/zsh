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

Both the GitHub Action and the hourly Routine write it to `main`. Therefore:

- **Never hand-edit it.** Let the tools own it.
- **Always `git pull --rebase` before pushing.**
- **On conflict, take `main`'s copy.** Losing one hour of diff beats a wedged
  branch.
- A feature branch that only touches code should leave it alone entirely.

## What runs on a schedule

Two of these live outside the repo, so you cannot see them in the tree:

| What | Where | When |
|---|---|---|
| Site rebuild | `.github/workflows/site.yml` | every 6h, plus Tue + Sun 12:00 UTC |
| Waiver/trade monitor | Claude Routine (account-level) | hourly, `:23` |
| Sunday inactives + lineup | Claude Routine (account-level) | Sun 11:35am ET |

All three target `main`. If you rename or restructure anything they invoke —
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
python3 matchups.py --full --markdown --out-dir matchups
python3 build_site.py                          # renders docs/ from the reports
```

`fantasy/README.md` documents the scoring model and every tunable weight.

## Analysis lessons worth not relearning

**A designation is not a diagnosis.** Sleeper tags a cramp and a hyperextended
knee both `Questionable`. Scoring the tag alone once put a third-string back at
the top of the waiver board on the strength of a starter who was fine. Always
check the blurb (`monitor player --name`) before acting on an injury.

The `serious` / `likely minor` verdict is keyword matching over prose, so it
errs in both directions — it lands on `unclear` often, and a healthy player
whose blurb mentions an old injury can read `serious`. Treat it as a pointer to
the text, never as the answer. Read the blurb yourself.

**Practice participation beats the designation.** Questionable + full Friday
practice means he plays. Questionable + DNP is a coin flip.

**Consensus rank is stale exactly when it matters.** A player whose situation
changed this week still carries last month's rank. Heavy waiver-add volume is
the market re-ranking him in real time — but the market also over-reacts to
headlines that later reports walk back, so read the news before endorsing a bid.

**Standalone value and conditional value are different questions.** For a roster
this deep, a handcuff who will never crack the lineup has no standalone value;
only the payoff *if* the starter goes down matters. Insure the biggest asset,
not the best available backup.

**Waivers cannot fix this roster's WR hole.** The best free-agent receiver has
been worse than our WR4 all season. That problem is solvable only by trade —
don't keep proposing waiver claims for it.
