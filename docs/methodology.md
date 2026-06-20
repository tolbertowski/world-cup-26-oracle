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

## Historical Ratings Fit

Team ratings are fit from the `martj42/international_results` dataset (every
men's senior international, goals only) via the `fit-ratings` command. The fit
replays the full match history into a running Elo rating whose K-factor is scaled
by match importance (World Cup 1.0, qualifiers/continental finals lower,
friendlies 0.3). Elo carries no explicit time decay because a running rating
already reflects recent form as older results wash out.

In parallel, weighted goals-for and goals-against per team — recency-weighted
with a ~3-year half-life and opponent-strength corrected — produce attacking and
defending multipliers. These are damped and clamped, then written as bounded
`attack_delta`/`defense_delta` rows (the goal-pattern residual on top of what the
overall rating already implies, so strength is not double-counted).

The fitted Elo is written to `seed_rating` in `data/processed/teams.csv`; the
attack/defense residuals are merged into `data/manual/team_adjustments.csv` under
the `international_results:` note prefix, coexisting idempotently with the
`player_callups:` rows. Re-running the command refreshes only those generated
rows. Because the dataset is goals-only, this fit informs ratings, expected
goals, scorelines, and win/draw/loss — but **not** cards or corners.

## In-Tournament Rating Updates

Played results locked in `match_updates.csv` do more than fix group standings:
they nudge each team's overall rating via an Elo update (`apply_results_to_ratings`
in `models.py`), scaled by margin of victory, before any prediction or
simulation. The winner's rating rises and the loser's falls, so an upset carries
forward into the knockout rounds as a real strength change rather than only a
group-table reshuffle. Updates use only real results — simulated games never feed
back — and touch only the overall rating, leaving attack/defense/discipline/tempo
as set by the base model and adjustments.

## Player Call-Up Layer

Player call-ups do not replace team Elo. They generate bounded adjustments on
top of the team baseline, which keeps the model stable while still reacting to
final squads, injuries, and likely starters.

The call-up model scores each player from a direct 0-100 rating when supplied,
or from club strength and market value as fallback signals. Expected role,
minutes share, and availability convert the squad list into an involvement
weight. The model then blends the top 11 core and next 7 depth players into a
squad score, derives attack/defense/tempo position scores, and converts the
difference from the tournament baseline into rating/style deltas.

Generated rating movement is capped at 80 Elo-style points. Attack, defense,
and tempo deltas are also capped, and discipline is left unchanged because the
current player input file does not include reliable card-risk features.

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
They use team tempo, discipline, attacking share, and underdog pressure. The
historical results dataset contains no card, corner, or referee data, so these
projections (and the underlying tempo/discipline factors) remain heuristic and
are not fit from data. Richer modeling would require a club-league dataset that
records corners, cards, and referees.

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
