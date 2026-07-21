from dataclasses import replace

from world_cup_oracle.backtest import backtest_pairs, run_backtest
from world_cup_oracle.domain import Fixture, MatchResult, MatchStage, MethodOfWin
from world_cup_oracle.metrics import evaluate_predictions, score_forecasts
from world_cup_oracle.models import MatchPredictor
from world_cup_oracle.snapshots import results_as_of


def test_rps_rewards_order_and_is_zero_for_perfect() -> None:
    perfect = score_forecasts([([1.0, 0.0, 0.0], [1.0, 0.0, 0.0])])
    assert perfect.rps == 0.0

    # Predicting a draw when away wins is "closer" (adjacent) than predicting a
    # home win when away wins (opposite ends), so RPS must be lower.
    near = score_forecasts([([0.0, 1.0, 0.0], [0.0, 0.0, 1.0])]).rps
    far = score_forecasts([([1.0, 0.0, 0.0], [0.0, 0.0, 1.0])]).rps
    assert near < far

    uniform = score_forecasts([([1 / 3, 1 / 3, 1 / 3], [1.0, 0.0, 0.0])]).rps
    assert 0 < uniform < far


def test_knockout_shootout_scored_as_advancement_not_draw() -> None:
    teams = _teams()
    predictor = MatchPredictor.from_teams(teams)
    ko = Fixture("K1", MatchStage.ROUND_OF_16, "BRA", "MEX")
    # 1-1, MEX (away) win on penalties -> scored as an away result, not a draw.
    result = MatchResult("K1", 1, 1, home_penalties=3, away_penalties=4, method=MethodOfWin.PENALTIES)

    evaluation = evaluate_predictions([(predictor.predict(ko), result)])
    # The model's knockout forecast has draw folded in; a shootout that the away
    # side wins must be treated as an away outcome (accuracy depends on the pick,
    # but the actual class is unambiguously "away").
    from world_cup_oracle.metrics import _actual_vector

    assert _actual_vector(predictor.predict(ko), result) == [0.0, 0.0, 1.0]
    assert evaluation.matches == 1


def test_results_as_of_strict_excludes_self_and_simultaneous() -> None:
    from datetime import datetime, timezone

    fixtures = [
        Fixture("A", MatchStage.GROUP, "BRA", "MEX", kickoff="2026-06-12T19:00:00Z"),
        Fixture("B", MatchStage.GROUP, "USA", "KOR", kickoff="2026-06-12T19:00:00Z"),
        Fixture("C", MatchStage.GROUP, "ESP", "CZE", kickoff="2026-06-11T19:00:00Z"),
    ]
    results = {code: MatchResult(code, 1, 0) for code in ("A", "B", "C")}
    cutoff = datetime(2026, 6, 12, 19, 0, tzinfo=timezone.utc)

    strict = results_as_of(fixtures, results, cutoff, inclusive=False)
    assert set(strict) == {"C"}  # A (self) and B (simultaneous) excluded


def _mini_tournament():
    teams = _teams()
    fixtures = [
        Fixture("M1", MatchStage.GROUP, "BRA", "MEX", group="A", kickoff="2026-06-11T19:00:00Z"),
        Fixture("M2", MatchStage.GROUP, "BRA", "USA", group="A", kickoff="2026-06-14T19:00:00Z"),
    ]
    locked = {
        "M1": MatchResult("M1", 3, 0, stage=MatchStage.GROUP, home_team="BRA", away_team="MEX"),
        "M2": MatchResult("M2", 1, 1, stage=MatchStage.GROUP, home_team="BRA", away_team="USA"),
    }
    return teams, fixtures, {}, locked, {}


def test_backtest_is_leak_free() -> None:
    teams, fixtures, adjustments, locked, params = _mini_tournament()

    pairs = backtest_pairs(teams, fixtures, adjustments, locked, params)
    assert [p[1].match_id for p in pairs] == ["M1", "M2"]
    m2_home_win = next(p[0].home_win for p in pairs if p[0].fixture.match_id == "M2")

    # Flipping M2's own result must not change M2's prediction — a match is
    # never allowed to inform its own forecast.
    flipped = dict(locked)
    flipped["M2"] = replace(locked["M2"], home_goals=0, away_goals=5)
    pairs2 = backtest_pairs(teams, fixtures, adjustments, flipped, params)
    m2_home_win_flipped = next(p[0].home_win for p in pairs2 if p[0].fixture.match_id == "M2")
    assert m2_home_win == m2_home_win_flipped


def _teams():
    from world_cup_oracle.data import build_demo_teams

    return build_demo_teams()
