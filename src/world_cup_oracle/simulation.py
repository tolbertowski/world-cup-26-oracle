"""Monte Carlo tournament simulation."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from random import Random

from world_cup_oracle.domain import (
    Fixture,
    MatchResult,
    MatchStage,
    MethodOfWin,
    SimulationSummary,
    Team,
)
from world_cup_oracle.engine import (
    build_next_round_fixtures,
    build_round_of_32,
    calculate_group_stage,
)
from world_cup_oracle.models import MatchPredictor


@dataclass(frozen=True, slots=True)
class SimulationRun:
    champion: str
    finalists: tuple[str, str]
    group_winners: dict[str, str]
    knockout_teams: set[str]
    upset_labels: list[str] = field(default_factory=list)
    results: dict[str, MatchResult] = field(default_factory=dict)


class TournamentSimulator:
    def __init__(
        self,
        teams: list[Team],
        fixtures: list[Fixture],
        predictor: MatchPredictor,
        *,
        seed: int | None = None,
    ) -> None:
        self.teams = teams
        self.fixtures = fixtures
        self.predictor = predictor
        self.random = Random(seed)

    def simulate_once(self, locked_results: dict[str, MatchResult] | None = None) -> SimulationRun:
        locked_results = locked_results or {}
        results = dict(locked_results)
        group_fixtures = [fixture for fixture in self.fixtures if fixture.stage == MatchStage.GROUP]

        for fixture in group_fixtures:
            if fixture.match_id in results:
                continue
            results[fixture.match_id] = self._simulate_group_match(fixture)

        group_stage = calculate_group_stage(self.teams, group_fixtures, results)
        group_winners = {
            group: rows[0].team_code for group, rows in group_stage.standings.items() if rows
        }
        knockout_teams = {qualified.team_code for qualified in group_stage.qualified}
        round_of_32 = build_round_of_32(group_stage.qualified)

        upset_labels: list[str] = []
        r32_winners, r32_losers = self._simulate_knockout_round(round_of_32, results, upset_labels)
        r16_winners, r16_losers = self._simulate_knockout_round(
            build_next_round_fixtures(r32_winners, MatchStage.ROUND_OF_16, "R16"),
            results,
            upset_labels,
        )
        qf_winners, qf_losers = self._simulate_knockout_round(
            build_next_round_fixtures(r16_winners, MatchStage.QUARTER_FINAL, "QF"),
            results,
            upset_labels,
        )
        sf_winners, sf_losers = self._simulate_knockout_round(
            build_next_round_fixtures(qf_winners, MatchStage.SEMI_FINAL, "SF"),
            results,
            upset_labels,
        )
        final_fixture = build_next_round_fixtures(sf_winners, MatchStage.FINAL, "F")[0]
        finalists = (final_fixture.home_team, final_fixture.away_team)
        final_winners, final_losers = self._simulate_knockout_round(
            [final_fixture],
            results,
            upset_labels,
        )

        third_place_fixture = Fixture(
            "TP-01",
            MatchStage.THIRD_PLACE,
            home_team=sf_losers[0],
            away_team=sf_losers[1],
        )
        self._simulate_knockout_round([third_place_fixture], results, upset_labels)

        champion = final_winners[0]
        finalists = tuple(sorted((champion, final_losers[0])))  # stable aggregate key ordering
        return SimulationRun(
            champion=champion,
            finalists=(final_fixture.home_team, final_fixture.away_team),
            group_winners=group_winners,
            knockout_teams=knockout_teams,
            upset_labels=upset_labels,
            results=results,
        )

    def run(
        self,
        simulations: int = 1000,
        locked_results: dict[str, MatchResult] | None = None,
    ) -> SimulationSummary:
        runs = [self.simulate_once(locked_results) for _ in range(simulations)]
        return summarize_runs(runs, simulations)

    def _simulate_group_match(self, fixture: Fixture) -> MatchResult:
        prediction = self.predictor.predict(fixture)
        home_goals, away_goals = _weighted_choice(self.random, prediction.scoreline_probs)
        return MatchResult(
            match_id=fixture.match_id,
            home_goals=home_goals,
            away_goals=away_goals,
            home_yellow_cards=round(prediction.expected_home_cards),
            away_yellow_cards=round(prediction.expected_away_cards),
            home_corners=round(prediction.expected_home_corners),
            away_corners=round(prediction.expected_away_corners),
            method=MethodOfWin.REGULATION if home_goals != away_goals else MethodOfWin.DRAW,
            locked=True,
        )

    def _simulate_knockout_round(
        self,
        fixtures: list[Fixture],
        results: dict[str, MatchResult],
        upset_labels: list[str],
    ) -> tuple[list[str], list[str]]:
        winners: list[str] = []
        losers: list[str] = []
        for fixture in fixtures:
            if fixture.match_id in results:
                result = results[fixture.match_id]
                winner_side = result.winner_side
            else:
                result = self._simulate_knockout_match(fixture)
                results[fixture.match_id] = result
                winner_side = result.winner_side
            if winner_side is None:
                raise ValueError(f"Knockout result {fixture.match_id} has no winner.")
            winner = fixture.home_team if winner_side == "home" else fixture.away_team
            loser = fixture.away_team if winner_side == "home" else fixture.home_team
            winners.append(winner)
            losers.append(loser)
            if self._is_upset(winner, loser):
                upset_labels.append(f"{winner} over {loser}")
        return winners, losers

    def _simulate_knockout_match(self, fixture: Fixture) -> MatchResult:
        prediction = self.predictor.predict(fixture)
        home_goals, away_goals = _weighted_choice(self.random, prediction.scoreline_probs)
        if home_goals > away_goals:
            home_penalties = away_penalties = None
            method = MethodOfWin.REGULATION
        elif away_goals > home_goals:
            home_penalties = away_penalties = None
            method = MethodOfWin.REGULATION
        else:
            home_eventual_share = prediction.home_win / max(0.01, prediction.home_win + prediction.away_win)
            home_advances = self.random.random() < home_eventual_share
            home_penalties, away_penalties = (5, 4) if home_advances else (4, 5)
            method = MethodOfWin.PENALTIES

        return MatchResult(
            match_id=fixture.match_id,
            home_goals=home_goals,
            away_goals=away_goals,
            home_penalties=home_penalties,
            away_penalties=away_penalties,
            home_yellow_cards=round(prediction.expected_home_cards),
            away_yellow_cards=round(prediction.expected_away_cards),
            home_corners=round(prediction.expected_home_corners),
            away_corners=round(prediction.expected_away_corners),
            method=method,
            locked=True,
        )

    def _is_upset(self, winner: str, loser: str) -> bool:
        winner_rating = self.predictor.ratings[winner].rating
        loser_rating = self.predictor.ratings[loser].rating
        return loser_rating - winner_rating >= 100.0


def run_monte_carlo(
    teams: list[Team],
    fixtures: list[Fixture],
    predictor: MatchPredictor,
    *,
    simulations: int = 1000,
    seed: int | None = 26,
    locked_results: dict[str, MatchResult] | None = None,
) -> SimulationSummary:
    simulator = TournamentSimulator(teams, fixtures, predictor, seed=seed)
    return simulator.run(simulations=simulations, locked_results=locked_results)


def summarize_runs(runs: list[SimulationRun], simulations: int | None = None) -> SimulationSummary:
    total = simulations or len(runs)
    champion_counts = Counter(run.champion for run in runs)
    finalist_counts: Counter[str] = Counter()
    group_winner_counts: dict[str, Counter[str]] = defaultdict(Counter)
    knockout_counts: Counter[str] = Counter()
    upset_counts: Counter[str] = Counter()

    for run in runs:
        finalist_counts.update(run.finalists)
        for group, winner in run.group_winners.items():
            group_winner_counts[group][winner] += 1
        knockout_counts.update(run.knockout_teams)
        upset_counts.update(run.upset_labels)

    return SimulationSummary(
        simulations=total,
        champion_probs=_counter_to_probs(champion_counts, total),
        finalist_probs=_counter_to_probs(finalist_counts, total),
        group_winner_probs={
            group: _counter_to_probs(counts, total) for group, counts in sorted(group_winner_counts.items())
        },
        knockout_probs=_counter_to_probs(knockout_counts, total),
        upset_probs=_counter_to_probs(upset_counts, total),
    )


def _counter_to_probs(counter: Counter[str], total: int) -> dict[str, float]:
    if total <= 0:
        return {}
    return {key: value / total for key, value in counter.most_common()}


def _weighted_choice(
    random: Random,
    probabilities: dict[tuple[int, int], float],
) -> tuple[int, int]:
    draw = random.random()
    running = 0.0
    last_score = (0, 0)
    for score, probability in probabilities.items():
        running += probability
        last_score = score
        if draw <= running:
            return score
    return last_score
