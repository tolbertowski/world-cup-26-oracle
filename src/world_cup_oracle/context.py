"""Compose the live prediction context from processed data + manual inputs.

Single source of truth for how the app, the bracket projection, and the
prediction snapshots build their predictor, so the three stay in lockstep:
seed ratings -> team adjustments -> in-tournament result updates -> fitted
average goals.
"""

from __future__ import annotations

from pathlib import Path

from world_cup_oracle.data.io import (
    apply_team_adjustments,
    read_match_updates,
    read_model_params,
    read_team_adjustments,
)
from world_cup_oracle.data.pipeline import load_processed_or_demo
from world_cup_oracle.domain import Fixture, MatchResult, Team
from world_cup_oracle.models import DEFAULT_AVERAGE_TOTAL_GOALS, MatchPredictor, apply_results_to_ratings


def load_live_context(
    root: Path,
) -> tuple[list[Team], list[Fixture], MatchPredictor, dict[str, MatchResult], str]:
    """Return (teams, fixtures, predictor, locked_results, data_source)."""
    tournament = load_processed_or_demo(root / "data" / "processed")
    adjustments = read_team_adjustments(root / "data" / "manual" / "team_adjustments.csv")
    locked = read_match_updates(root / "data" / "manual" / "match_updates.csv")
    params = read_model_params(root / "data" / "processed" / "model_params.json")

    ratings = apply_team_adjustments(MatchPredictor.from_teams(tournament.teams).ratings, adjustments)
    ratings = apply_results_to_ratings(ratings, tournament.fixtures, locked)
    predictor = MatchPredictor(
        ratings,
        average_total_goals=params.get("average_total_goals", DEFAULT_AVERAGE_TOTAL_GOALS),
    )
    return tournament.teams, tournament.fixtures, predictor, locked, tournament.source
