from world_cup_oracle.data import build_demo_fixtures, build_demo_teams
from world_cup_oracle.domain import Fixture, MatchResult, MatchStage
from world_cup_oracle.metrics import calibration_bins, evaluate_predictions
from world_cup_oracle.models import EloRatingModel, MatchPredictor


def test_predictor_outputs_normalized_probabilities_and_event_projections() -> None:
    teams = build_demo_teams()
    predictor = MatchPredictor.from_teams(teams)
    prediction = predictor.predict(Fixture("T1", MatchStage.GROUP, "BRA", "HAI", group="C"))

    assert round(prediction.home_win + prediction.draw + prediction.away_win, 6) == 1
    assert round(sum(prediction.scoreline_probs.values()), 6) == 1
    assert prediction.home_win > prediction.away_win
    assert prediction.expected_home_goals > prediction.expected_away_goals
    assert prediction.expected_home_corners > 0
    assert prediction.expected_away_cards > 0


def test_elo_update_rewards_winner() -> None:
    teams = build_demo_teams()
    model = EloRatingModel.from_teams(teams)
    before = model.ratings["BRA"].rating

    model.update_from_result(Fixture("T1", MatchStage.GROUP, "BRA", "HAI", group="C"), MatchResult("T1", 2, 0))

    assert model.ratings["BRA"].rating > before


def test_evaluation_metrics_and_calibration_bins() -> None:
    teams = build_demo_teams()
    predictor = MatchPredictor.from_teams(teams)
    fixture = build_demo_fixtures()[0]
    prediction = predictor.predict(fixture)
    result = MatchResult(fixture.match_id, 1, 0)

    evaluation = evaluate_predictions([(prediction, result)])
    bins = calibration_bins([(prediction, result)], bins=4)

    assert evaluation.matches == 1
    assert evaluation.log_loss > 0
    assert sum(bucket["count"] for bucket in bins) == 1
