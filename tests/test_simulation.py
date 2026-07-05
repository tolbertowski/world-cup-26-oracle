from world_cup_oracle.data import build_demo_fixtures, build_demo_teams
from world_cup_oracle.domain import MatchResult, MatchStage, MethodOfWin
from world_cup_oracle.models import MatchPredictor
from world_cup_oracle.simulation import (
    TournamentSimulator,
    build_knockout_result_pool,
    lookup_knockout_result,
    project_bracket,
    run_monte_carlo,
)


def test_monte_carlo_is_reproducible_with_seed() -> None:
    teams = build_demo_teams()
    fixtures = build_demo_fixtures()
    predictor = MatchPredictor.from_teams(teams)

    first = run_monte_carlo(teams, fixtures, predictor, simulations=25, seed=26)
    second = run_monte_carlo(teams, fixtures, predictor, simulations=25, seed=26)

    assert first.champion_probs == second.champion_probs
    assert first.simulations == 25


def test_monte_carlo_returns_core_probability_surfaces() -> None:
    teams = build_demo_teams()
    summary = run_monte_carlo(
        teams,
        build_demo_fixtures(),
        MatchPredictor.from_teams(teams),
        simulations=20,
        seed=99,
    )

    assert summary.champion_probs
    assert summary.finalist_probs
    assert summary.group_winner_probs["A"]
    assert summary.knockout_probs


def _lock_all_group_results() -> dict[str, MatchResult]:
    """Deterministic full group stage: the home side always wins 1-0."""
    return {
        fixture.match_id: MatchResult(
            match_id=fixture.match_id,
            home_goals=1,
            away_goals=0,
            method=MethodOfWin.REGULATION,
            stage=MatchStage.GROUP,
            home_team=fixture.home_team,
            away_team=fixture.away_team,
        )
        for fixture in build_demo_fixtures()
        if fixture.stage == MatchStage.GROUP
    }


def _first_r32_pairing(teams, fixtures, predictor, locked) -> tuple[str, str, str]:
    """(match_id, home, away) of the first Round-of-32 tie in the projection."""
    bracket = project_bracket(teams, fixtures, predictor, locked)
    stage, matches = bracket.rounds[0]
    assert stage == MatchStage.ROUND_OF_32
    first = matches[0]
    return first.match_id, first.home_team, first.away_team


def test_locked_knockout_result_overrides_simulation_by_team_pair() -> None:
    teams = build_demo_teams()
    fixtures = build_demo_fixtures()
    predictor = MatchPredictor.from_teams(teams)
    locked = _lock_all_group_results()
    match_id, home, away = _first_r32_pairing(teams, fixtures, predictor, locked)

    # Lock the real result under an official-style id with FLIPPED orientation:
    # the bracket's away side is listed as home and wins 3-0.
    locked["400099001"] = MatchResult(
        match_id="400099001",
        home_goals=3,
        away_goals=0,
        method=MethodOfWin.REGULATION,
        stage=MatchStage.ROUND_OF_32,
        home_team=away,
        away_team=home,
    )

    simulator = TournamentSimulator(teams, fixtures, predictor, seed=7)
    for _ in range(5):
        run = simulator.simulate_once(locked)
        result = run.results[match_id]
        # Never re-simulated: always the locked scoreline, oriented to the fixture.
        assert (result.home_goals, result.away_goals) == (0, 3)
        assert result.home_team == home and result.away_team == away

    bracket = project_bracket(teams, fixtures, predictor, locked)
    first = bracket.rounds[0][1][0]
    assert first.projected_winner == away
    assert first.source == "locked"
    assert first.advance_prob == 1.0


def _official_tree_fixtures() -> list:
    """Group fixtures plus a synthetic official bracket: two semis, TP, final."""
    from world_cup_oracle.domain import Fixture

    return [
        *build_demo_fixtures(),
        # Real teams already known (like a played/drawn match in the calendar).
        Fixture("KO1", MatchStage.SEMI_FINAL, home_team="BRA", away_team="MEX"),
        # Teams TBD, resolved from group seeds.
        Fixture("KO2", MatchStage.SEMI_FINAL, home_team="", away_team="", home_source="1A", away_source="1B"),
        Fixture("KO3", MatchStage.THIRD_PLACE, home_team="", away_team="", home_source="RU:KO1", away_source="RU:KO2"),
        Fixture("KO4", MatchStage.FINAL, home_team="", away_team="", home_source="W:KO1", away_source="W:KO2"),
    ]


def test_official_bracket_tree_resolves_sources_and_locked_results() -> None:
    teams = build_demo_teams()
    fixtures = _official_tree_fixtures()
    predictor = MatchPredictor.from_teams(teams)
    locked = _lock_all_group_results()
    # Lock the first semi by official id: Brazil wins 2-0.
    locked["KO1"] = MatchResult("KO1", 2, 0, method=MethodOfWin.REGULATION,
                                stage=MatchStage.SEMI_FINAL, home_team="BRA", away_team="MEX")

    simulator = TournamentSimulator(teams, fixtures, predictor, seed=3)
    run = simulator.simulate_once(locked)

    # The final's home side is the winner of KO1 via the W: reference.
    assert run.finalists[0] == "BRA"
    assert run.results["KO1"].home_goals == 2  # never re-simulated
    assert "KO4" in run.results and "KO3" in run.results
    # Third place pairs the two semi losers via RU: references.
    third = run.results["KO3"]
    assert third.match_id == "KO3"
    assert run.champion in {"BRA", run.finalists[1]}

    bracket = project_bracket(teams, fixtures, predictor, locked)
    semis = dict((match.match_id, match) for _, matches in bracket.rounds for match in matches)
    assert semis["KO1"].source == "locked"
    assert semis["KO1"].projected_winner == "BRA"
    assert bracket.champion in {"BRA", semis["KO4"].away_team}
    assert bracket.third_place is not None


def test_knockout_pool_ignores_results_without_provenance() -> None:
    plain = MatchResult(match_id="X1", home_goals=2, away_goals=0)
    group = MatchResult(
        match_id="X2", home_goals=2, away_goals=0, stage=MatchStage.GROUP, home_team="BRA", away_team="MEX"
    )
    knockout = MatchResult(
        match_id="X3", home_goals=2, away_goals=0, stage=MatchStage.FINAL, home_team="BRA", away_team="MEX"
    )
    pool = build_knockout_result_pool({"X1": plain, "X2": group, "X3": knockout})
    assert list(pool) == [(MatchStage.FINAL, frozenset({"BRA", "MEX"}))]


def test_lookup_orients_result_to_fixture() -> None:
    from world_cup_oracle.domain import Fixture

    result = MatchResult(
        match_id="OFFICIAL",
        home_goals=1,
        away_goals=1,
        home_penalties=5,
        away_penalties=4,
        method=MethodOfWin.PENALTIES,
        stage=MatchStage.SEMI_FINAL,
        home_team="ARG",
        away_team="FRA",
    )
    pool = build_knockout_result_pool({"OFFICIAL": result})
    flipped_fixture = Fixture("SF-01", MatchStage.SEMI_FINAL, home_team="FRA", away_team="ARG")

    oriented = lookup_knockout_result(pool, flipped_fixture)

    assert oriented is not None
    assert oriented.match_id == "SF-01"
    assert (oriented.home_penalties, oriented.away_penalties) == (4, 5)
    assert oriented.winner_side == "away"  # ARG still wins after the flip
    assert oriented.winner_team == "ARG"
