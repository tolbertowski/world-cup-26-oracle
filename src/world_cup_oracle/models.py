"""Baseline ratings and match prediction models.

The first production-quality model is intentionally explainable: ratings drive
expected goals, expected goals drive scoreline probabilities, and scorelines
drive outcome probabilities. More advanced ML can be trained against this same
interface later without changing the Streamlit app.
"""

from __future__ import annotations

from dataclasses import replace
from math import exp, factorial, sqrt

from world_cup_oracle.domain import (
    Fixture,
    MatchPrediction,
    MatchResult,
    MatchStage,
    MethodOfWin,
    Team,
    TeamRating,
)


DEFAULT_AVERAGE_TOTAL_GOALS = 2.62
# Exponent for pulling each match's expected total toward the tournament
# average: 1.0 pins every match to the average, 0.0 leaves totals unanchored.
TOTAL_GOALS_ANCHORING = 0.45
# Dixon-Coles low-score correlation: a fixed, transparent assumption at the
# literature-standard value. Negative rho boosts 0-0/1-1 (draws) and trims
# 1-0/0-1, correcting independent Poisson's known draw under-prediction.
DEFAULT_DRAW_RHO = -0.10
# Elo bonus for a host nation playing in its own country (fixtures flagged
# non-neutral by the FIFA sync). Slightly below the historical fit's general
# home advantage: a World Cup host crowd is real but shares the stadium with
# a large travelling support.
HOST_ADVANTAGE_ELO = 60.0


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
                attack=attack_from_rating(team.seed_rating),
                defense=defense_from_rating(team.seed_rating),
                discipline=_style_value(team.code, low=0.86, high=1.22),
                tempo=_style_value(team.code[::-1], low=0.88, high=1.18),
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
        average_total_goals: float = DEFAULT_AVERAGE_TOTAL_GOALS,
        max_scoreline_goals: int = 7,
        draw_correlation: float = DEFAULT_DRAW_RHO,
        home_advantage: float = HOST_ADVANTAGE_ELO,
    ) -> None:
        self.ratings = ratings
        self.average_total_goals = average_total_goals
        self.max_scoreline_goals = max_scoreline_goals
        self.draw_correlation = draw_correlation
        self.home_advantage = home_advantage

    @classmethod
    def from_teams(cls, teams: list[Team]) -> "MatchPredictor":
        return cls(EloRatingModel.from_teams(teams).ratings)

    def predict(self, fixture: Fixture) -> MatchPrediction:
        home_rating = self.ratings[fixture.home_team]
        away_rating = self.ratings[fixture.away_team]
        advantage = 0.0 if fixture.neutral_site else self.home_advantage
        home_xg, away_xg = self._expected_goals(home_rating, away_rating, advantage=advantage)
        scorelines = scoreline_distribution(
            home_xg,
            away_xg,
            max_goals=self.max_scoreline_goals,
            rho=self.draw_correlation,
        )
        regulation_home = sum(prob for (home, away), prob in scorelines.items() if home > away)
        regulation_draw = sum(prob for (home, away), prob in scorelines.items() if home == away)
        regulation_away = sum(prob for (home, away), prob in scorelines.items() if home < away)

        if fixture.is_knockout:
            shootout_edge = _logistic((home_rating.rating + advantage - away_rating.rating) / 420.0)
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

        home_corners, away_corners, home_cards, away_cards = self._event_projections(
            home_rating,
            away_rating,
            home_xg,
            away_xg,
        )

        return MatchPrediction(
            fixture=fixture,
            home_win=home_win,
            draw=draw,
            away_win=away_win,
            expected_home_goals=home_xg,
            expected_away_goals=away_xg,
            expected_home_corners=home_corners,
            expected_away_corners=away_corners,
            expected_home_cards=home_cards,
            expected_away_cards=away_cards,
            method_probs=_normalize_method_probs(method_probs),
            scoreline_probs=scorelines,
            explanation=[
                f"{fixture.home_team} rating {home_rating.rating:.0f}",
                f"{fixture.away_team} rating {away_rating.rating:.0f}",
                *( [f"Host advantage: +{advantage:.0f} Elo for {fixture.home_team}"] if advantage else [] ),
                f"Expected goals: {home_xg:.2f}-{away_xg:.2f}",
                f"Corners lean: {home_corners:.1f}-{away_corners:.1f}",
                f"Cards lean: {home_cards:.1f}-{away_cards:.1f}",
            ],
        )

    def _expected_goals(self, home: TeamRating, away: TeamRating, *, advantage: float = 0.0) -> tuple[float, float]:
        rating_gap = (home.rating + advantage - away.rating) / 400.0
        base = self.average_total_goals / 2.0
        home_xg = base * exp(0.36 * rating_gap) * home.attack / max(0.45, away.defense)
        away_xg = base * exp(-0.36 * rating_gap) * away.attack / max(0.45, home.defense)
        # Damped anchor: pull the match total toward the tournament average
        # without forcing it there, so mismatches can total more goals and
        # cagey pairings fewer. TOTAL_GOALS_ANCHORING=1 would pin every match
        # to the average (the old behavior); 0 would not anchor at all.
        scale = (self.average_total_goals / max(0.1, home_xg + away_xg)) ** TOTAL_GOALS_ANCHORING
        return max(0.15, home_xg * scale), max(0.15, away_xg * scale)

    def _event_projections(
        self,
        home: TeamRating,
        away: TeamRating,
        home_xg: float,
        away_xg: float,
    ) -> tuple[float, float, float, float]:
        total_xg = max(0.1, home_xg + away_xg)
        home_attack_share = home_xg / total_xg
        away_attack_share = away_xg / total_xg

        total_corners = 9.7 * ((home.tempo + away.tempo) / 2.0)
        home_corners = total_corners * (0.34 + 0.66 * home_attack_share) * home.tempo
        away_corners = total_corners * (0.34 + 0.66 * away_attack_share) * away.tempo
        corner_scale = total_corners / max(0.1, home_corners + away_corners)

        home_underdog_pressure = max(0.0, away.rating - home.rating) / 850.0
        away_underdog_pressure = max(0.0, home.rating - away.rating) / 850.0
        home_cards = 1.9 * home.discipline * (1.0 + home_underdog_pressure)
        away_cards = 1.9 * away.discipline * (1.0 + away_underdog_pressure)

        return (
            max(1.0, home_corners * corner_scale),
            max(1.0, away_corners * corner_scale),
            max(0.4, home_cards),
            max(0.4, away_cards),
        )


MAX_RATED_GOAL_MARGIN = 4


def margin_of_victory_multiplier(home_goals: int, away_goals: int) -> float:
    """Diminishing Elo bonus for larger winning margins (1.0 for a one-goal game).

    Capped at a four-goal margin so a rout cannot swing ratings without bound —
    a 7-0 is treated as no stronger evidence than a 4-0.
    """
    margin = min(MAX_RATED_GOAL_MARGIN, max(1, abs(home_goals - away_goals)))
    return sqrt(margin)


_STAGE_ORDER = {
    MatchStage.GROUP: 0,
    MatchStage.ROUND_OF_32: 1,
    MatchStage.ROUND_OF_16: 2,
    MatchStage.QUARTER_FINAL: 3,
    MatchStage.SEMI_FINAL: 4,
    MatchStage.THIRD_PLACE: 5,
    MatchStage.FINAL: 6,
}


def _stage_order(stage: MatchStage | None) -> int:
    return _STAGE_ORDER.get(stage, 0) if stage is not None else 0


def _rating_outcome(result: MatchResult) -> float:
    """Match outcome used for rating updates: level after play is always 0.5.

    Unlike standings, a penalty shootout says little about team strength, so a
    knockout tie decided on penalties counts as a draw for rating purposes.
    """
    if result.home_goals > result.away_goals:
        return 1.0
    if result.home_goals < result.away_goals:
        return 0.0
    return 0.5


def apply_results_to_ratings(
    ratings: dict[str, TeamRating],
    fixtures: list[Fixture],
    results: dict[str, MatchResult],
    *,
    k_factor: float = 32.0,
    home_advantage: float = 0.0,
) -> dict[str, TeamRating]:
    """Nudge overall ratings by replaying played (locked) results through Elo.

    Real results move a team's strength so an upset carries into later rounds:
    the winner's rating rises and the loser's falls, scaled by the margin of
    victory. Only ``rating`` changes — attack/defense/discipline/tempo are left
    as set by the base model and adjustments. Results are applied in kickoff
    order so sequential games compound correctly.
    """
    updated = dict(ratings)
    fixtures_by_id = {fixture.match_id: fixture for fixture in fixtures}
    # (stage order, kickoff, match_id, home team, away team, neutral, result):
    # fixture-matched results take teams/venue from the fixture; results that
    # match no fixture (real knockout games — the fixture list only holds the
    # group stage) carry their own team codes and are treated as neutral.
    entries: list[tuple[int, str, str, str, str, bool, MatchResult]] = []
    for match_id, result in results.items():
        if not result.locked:
            continue
        fixture = fixtures_by_id.get(match_id)
        if fixture is not None:
            entries.append(
                (
                    _stage_order(fixture.stage),
                    fixture.kickoff or "",
                    match_id,
                    fixture.home_team,
                    fixture.away_team,
                    fixture.neutral_site,
                    result,
                )
            )
        elif result.home_team and result.away_team:
            entries.append(
                (_stage_order(result.stage), "", match_id, result.home_team, result.away_team, True, result)
            )
    entries.sort(key=lambda entry: entry[:3])
    for _, _, _, home_team, away_team, neutral, result in entries:
        home = updated.get(home_team)
        away = updated.get(away_team)
        if home is None or away is None:
            continue
        advantage = 0.0 if neutral else home_advantage
        expected_home = 1.0 / (1.0 + 10 ** ((away.rating - (home.rating + advantage)) / 400.0))
        movement = k_factor * margin_of_victory_multiplier(result.home_goals, result.away_goals) * (
            _rating_outcome(result) - expected_home
        )
        updated[home_team] = replace(home, rating=home.rating + movement)
        updated[away_team] = replace(away, rating=away.rating - movement)
    return updated


def attack_from_rating(rating: float) -> float:
    """Baseline attacking multiplier implied by an overall Elo-style rating."""
    return max(0.75, 1.0 + (rating - 1500.0) / 2200.0)


def defense_from_rating(rating: float) -> float:
    """Baseline defending multiplier implied by an overall Elo-style rating."""
    return max(0.75, 1.0 + (rating - 1500.0) / 2600.0)


def scoreline_distribution(
    home_xg: float,
    away_xg: float,
    *,
    max_goals: int = 7,
    rho: float = DEFAULT_DRAW_RHO,
) -> dict[tuple[int, int], float]:
    """Poisson scoreline grid with a Dixon-Coles low-score correction.

    Independent Poisson under-predicts draws; the Dixon-Coles tau factor
    reweights the four low-score cells (with negative ``rho`` boosting 0-0 and
    1-1 while trimming 1-0 and 0-1) before normalization. ``rho=0`` reproduces
    the plain independent-Poisson grid.
    """
    probabilities: dict[tuple[int, int], float] = {}
    for home_goals in range(max_goals + 1):
        for away_goals in range(max_goals + 1):
            probability = _poisson_pmf(home_goals, home_xg) * _poisson_pmf(away_goals, away_xg)
            probabilities[(home_goals, away_goals)] = probability * _dixon_coles_tau(
                home_goals,
                away_goals,
                home_xg,
                away_xg,
                rho,
            )
    total = sum(probabilities.values())
    return {score: probability / total for score, probability in probabilities.items()}


def _dixon_coles_tau(home_goals: int, away_goals: int, home_xg: float, away_xg: float, rho: float) -> float:
    if home_goals == 0 and away_goals == 0:
        return max(0.0, 1.0 - home_xg * away_xg * rho)
    if home_goals == 0 and away_goals == 1:
        return max(0.0, 1.0 + home_xg * rho)
    if home_goals == 1 and away_goals == 0:
        return max(0.0, 1.0 + away_xg * rho)
    if home_goals == 1 and away_goals == 1:
        return max(0.0, 1.0 - rho)
    return 1.0


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


def _style_value(code: str, *, low: float, high: float) -> float:
    stable = sum((index + 1) * ord(char) for index, char in enumerate(code))
    return low + (stable % 100) / 99.0 * (high - low)


def _normalize_method_probs(probs: dict[MethodOfWin, float]) -> dict[MethodOfWin, float]:
    total = sum(probs.values())
    if total <= 0:
        return probs
    return {method: probability / total for method, probability in probs.items()}
