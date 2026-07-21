"""Backtest the match model against played results.

For every played fixture, rebuild the predictor from only the results that were
known *before* that match kicked off, predict it, and score the genuine
pre-match forecast. The scoring is sport-agnostic (home/draw/away over the
`MatchPrediction`/`MatchResult` interface), so the same backtest transfers to
any tournament or league whose fixtures and results are loaded in the standard
schema — a future World Cup, the Euros, or a Premier League season.
"""

from __future__ import annotations

from pathlib import Path

from world_cup_oracle.context import build_predictor, load_raw_inputs
from world_cup_oracle.domain import Fixture, MatchPrediction, MatchResult, Team
from world_cup_oracle.metrics import PredictionEvaluation, calibration_bins, evaluate_predictions, score_forecasts
from world_cup_oracle.snapshots import _parse_kickoff, results_as_of

UNIFORM = [1 / 3, 1 / 3, 1 / 3]


def backtest_pairs(
    teams: list[Team],
    fixtures: list[Fixture],
    adjustments: dict,
    locked: dict[str, MatchResult],
    params: dict,
) -> list[tuple[MatchPrediction, MatchResult]]:
    """Genuine pre-kickoff (prediction, result) pairs for every played match."""
    pairs: list[tuple[MatchPrediction, MatchResult]] = []
    for fixture in sorted(fixtures, key=lambda item: item.kickoff or ""):
        result = locked.get(fixture.match_id)
        kickoff = _parse_kickoff(fixture.kickoff)
        if result is None or kickoff is None or not fixture.home_team or not fixture.away_team:
            continue
        prior = results_as_of(fixtures, locked, kickoff, inclusive=False)
        predictor = build_predictor(teams, fixtures, adjustments, prior, params)
        if fixture.home_team not in predictor.ratings or fixture.away_team not in predictor.ratings:
            continue
        pairs.append((predictor.predict(fixture), result))
    return pairs


def run_backtest(root: Path) -> dict:
    teams, fixtures, adjustments, locked, params, source = load_raw_inputs(root)
    pairs = backtest_pairs(teams, fixtures, adjustments, locked, params)

    overall = evaluate_predictions(pairs)
    uniform = score_forecasts([(UNIFORM, _actual(prediction, result)) for prediction, result in pairs])

    by_stage: dict[str, dict] = {}
    for stage in sorted({prediction.fixture.stage.value for prediction, _ in pairs}):
        stage_pairs = [pair for pair in pairs if pair[0].fixture.stage.value == stage]
        by_stage[stage] = _as_dict(evaluate_predictions(stage_pairs))

    return {
        "data_source": source,
        "overall": _as_dict(overall),
        "baseline_uniform": _as_dict(uniform),
        "skill_vs_uniform": {
            "rps": _skill(overall.rps, uniform.rps),
            "brier": _skill(overall.brier_score, uniform.brier_score),
            "log_loss": _skill(overall.log_loss, uniform.log_loss),
        },
        "by_stage": by_stage,
        "calibration": calibration_bins(pairs, bins=5),
    }


def _skill(model: float, baseline: float) -> float:
    """Fraction of the baseline's error removed (1 = perfect, 0 = no better)."""
    return round(1.0 - (model / baseline), 4) if baseline else 0.0


def _as_dict(evaluation: PredictionEvaluation) -> dict:
    return {
        "matches": evaluation.matches,
        "accuracy": round(evaluation.accuracy, 4),
        "brier_score": round(evaluation.brier_score, 4),
        "log_loss": round(evaluation.log_loss, 4),
        "rps": round(evaluation.rps, 4),
    }


def _actual(prediction: MatchPrediction, result: MatchResult) -> list[float]:
    from world_cup_oracle.metrics import _actual_vector

    return _actual_vector(prediction, result)
