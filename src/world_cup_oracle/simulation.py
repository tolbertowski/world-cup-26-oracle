"""Monte Carlo tournament simulation."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from random import Random

from world_cup_oracle.domain import (
    BracketMatch,
    BracketProjection,
    Fixture,
    MatchPrediction,
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


KnockoutResultPool = dict[tuple[MatchStage, frozenset[str]], MatchResult]

# Order in which knockout rounds are resolved. The third-place playoff draws on
# semi-final losers, so it precedes the final in compute order.
KNOCKOUT_STAGE_SEQUENCE = (
    MatchStage.ROUND_OF_32,
    MatchStage.ROUND_OF_16,
    MatchStage.QUARTER_FINAL,
    MatchStage.SEMI_FINAL,
    MatchStage.THIRD_PLACE,
    MatchStage.FINAL,
)


def official_knockout_fixtures(fixtures: list[Fixture]) -> dict[MatchStage, list[Fixture]]:
    """Knockout fixtures grouped by stage, in calendar order.

    When the official calendar supplies the knockout bracket (real pairings for
    played rounds, ``W:<match_id>``/``RU:<match_id>``/seed sources for future
    ones), the tournament tree comes from here instead of being reconstructed
    from group standings — which can diverge from reality on tie-breaks and
    third-place allocation.
    """
    by_stage: dict[MatchStage, list[Fixture]] = defaultdict(list)
    for fixture in fixtures:
        if fixture.stage != MatchStage.GROUP:
            by_stage[fixture.stage].append(fixture)
    return dict(by_stage)


def _resolve_bracket_side(
    team: str,
    source: str | None,
    *,
    seeds: dict[str, str],
    thirds: dict[str, str],
    used_thirds: set[str],
    outcomes: dict[str, tuple[str, str]],
    match_id: str,
) -> str:
    """Team code for one side of a knockout fixture.

    Real team codes (already-played or already-drawn matches) win outright;
    otherwise the bracket source is followed: winner/loser of an earlier match,
    a direct seed label, or a best-third label resolved greedily.
    """
    if team:
        return team
    if not source:
        raise ValueError(f"Knockout fixture {match_id} has no team and no bracket source.")
    if source.startswith("W:"):
        return outcomes[source[len("W:"):]][0]
    if source.startswith("RU:"):
        return outcomes[source[len("RU:"):]][1]
    if source.startswith("3"):
        for group in source[1:]:
            if group in thirds and group not in used_thirds:
                used_thirds.add(group)
                return thirds[group]
        for group in sorted(thirds):
            if group not in used_thirds:
                used_thirds.add(group)
                return thirds[group]
        raise KeyError(f"No available third-place qualifier for {match_id} ({source}).")
    return seeds[source]


def build_knockout_result_pool(locked_results: dict[str, MatchResult]) -> KnockoutResultPool:
    """Index locked knockout results by (stage, team pair).

    The simulator generates its own knockout match ids (R32-01, QF-02, ...), so
    real knockout results — locked under official match ids — are matched to
    bracket slots by stage and the unordered pair of team codes instead. Results
    without stage/team provenance simply are not indexed, which also guards
    against applying a real result to a simulated pairing that never happened.
    """
    pool: KnockoutResultPool = {}
    for result in locked_results.values():
        pair = result.team_pair
        if not result.locked or pair is None or result.stage in (None, MatchStage.GROUP):
            continue
        pool[(result.stage, pair)] = result
    return pool


def lookup_knockout_result(pool: KnockoutResultPool, fixture: Fixture) -> MatchResult | None:
    """Locked result for this fixture's stage and team pair, oriented to the fixture."""
    result = pool.get((fixture.stage, frozenset((fixture.home_team, fixture.away_team))))
    if result is None:
        return None
    return _orient_result_to_fixture(fixture, result)


def _orient_result_to_fixture(fixture: Fixture, result: MatchResult) -> MatchResult:
    """Re-key a locked result to the fixture, flipping sides if needed."""
    if result.home_team == fixture.home_team:
        return replace(result, match_id=fixture.match_id)
    return replace(
        result,
        match_id=fixture.match_id,
        home_goals=result.away_goals,
        away_goals=result.home_goals,
        home_penalties=result.away_penalties,
        away_penalties=result.home_penalties,
        home_yellow_cards=result.away_yellow_cards,
        away_yellow_cards=result.home_yellow_cards,
        home_red_cards=result.away_red_cards,
        away_red_cards=result.home_red_cards,
        home_corners=result.away_corners,
        away_corners=result.home_corners,
        home_team=result.away_team,
        away_team=result.home_team,
    )


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
        knockout_pool = build_knockout_result_pool(locked_results)
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
        upset_labels: list[str] = []

        official = official_knockout_fixtures(self.fixtures)
        if official:
            champion, finalists, r32_participants = self._simulate_official_knockouts(
                official,
                group_stage,
                results,
                knockout_pool,
                upset_labels,
            )
            return SimulationRun(
                champion=champion,
                finalists=finalists,
                group_winners=group_winners,
                knockout_teams=r32_participants or knockout_teams,
                upset_labels=upset_labels,
                results=results,
            )

        round_of_32 = build_round_of_32(group_stage.qualified)
        r32_winners, r32_losers = self._simulate_knockout_round(round_of_32, results, upset_labels, knockout_pool)
        r16_winners, r16_losers = self._simulate_knockout_round(
            build_next_round_fixtures(r32_winners, MatchStage.ROUND_OF_16, "R16"),
            results,
            upset_labels,
            knockout_pool,
        )
        qf_winners, qf_losers = self._simulate_knockout_round(
            build_next_round_fixtures(r16_winners, MatchStage.QUARTER_FINAL, "QF"),
            results,
            upset_labels,
            knockout_pool,
        )
        sf_winners, sf_losers = self._simulate_knockout_round(
            build_next_round_fixtures(qf_winners, MatchStage.SEMI_FINAL, "SF"),
            results,
            upset_labels,
            knockout_pool,
        )
        final_fixture = build_next_round_fixtures(sf_winners, MatchStage.FINAL, "F")[0]
        finalists = (final_fixture.home_team, final_fixture.away_team)
        final_winners, final_losers = self._simulate_knockout_round(
            [final_fixture],
            results,
            upset_labels,
            knockout_pool,
        )

        third_place_fixture = Fixture(
            "TP-01",
            MatchStage.THIRD_PLACE,
            home_team=sf_losers[0],
            away_team=sf_losers[1],
        )
        self._simulate_knockout_round([third_place_fixture], results, upset_labels, knockout_pool)

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

    def _simulate_official_knockouts(
        self,
        official: dict[MatchStage, list[Fixture]],
        group_stage,
        results: dict[str, MatchResult],
        knockout_pool: KnockoutResultPool,
        upset_labels: list[str],
    ) -> tuple[str, tuple[str, str], set[str]]:
        """Walk the official bracket tree, using real results where they exist.

        Returns (champion, final pairing, round-of-32 participants).
        """
        seeds = {qualified.seed_label: qualified.team_code for qualified in group_stage.qualified}
        thirds = {qualified.group: qualified.team_code for qualified in group_stage.qualified if qualified.rank == 3}
        used_thirds: set[str] = set()
        outcomes: dict[str, tuple[str, str]] = {}
        r32_participants: set[str] = set()
        champion = ""
        finalists = ("", "")

        for stage in KNOCKOUT_STAGE_SEQUENCE:
            for fixture in official.get(stage, []):
                home = _resolve_bracket_side(
                    fixture.home_team,
                    fixture.home_source,
                    seeds=seeds,
                    thirds=thirds,
                    used_thirds=used_thirds,
                    outcomes=outcomes,
                    match_id=fixture.match_id,
                )
                away = _resolve_bracket_side(
                    fixture.away_team,
                    fixture.away_source,
                    seeds=seeds,
                    thirds=thirds,
                    used_thirds=used_thirds,
                    outcomes=outcomes,
                    match_id=fixture.match_id,
                )
                result = results.get(fixture.match_id)
                if result is not None and result.team_pair is not None and result.team_pair != frozenset((home, away)):
                    # A real result outranks any resolution approximation.
                    home, away = result.home_team, result.away_team
                resolved = replace(fixture, home_team=home, away_team=away)
                if stage == MatchStage.ROUND_OF_32:
                    r32_participants.update((home, away))
                if result is not None and result.home_team not in (None, home):
                    result = _orient_result_to_fixture(resolved, result)
                    results[resolved.match_id] = result
                if result is None:
                    result = lookup_knockout_result(knockout_pool, resolved)
                    if result is None:
                        result = self._simulate_knockout_match(resolved)
                    results[resolved.match_id] = result
                winner_side = result.winner_side
                if winner_side is None:
                    raise ValueError(f"Knockout result {resolved.match_id} has no winner.")
                winner = home if winner_side == "home" else away
                loser = away if winner_side == "home" else home
                outcomes[resolved.match_id] = (winner, loser)
                if self._is_upset(winner, loser):
                    upset_labels.append(f"{winner} over {loser}")
                if stage == MatchStage.FINAL:
                    champion = winner
                    finalists = (home, away)
        return champion, finalists, r32_participants

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
        knockout_pool: KnockoutResultPool,
    ) -> tuple[list[str], list[str]]:
        winners: list[str] = []
        losers: list[str] = []
        for fixture in fixtures:
            if fixture.match_id in results:
                result = results[fixture.match_id]
            else:
                result = lookup_knockout_result(knockout_pool, fixture)
                if result is None:
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
            home_advances = self.random.random() < knockout_penalty_share(prediction)
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


def knockout_penalty_share(prediction: MatchPrediction) -> float:
    """Probability the home side wins a knockout tie that reaches penalties."""
    return prediction.home_win / max(0.01, prediction.home_win + prediction.away_win)


def knockout_advance_share(prediction: MatchPrediction) -> float:
    """Overall probability the home side advances from a knockout tie.

    Mirrors the Monte Carlo path: the home side advances by winning in
    regulation (``home_win``) or by surviving a drawn tie on penalties
    (``draw`` weighted by :func:`knockout_penalty_share`).
    """
    return prediction.home_win + prediction.draw * knockout_penalty_share(prediction)


def project_bracket(
    teams: list[Team],
    fixtures: list[Fixture],
    predictor: MatchPredictor,
    locked_results: dict[str, MatchResult] | None = None,
) -> BracketProjection:
    """Deterministic most-likely knockout bracket (no RNG).

    Group results use the modal scoreline from each prediction (or a locked
    real result when present); knockout winners take the side with the higher
    :func:`knockout_advance_share`. Reuses the same engine functions as the
    Monte Carlo path so the structure stays consistent.
    """
    locked_results = locked_results or {}
    knockout_pool = build_knockout_result_pool(locked_results)
    group_fixtures = [fixture for fixture in fixtures if fixture.stage == MatchStage.GROUP]

    results: dict[str, MatchResult] = {}
    for fixture in group_fixtures:
        if fixture.match_id in locked_results:
            results[fixture.match_id] = locked_results[fixture.match_id]
            continue
        prediction = predictor.predict(fixture)
        home_goals, away_goals = _modal_scoreline(prediction)
        results[fixture.match_id] = MatchResult(
            match_id=fixture.match_id,
            home_goals=home_goals,
            away_goals=away_goals,
            method=MethodOfWin.REGULATION if home_goals != away_goals else MethodOfWin.DRAW,
            locked=True,
        )

    group_stage = calculate_group_stage(teams, group_fixtures, results)

    official = official_knockout_fixtures(fixtures)
    if official:
        return _project_official_bracket(official, group_stage, predictor, locked_results, knockout_pool)

    rounds: list[tuple[MatchStage, list[BracketMatch]]] = []

    def project_round(round_fixtures: list[Fixture]) -> tuple[list[BracketMatch], list[str], list[str]]:
        matches: list[BracketMatch] = []
        winners: list[str] = []
        losers: list[str] = []
        for fixture in round_fixtures:
            locked = locked_results.get(fixture.match_id)
            if locked is None:
                locked = lookup_knockout_result(knockout_pool, fixture)
            if locked is not None and locked.winner_side is not None:
                home_advances = locked.winner_side == "home"
                advance_prob = 1.0
                source = "locked"
            else:
                share = knockout_advance_share(predictor.predict(fixture))
                home_advances = share >= 0.5
                advance_prob = share if home_advances else 1.0 - share
                source = "expected"
            winner = fixture.home_team if home_advances else fixture.away_team
            loser = fixture.away_team if home_advances else fixture.home_team
            matches.append(
                BracketMatch(
                    stage=fixture.stage,
                    match_id=fixture.match_id,
                    home_team=fixture.home_team,
                    away_team=fixture.away_team,
                    projected_winner=winner,
                    advance_prob=advance_prob,
                    source=source,
                )
            )
            winners.append(winner)
            losers.append(loser)
        return matches, winners, losers

    round_of_32 = build_round_of_32(group_stage.qualified)
    r32_matches, r32_winners, _ = project_round(round_of_32)
    rounds.append((MatchStage.ROUND_OF_32, r32_matches))

    r16_matches, r16_winners, _ = project_round(
        build_next_round_fixtures(r32_winners, MatchStage.ROUND_OF_16, "R16")
    )
    rounds.append((MatchStage.ROUND_OF_16, r16_matches))

    qf_matches, qf_winners, _ = project_round(
        build_next_round_fixtures(r16_winners, MatchStage.QUARTER_FINAL, "QF")
    )
    rounds.append((MatchStage.QUARTER_FINAL, qf_matches))

    sf_matches, sf_winners, sf_losers = project_round(
        build_next_round_fixtures(qf_winners, MatchStage.SEMI_FINAL, "SF")
    )
    rounds.append((MatchStage.SEMI_FINAL, sf_matches))

    final_matches, final_winners, _ = project_round(
        build_next_round_fixtures(sf_winners, MatchStage.FINAL, "F")
    )
    rounds.append((MatchStage.FINAL, final_matches))

    third_place = None
    if len(sf_losers) == 2:
        tp_matches, tp_winners, _ = project_round(
            [Fixture("TP-01", MatchStage.THIRD_PLACE, home_team=sf_losers[0], away_team=sf_losers[1])]
        )
        rounds.append((MatchStage.THIRD_PLACE, tp_matches))
        third_place = tp_winners[0]

    return BracketProjection(rounds=rounds, champion=final_winners[0], third_place=third_place)


def _project_official_bracket(
    official: dict[MatchStage, list[Fixture]],
    group_stage,
    predictor: MatchPredictor,
    locked_results: dict[str, MatchResult],
    knockout_pool: KnockoutResultPool,
) -> BracketProjection:
    """Deterministic projection over the official bracket tree."""
    seeds = {qualified.seed_label: qualified.team_code for qualified in group_stage.qualified}
    thirds = {qualified.group: qualified.team_code for qualified in group_stage.qualified if qualified.rank == 3}
    used_thirds: set[str] = set()
    outcomes: dict[str, tuple[str, str]] = {}
    by_stage: dict[MatchStage, list[BracketMatch]] = {}
    champion = ""
    third_place: str | None = None

    for stage in KNOCKOUT_STAGE_SEQUENCE:
        matches: list[BracketMatch] = []
        for fixture in official.get(stage, []):
            home = _resolve_bracket_side(
                fixture.home_team,
                fixture.home_source,
                seeds=seeds,
                thirds=thirds,
                used_thirds=used_thirds,
                outcomes=outcomes,
                match_id=fixture.match_id,
            )
            away = _resolve_bracket_side(
                fixture.away_team,
                fixture.away_source,
                seeds=seeds,
                thirds=thirds,
                used_thirds=used_thirds,
                outcomes=outcomes,
                match_id=fixture.match_id,
            )
            locked = locked_results.get(fixture.match_id)
            if locked is not None and locked.team_pair is not None and locked.team_pair != frozenset((home, away)):
                # A real result outranks any resolution approximation.
                home, away = locked.home_team, locked.away_team
            resolved = replace(fixture, home_team=home, away_team=away)
            if locked is None:
                locked = lookup_knockout_result(knockout_pool, resolved)
            if locked is not None and locked.winner_side is not None:
                winner_team = locked.winner_team
                home_advances = winner_team == home if winner_team is not None else locked.winner_side == "home"
                advance_prob = 1.0
                source = "locked"
            else:
                share = knockout_advance_share(predictor.predict(resolved))
                home_advances = share >= 0.5
                advance_prob = share if home_advances else 1.0 - share
                source = "expected"
            winner = home if home_advances else away
            loser = away if home_advances else home
            outcomes[resolved.match_id] = (winner, loser)
            matches.append(
                BracketMatch(
                    stage=stage,
                    match_id=resolved.match_id,
                    home_team=home,
                    away_team=away,
                    projected_winner=winner,
                    advance_prob=advance_prob,
                    source=source,
                )
            )
            if stage == MatchStage.FINAL:
                champion = winner
            if stage == MatchStage.THIRD_PLACE:
                third_place = winner
        if matches:
            by_stage[stage] = matches

    display_order = [
        MatchStage.ROUND_OF_32,
        MatchStage.ROUND_OF_16,
        MatchStage.QUARTER_FINAL,
        MatchStage.SEMI_FINAL,
        MatchStage.FINAL,
        MatchStage.THIRD_PLACE,
    ]
    rounds = [(stage, by_stage[stage]) for stage in display_order if stage in by_stage]
    return BracketProjection(rounds=rounds, champion=champion, third_place=third_place)


def _modal_scoreline(prediction: MatchPrediction) -> tuple[int, int]:
    """Most-likely scoreline, with a deterministic tie-break."""
    if not prediction.scoreline_probs:
        return (0, 0)
    return max(prediction.scoreline_probs.items(), key=lambda item: (item[1], item[0]))[0]


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
