"""Compose the live prediction context from processed data + manual inputs.

Single source of truth for how the app, the bracket projection, and the
prediction snapshots build their predictor, so they all stay in lockstep:
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


def load_raw_inputs(root: Path) -> tuple[list[Team], list[Fixture], dict, dict[str, MatchResult], dict, str]:
    """Return (teams, fixtures, adjustments, locked_results, model_params, source)."""
    tournament = load_processed_or_demo(root / "data" / "processed")
    adjustments = read_team_adjustments(root / "data" / "manual" / "team_adjustments.csv")
    locked = read_match_updates(root / "data" / "manual" / "match_updates.csv")
    params = read_model_params(root / "data" / "processed" / "model_params.json")
    return tournament.teams, tournament.fixtures, adjustments, locked, params, tournament.source


def build_predictor(
    teams: list[Team],
    fixtures: list[Fixture],
    adjustments: dict,
    locked: dict[str, MatchResult],
    params: dict,
) -> MatchPredictor:
    ratings = apply_team_adjustments(MatchPredictor.from_teams(teams).ratings, adjustments)
    ratings = apply_results_to_ratings(ratings, fixtures, locked)
    return MatchPredictor(
        ratings,
        average_total_goals=params.get("average_total_goals", DEFAULT_AVERAGE_TOTAL_GOALS),
    )


def load_live_context(
    root: Path,
) -> tuple[list[Team], list[Fixture], MatchPredictor, dict[str, MatchResult], str]:
    """Return (teams, fixtures, predictor, locked_results, data_source)."""
    teams, fixtures, adjustments, locked, params, source = load_raw_inputs(root)
    predictor = build_predictor(teams, fixtures, adjustments, locked, params)
    return teams, fixtures, predictor, locked, source
