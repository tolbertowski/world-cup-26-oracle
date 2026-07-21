"""Model validation metrics for backtests and calibration checks."""

from __future__ import annotations

from dataclasses import dataclass
from math import log

from world_cup_oracle.domain import MatchPrediction, MatchResult


@dataclass(frozen=True, slots=True)
class PredictionEvaluation:
    matches: int
    accuracy: float
    brier_score: float
    log_loss: float
    rps: float  # ranked probability score — the standard ordinal football metric


def score_forecasts(forecasts: list[tuple[list[float], list[float]]]) -> PredictionEvaluation:
    """Score (probabilities, actual one-hot) pairs over ordered home/draw/away.

    Sport- and model-agnostic: any forecaster that emits a home/draw/away
    distribution can be scored here, which is what makes the same evaluation
    reusable across tournaments and leagues (and against baselines).
    """
    if not forecasts:
        return PredictionEvaluation(matches=0, accuracy=0.0, brier_score=0.0, log_loss=0.0, rps=0.0)

    accuracy_hits = 0
    brier_total = 0.0
    log_loss_total = 0.0
    rps_total = 0.0
    for probs, actual in forecasts:
        predicted_index = max(range(3), key=lambda index: probs[index])
        actual_index = max(range(3), key=lambda index: actual[index])
        accuracy_hits += int(predicted_index == actual_index)
        brier_total += sum((prob - outcome) ** 2 for prob, outcome in zip(probs, actual, strict=True))
        log_loss_total += -log(max(1e-9, probs[actual_index]))
        rps_total += _ranked_probability_score(probs, actual)

    total = len(forecasts)
    return PredictionEvaluation(
        matches=total,
        accuracy=accuracy_hits / total,
        brier_score=brier_total / total,
        log_loss=log_loss_total / total,
        rps=rps_total / total,
    )


def evaluate_predictions(pairs: list[tuple[MatchPrediction, MatchResult]]) -> PredictionEvaluation:
    return score_forecasts(
        [([prediction.home_win, prediction.draw, prediction.away_win], _actual_vector(prediction, result))
         for prediction, result in pairs]
    )


def _ranked_probability_score(probs: list[float], actual: list[float]) -> float:
    """RPS over ordered categories; 0 is perfect. Rewards being close in order."""
    score = 0.0
    cum_p = 0.0
    cum_o = 0.0
    for index in range(len(probs) - 1):
        cum_p += probs[index]
        cum_o += actual[index]
        score += (cum_p - cum_o) ** 2
    return score / (len(probs) - 1)


def calibration_bins(
    pairs: list[tuple[MatchPrediction, MatchResult]],
    *,
    bins: int = 5,
) -> list[dict[str, float]]:
    if bins <= 0:
        raise ValueError("bins must be positive.")
    buckets = [
        {"count": 0, "confidence": 0.0, "accuracy": 0.0}
        for _ in range(bins)
    ]
    for prediction, result in pairs:
        probs = [prediction.home_win, prediction.draw, prediction.away_win]
        confidence = max(probs)
        predicted_index = probs.index(confidence)
        actual_index = _actual_vector(prediction, result).index(1.0)
        bucket_index = min(bins - 1, int(confidence * bins))
        buckets[bucket_index]["count"] += 1
        buckets[bucket_index]["confidence"] += confidence
        buckets[bucket_index]["accuracy"] += float(predicted_index == actual_index)

    calibrated: list[dict[str, float]] = []
    for index, bucket in enumerate(buckets):
        count = bucket["count"]
        calibrated.append(
            {
                "bin": index + 1,
                "count": count,
                "confidence": bucket["confidence"] / count if count else 0.0,
                "accuracy": bucket["accuracy"] / count if count else 0.0,
            }
        )
    return calibrated


def _actual_vector(prediction: MatchPrediction, result: MatchResult) -> list[float]:
    # Knockout ties are scored on who advanced (the model folds draws into the
    # eventual winner), so a shootout win counts as a home/away result, not a
    # draw. Group games are scored on the regulation result.
    if prediction.fixture.is_knockout:
        winner = result.winner_side
        if winner == "home":
            return [1.0, 0.0, 0.0]
        if winner == "away":
            return [0.0, 0.0, 1.0]
    if result.home_goals > result.away_goals:
        return [1.0, 0.0, 0.0]
    if result.away_goals > result.home_goals:
        return [0.0, 0.0, 1.0]
    return [0.0, 1.0, 0.0]
