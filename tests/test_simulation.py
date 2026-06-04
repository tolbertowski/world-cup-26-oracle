from world_cup_oracle.data import build_demo_fixtures, build_demo_teams
from world_cup_oracle.models import MatchPredictor
from world_cup_oracle.simulation import run_monte_carlo


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
