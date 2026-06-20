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
world-cup-oracle apply-player-callups --dry-run
world-cup-oracle release-check
world-cup-oracle validate-snapshot --teams data/raw/teams.csv --fixtures data/raw/fixtures.csv
world-cup-oracle import-snapshot --teams data/raw/teams.csv --fixtures data/raw/fixtures.csv
world-cup-oracle simulate-demo --simulations 1000 --seed 26
world-cup-oracle project-bracket
world-cup-oracle cache-url "https://example.com/free-data.csv" --name source.csv
make player-callups
```

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
- `data/cache/` stores free public snapshots before import.

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
