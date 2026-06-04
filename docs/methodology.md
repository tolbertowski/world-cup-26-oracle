# Methodology

## Scope

World Cup 26 Oracle predicts match-level and tournament-level outcomes for a
fan-facing Streamlit app. The MVP prioritizes transparent assumptions and
repeatable simulation over opaque model complexity.

## Tournament Engine

The engine supports:

- group tables with points, goal difference, goals for, head-to-head tie checks, fair play, and seed fallback;
- top-two automatic qualification from each group;
- eight best third-place teams;
- a data-driven Round of 32 template;
- knockout progression, penalties, final, and third-place match.

The Round of 32 template can be replaced with an official fixture snapshot
without changing the model or app interface.

## Data Pipeline

Raw source CSVs are validated before they become app data. The pipeline checks
team-code uniqueness, fixture references, group shape, duplicate match IDs, and
strict 2026 requirements when requested. Processed files live in
`data/processed/` and override the bundled demo data.

## Match Model

The baseline model uses an Elo-style rating interface. Ratings are transformed
into attack and defense factors, then into expected goals. A Poisson scoreline
grid converts expected goals into win, draw, loss, and likely scoreline
probabilities.

Knockout draws are resolved into eventual win probabilities using rating edge,
with method probabilities split between regulation, extra time, and penalties.

## Projected Bracket

The Bracket view is a single deterministic projection, distinct from the Monte
Carlo odds. It fills group results with each match's modal scoreline (or a
locked real result), takes the resulting qualifiers, and advances the side with
the higher eventual-win probability in every knockout tie through to the
champion. It reuses the same engine and match-model functions as the simulator,
so it represents the model's single most-likely path rather than an aggregate
over thousands of runs. Locked results in `data/manual/match_updates.csv` are
always honored over projections.

## Cards and Corners

Cards and corners are v1 projections, not claims of deep player-level modeling.
They use team tempo, discipline, attacking share, and underdog pressure. This
keeps the app useful while leaving room for richer data later.

## Backtesting

The validation interface reports:

- multiclass Brier score;
- log loss;
- top-pick accuracy;
- confidence-bin calibration.

Historical international match data can be wired into the same `MatchPrediction`
and `MatchResult` pair format.

## Limitations

- Demo teams and fixtures are illustrative until official snapshots are loaded.
- Likely scorer predictions are deferred until reliable free player data is available.
- The app is not betting advice.
- Cached public scraping must respect source terms.
