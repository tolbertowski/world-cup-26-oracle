# World Cup 26 Oracle

A Python + Streamlit World Cup predictor for match odds, scorelines, cards,
corners, upsets, and bracket chaos, from group-stage drama to MetLife glory.

This is a portfolio MVP: the app runs end to end with deterministic demo data,
manual tournament updates, an explainable rating model, and Monte Carlo bracket
simulation. It is not betting advice.

## Features

- 48-team tournament engine with 12 groups, best third-place qualification, and knockout progression.
- Match predictor with win/draw/loss odds, expected goals, scoreline probabilities, cards, and corners.
- Monte Carlo simulator for champion, finalist, group winner, knockout, and upset probabilities.
- Projected knockout bracket: the deterministic most-likely path from the Round of 32 to the champion.
- Manual-assisted CSV workflow for locking real results as the tournament progresses.
- Streamlit dashboard built for friends and fans rather than technical users.

## Run Locally

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e ".[dev]"
streamlit run app.py
```

Useful commands:

```bash
world-cup-oracle --version
world-cup-oracle init-data
world-cup-oracle sync-fifa --apply
world-cup-oracle fit-ratings --dry-run
world-cup-oracle apply-player-callups --dry-run
world-cup-oracle release-check
world-cup-oracle validate-snapshot --teams data/raw/teams.csv --fixtures data/raw/fixtures.csv
world-cup-oracle import-snapshot --teams data/raw/teams.csv --fixtures data/raw/fixtures.csv
world-cup-oracle simulate-demo --simulations 1000 --seed 26
world-cup-oracle project-bracket
world-cup-oracle snapshot-predictions
world-cup-oracle backtest
world-cup-oracle cache-url "https://example.com/free-data.csv" --name source.csv
make fit-ratings
make player-callups
```

## Deploy (GitHub Pages)

The app deploys as a static site: [stlite](https://github.com/whitphx/stlite)
runs Streamlit in the browser via WebAssembly, so GitHub Pages can host it with
no server. `.github/workflows/deploy.yml` has two independent jobs:

- **`deploy`** (every push to `main`, plus the schedule): re-syncs official
  results into the working tree, builds `_site/` with `scripts/build_site.py`
  (app, package source, and data mounted into the browser filesystem), and
  publishes to Pages. It never commits, so branch protection on `main` can
  never block a deploy — the live page always reflects current FIFA data.
- **`snapshot`** (every 6 hours, or manually via *Run workflow*): syncs results,
  records a timestamped prediction snapshot, and opens/refreshes a single
  rolling pull request (`automated/data-refresh`) so the refreshed data and the
  audit trail land on `main` through review rather than a protected-branch push.

One-time setup: repo Settings → Pages → Source: **GitHub Actions** (or
`gh api repos/<owner>/<repo>/pages -X POST -f build_type=workflow`).

Verify a build locally before pushing:

```bash
python3 scripts/build_site.py
python3 scripts/serve_site.py 8765  # open http://127.0.0.1:8765
```

The deployed app is read-only: predictions react to the data baked in at build
time, and the scheduled sync keeps that data current through the final.

### Model performance

`world-cup-oracle backtest` scores the model's genuine pre-kickoff predictions
against every played result and writes `data/backtest.json` (shown on the app's
**Model Performance** page). On the 2026 World Cup it scored **64% top-pick
accuracy** and an **RPS of 0.18 — about 25% better than a coin-flip baseline**,
in the range of bookmaker-level football forecasts. Because it operates over the
generic prediction interfaces, the same evaluation transfers to another
tournament or a Premier League season by swapping in that competition's fitted
ratings, fixtures, and results (see [docs/methodology.md](docs/methodology.md)).

The tournament is over, so the deploy workflow's 6-hourly schedule is retired;
the site now redeploys only on push to `main` or a manual run. Restoring the
`schedule` trigger in `.github/workflows/deploy.yml` resumes live syncing for a
future event.

### Prediction audit trail

`world-cup-oracle snapshot-predictions` records a timestamped JSON in
`data/snapshots/` — champion/finalist probabilities, the projected bracket, and
win/draw/loss + expected goals for every currently-playable fixture. Files are
immutable, so the git history of `data/snapshots/` is a reviewable record of
what the model predicted at each point in time. The app's **Prediction History**
page charts how champion odds have moved across snapshots.

The scheduled workflow runs `snapshot-predictions --daily`, which guarantees
exactly one snapshot per UTC day (later runs the same day are no-ops), and
restores any not-yet-merged snapshots from the rolling data PR so nothing is
lost between merges.

`world-cup-oracle backfill-snapshots` reconstructs one end-of-day snapshot for
each missing day since the tournament started. Reconstructions are honest: the
same model and seed, restricted to the results whose fixtures had kicked off by
that day, with knockout pairings re-blanked unless they were derivable from
those results. Backfilled snapshots carry `"backfilled": true` so the audit
trail always distinguishes recorded from reconstructed.

## Data Workflow

The app ships with an illustrative offline demo tournament so it can run from a
fresh clone without paid APIs. For portfolio use, sync the official FIFA
calendar first and run `world-cup-oracle release-check`; do not deploy or push a
portfolio release while that check fails.

Manual update files:

- `data/manual/match_updates.csv` locks played matches, penalties, cards, corners, and notes.
- `data/manual/team_adjustments.csv` applies transparent rating/style adjustments.
- `data/manual/player_callups.csv` stores reviewed squad inputs for portfolio-grade player-based rating deltas.
- `data/raw/teams_template.csv` and `data/raw/fixtures_template.csv` show the source CSV shape.
- `data/processed/teams.csv` and `data/processed/fixtures.csv` are loaded by the app when present.
- `data/cache/` stores free public snapshots before import, including the `martj42/international_results` results.csv used by `fit-ratings` to replace placeholder seed ratings with ratings fit from real match history.

See [docs/official-fifa-data.md](docs/official-fifa-data.md) and
[docs/data-pipeline.md](docs/data-pipeline.md) for the import workflow.

## Model Summary

The first model is intentionally explainable:

1. Seed team ratings initialize Elo-style strength.
2. Rating, attack, and defense produce expected goals.
3. Poisson scorelines produce win/draw/loss and method-of-win probabilities.
4. Tempo and discipline style factors produce first-pass corners/cards projections.
5. Monte Carlo runs aggregate tournament outcomes from match-level probabilities.

See [docs/methodology.md](docs/methodology.md) for details and limitations.

## Test

```bash
python3 -m pytest -q
```

The test suite covers tournament rules, data parsing, model sanity checks,
calibration metrics, and seeded simulation reproducibility.
