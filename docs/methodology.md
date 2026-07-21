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
grid with a Dixon-Coles low-score correction (fixed rho = -0.10, a transparent
literature-standard assumption) converts expected goals into win, draw, loss,
and likely scoreline probabilities; the correction boosts 0-0 and 1-1 and trims
1-0 and 0-1, fixing independent Poisson's known draw under-prediction.

Each match's expected total is anchored softly to the fitted tournament
average (`model_params.json`, derived from weighted historical results rather
than a hardcoded constant): the total is pulled toward the average with a 0.45
exponent instead of being pinned to it, so mismatches can produce more goals
and cagey pairings fewer.

Host nations playing in their own country get a +60 Elo host advantage. The
FIFA sync flags a fixture as non-neutral when the home-listed side's code
matches the stadium's country, and the predictor applies the bonus to both
expected goals and the knockout shootout edge. All other matches are treated
as neutral-venue.

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

Two guardrails keep single games from over-moving ratings: a tie decided on
penalties counts as a draw (a shootout says little about team strength), and the
margin-of-victory multiplier is capped at a four-goal margin, so a 7-0 moves
ratings no more than a 4-0.

## Final Stages: Locked Knockout Results

The simulator generates its own knockout bracket ids, so real knockout results
cannot be keyed by match id ahead of time. Instead, locked results carry
optional provenance — stage plus home/away team codes (written automatically by
the FIFA sync, or by hand in `match_updates.csv`) — and are matched to bracket
slots by stage and the unordered team pair. Goals and penalties are re-oriented
when the locked orientation differs from the constructed fixture.

This makes locked knockout games authoritative in both the Monte Carlo
simulation and the projected bracket: a played match is never re-simulated, and
its winner feeds the next round. It also sidesteps third-place slotting
approximations, since real pairings override constructed ones. Knockout results
move Elo ratings too (applied in stage order after the group games), so a
knockout upset changes the strength picture for every remaining round. If the
group stage is only partially locked, a simulated run whose bracket produces a
different pairing simply does not match the locked result — real results are
never applied to pairings that did not happen.

The official FIFA sync goes further: it imports the knockout fixtures
themselves, with real pairings for drawn or played rounds and bracket sources
for future ones (`W:<match_id>` = winner of, `RU:<match_id>` = loser of, or a
seed label like `1A`/`3ABCDF`). When those fixtures are present, the simulator
and projected bracket walk the *official* tree — resolving each side from real
teams, earlier winners, or group seeds — instead of reconstructing the bracket
from standings, which can diverge from reality on tie-breaks (fair-play data is
not in the public calendar) and third-place allocation. The template-based
construction remains as the fallback for the offline demo dataset.

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

## Prediction Audit Trail

Every prediction snapshot in `data/snapshots/` is an immutable, timestamped
record of the model's state: champion/finalist probabilities from a seeded
Monte Carlo run, the deterministic projected bracket, and per-match
probabilities for every fixture that was playable at that moment. One snapshot
is recorded per UTC day by the scheduled workflow.

Snapshots for days before recording began are reconstructed by
`backfill-snapshots` and marked `"backfilled": true`. A reconstruction uses the
same model, parameters, and seed, but restricts knowledge to that day: only
results whose fixtures had kicked off by 23:59 UTC are locked, and knockout
pairings are re-blanked unless every bracket source they reference had been
decided — so a backfilled snapshot cannot peek at pairings, hosts, or results
that were still in the future. Because ratings, adjustments, and fitted
parameters were frozen before the tournament (the fit cutoff), the only
time-varying input is the results themselves, which is what makes the
reconstruction faithful.

## Backtest Evaluation

`world-cup-oracle backtest` scores the model against every played result the
honest way: for each match it rebuilds the predictor from **only the results
known before that match kicked off** (a strict kickoff-time cutoff that excludes
the match itself and any simultaneous games), predicts it, and scores the
genuine pre-match forecast. Ratings, adjustments, and fitted parameters were
frozen before the tournament, so this is a true out-of-sample test.

Forecasts are scored over the ordered home/draw/away outcome with:

- **Ranked Probability Score (RPS)** — the standard ordinal football metric
  (lower is better; rewards being close in outcome order);
- **multiclass Brier score** and **log loss**;
- **top-pick accuracy**;
- **calibration** (confidence vs realized hit rate per bin);
- **skill vs a uniform 1/3-1/3-1/3 baseline** — the fraction of the baseline's
  error the model removes.

Knockout ties are scored on who advanced (a shootout win is a home/away result,
not a draw), matching how the model folds draws into an eventual winner. The
report is written to `data/backtest.json` and shown on the app's **Model
Performance** page.

### Reusing this architecture for another competition

Nothing in the backtest, the metrics, or the match model is World-Cup specific —
they operate over the `Team` / `Fixture` / `MatchResult` / `MatchPrediction`
interfaces. To evaluate (or run) the same architecture on a future international
tournament, the Euros, or a **Premier League** season: fit ratings from that
competition's history with `fit-ratings`, supply its fixtures and results in the
standard processed schema (each fixture with a kickoff timestamp), and run
`backtest`. The Elo + Dixon-Coles + in-tournament-update pipeline and every
metric carry over unchanged; only the data source differs.

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
