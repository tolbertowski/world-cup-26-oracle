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


def evaluate_predictions(pairs: list[tuple[MatchPrediction, MatchResult]]) -> PredictionEvaluation:
    if not pairs:
        return PredictionEvaluation(matches=0, accuracy=0.0, brier_score=0.0, log_loss=0.0)

    accuracy_hits = 0
    brier_total = 0.0
    log_loss_total = 0.0
    for prediction, result in pairs:
        probs = [prediction.home_win, prediction.draw, prediction.away_win]
        actual = _actual_vector(result)
        predicted_index = max(range(3), key=lambda index: probs[index])
        actual_index = max(range(3), key=lambda index: actual[index])
        accuracy_hits += int(predicted_index == actual_index)
        brier_total += sum((prob - outcome) ** 2 for prob, outcome in zip(probs, actual, strict=True))
        log_loss_total += -log(max(1e-9, probs[actual_index]))

    total = len(pairs)
    return PredictionEvaluation(
        matches=total,
        accuracy=accuracy_hits / total,
        brier_score=brier_total / total,
        log_loss=log_loss_total / total,
    )


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
        actual_index = _actual_vector(result).index(1.0)
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


def _actual_vector(result: MatchResult) -> list[float]:
    if result.home_goals > result.away_goals:
        return [1.0, 0.0, 0.0]
    if result.away_goals > result.home_goals:
        return [0.0, 0.0, 1.0]
    return [0.0, 1.0, 0.0]
