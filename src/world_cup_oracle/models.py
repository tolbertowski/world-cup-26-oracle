"""Baseline ratings and match prediction models.

The first production-quality model is intentionally explainable: ratings drive
expected goals, expected goals drive scoreline probabilities, and scorelines
drive outcome probabilities. More advanced ML can be trained against this same
interface later without changing the Streamlit app.
"""

from __future__ import annotations

from dataclasses import replace
from math import exp, factorial

from world_cup_oracle.domain import (
    Fixture,
    MatchPrediction,
    MatchResult,
    MethodOfWin,
    Team,
    TeamRating,
)


class EloRatingModel:
    def __init__(
        self,
        ratings: dict[str, TeamRating],
        *,
        k_factor: float = 28.0,
        home_advantage: float = 0.0,
    ) -> None:
        self.ratings = dict(ratings)
        self.k_factor = k_factor
        self.home_advantage = home_advantage

    @classmethod
    def from_teams(cls, teams: list[Team], *, k_factor: float = 28.0) -> "EloRatingModel":
        ratings = {
            team.code: TeamRating(
                team_code=team.code,
                rating=team.seed_rating,
                attack=max(0.75, 1.0 + (team.seed_rating - 1500.0) / 2200.0),
                defense=max(0.75, 1.0 + (team.seed_rating - 1500.0) / 2600.0),
                recent_form=0.0,
            )
            for team in teams
        }
        return cls(ratings, k_factor=k_factor)

    def expected_score(self, home_team: str, away_team: str) -> float:
        home = self.ratings[home_team].rating + self.home_advantage
        away = self.ratings[away_team].rating
        return 1.0 / (1.0 + 10 ** ((away - home) / 400.0))

    def update_from_result(self, fixture: Fixture, result: MatchResult) -> None:
        expected_home = self.expected_score(fixture.home_team, fixture.away_team)
        actual_home = _actual_home_score(result)
        movement = self.k_factor * (actual_home - expected_home)
        self.ratings[fixture.home_team] = replace(
            self.ratings[fixture.home_team],
            rating=self.ratings[fixture.home_team].rating + movement,
        )
        self.ratings[fixture.away_team] = replace(
            self.ratings[fixture.away_team],
            rating=self.ratings[fixture.away_team].rating - movement,
        )


class MatchPredictor:
    def __init__(
        self,
        ratings: dict[str, TeamRating],
        *,
        average_total_goals: float = 2.62,
        max_scoreline_goals: int = 7,
    ) -> None:
        self.ratings = ratings
        self.average_total_goals = average_total_goals
        self.max_scoreline_goals = max_scoreline_goals

    @classmethod
    def from_teams(cls, teams: list[Team]) -> "MatchPredictor":
        return cls(EloRatingModel.from_teams(teams).ratings)

    def predict(self, fixture: Fixture) -> MatchPrediction:
        home_rating = self.ratings[fixture.home_team]
        away_rating = self.ratings[fixture.away_team]
        home_xg, away_xg = self._expected_goals(home_rating, away_rating)
        scorelines = scoreline_distribution(
            home_xg,
            away_xg,
            max_goals=self.max_scoreline_goals,
        )
        regulation_home = sum(prob for (home, away), prob in scorelines.items() if home > away)
        regulation_draw = sum(prob for (home, away), prob in scorelines.items() if home == away)
        regulation_away = sum(prob for (home, away), prob in scorelines.items() if home < away)

        if fixture.is_knockout:
            shootout_edge = _logistic((home_rating.rating - away_rating.rating) / 420.0)
            home_win = regulation_home + regulation_draw * shootout_edge
            away_win = regulation_away + regulation_draw * (1.0 - shootout_edge)
            draw = 0.0
            method_probs = {
                MethodOfWin.REGULATION: regulation_home + regulation_away,
                MethodOfWin.EXTRA_TIME: regulation_draw * 0.42,
                MethodOfWin.PENALTIES: regulation_draw * 0.58,
            }
        else:
            home_win = regulation_home
            draw = regulation_draw
            away_win = regulation_away
            method_probs = {
                MethodOfWin.REGULATION: regulation_home + regulation_away,
                MethodOfWin.DRAW: regulation_draw,
            }

        return MatchPrediction(
            fixture=fixture,
            home_win=home_win,
            draw=draw,
            away_win=away_win,
            expected_home_goals=home_xg,
            expected_away_goals=away_xg,
            expected_home_corners=0.0,
            expected_away_corners=0.0,
            expected_home_cards=0.0,
            expected_away_cards=0.0,
            method_probs=_normalize_method_probs(method_probs),
            scoreline_probs=scorelines,
            explanation=[
                f"{fixture.home_team} rating {home_rating.rating:.0f}",
                f"{fixture.away_team} rating {away_rating.rating:.0f}",
                f"Expected goals: {home_xg:.2f}-{away_xg:.2f}",
            ],
        )

    def _expected_goals(self, home: TeamRating, away: TeamRating) -> tuple[float, float]:
        rating_gap = (home.rating - away.rating) / 400.0
        base = self.average_total_goals / 2.0
        home_xg = base * exp(0.36 * rating_gap) * home.attack / max(0.45, away.defense)
        away_xg = base * exp(-0.36 * rating_gap) * away.attack / max(0.45, home.defense)
        scale = self.average_total_goals / max(0.1, home_xg + away_xg)
        return max(0.15, home_xg * scale), max(0.15, away_xg * scale)


def scoreline_distribution(
    home_xg: float,
    away_xg: float,
    *,
    max_goals: int = 7,
) -> dict[tuple[int, int], float]:
    probabilities: dict[tuple[int, int], float] = {}
    for home_goals in range(max_goals + 1):
        for away_goals in range(max_goals + 1):
            probabilities[(home_goals, away_goals)] = _poisson_pmf(home_goals, home_xg) * _poisson_pmf(
                away_goals,
                away_xg,
            )
    total = sum(probabilities.values())
    return {score: probability / total for score, probability in probabilities.items()}


def _actual_home_score(result: MatchResult) -> float:
    if result.home_goals > result.away_goals:
        return 1.0
    if result.home_goals < result.away_goals:
        return 0.0
    if result.home_penalties is None or result.away_penalties is None:
        return 0.5
    return 1.0 if result.home_penalties > result.away_penalties else 0.0


def _poisson_pmf(k: int, rate: float) -> float:
    return exp(-rate) * rate**k / factorial(k)


def _logistic(value: float) -> float:
    return 1.0 / (1.0 + exp(-value))


def _normalize_method_probs(probs: dict[MethodOfWin, float]) -> dict[MethodOfWin, float]:
    total = sum(probs.values())
    if total <= 0:
        return probs
    return {method: probability / total for method, probability in probs.items()}
