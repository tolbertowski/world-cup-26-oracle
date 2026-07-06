from world_cup_oracle.data import build_demo_fixtures, build_demo_teams
from world_cup_oracle.domain import Fixture, MatchResult, MatchStage, TeamRating
from world_cup_oracle.metrics import calibration_bins, evaluate_predictions
from world_cup_oracle.models import (
    EloRatingModel,
    MatchPredictor,
    apply_results_to_ratings,
    margin_of_victory_multiplier,
)


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


def _two_team_ratings(home: float, away: float) -> dict[str, TeamRating]:
    return {
        "AUS": TeamRating(team_code="AUS", rating=home, attack=1.2, defense=1.2),
        "USA": TeamRating(team_code="USA", rating=away, attack=1.0, defense=1.0),
    }


def _fixture(neutral: bool = True) -> Fixture:
    return Fixture("M1", MatchStage.GROUP, "AUS", "USA", group="D", neutral_site=neutral)


def test_apply_results_updates_winner_and_loser_only_on_rating() -> None:
    ratings = _two_team_ratings(home=1804.0, away=1756.0)
    fixtures = [_fixture()]
    # USA (away) beats Australia (home).
    results = {"M1": MatchResult("M1", home_goals=0, away_goals=2, locked=True)}

    updated = apply_results_to_ratings(ratings, fixtures, results)

    assert updated["USA"].rating > 1756.0  # winner gains
    assert updated["AUS"].rating < 1804.0  # loser drops
    # Only the overall rating moves; style factors are preserved.
    assert updated["USA"].attack == 1.0
    assert updated["AUS"].defense == 1.2


def test_margin_of_victory_scales_movement() -> None:
    fixtures = [_fixture()]
    narrow = apply_results_to_ratings(
        _two_team_ratings(1800.0, 1800.0), fixtures, {"M1": MatchResult("M1", 1, 0, locked=True)}
    )
    blowout = apply_results_to_ratings(
        _two_team_ratings(1800.0, 1800.0), fixtures, {"M1": MatchResult("M1", 4, 0, locked=True)}
    )
    assert blowout["AUS"].rating - 1800.0 > narrow["AUS"].rating - 1800.0
    assert margin_of_victory_multiplier(4, 0) > margin_of_victory_multiplier(1, 0) == 1.0


def test_margin_of_victory_is_capped() -> None:
    fixtures = [_fixture()]
    heavy = apply_results_to_ratings(
        _two_team_ratings(1800.0, 1800.0), fixtures, {"M1": MatchResult("M1", 4, 0, locked=True)}
    )
    rout = apply_results_to_ratings(
        _two_team_ratings(1800.0, 1800.0), fixtures, {"M1": MatchResult("M1", 7, 0, locked=True)}
    )
    # A 7-0 is no stronger rating evidence than a 4-0.
    assert rout["AUS"].rating == heavy["AUS"].rating
    assert margin_of_victory_multiplier(7, 0) == margin_of_victory_multiplier(4, 0) == 2.0


def test_shootout_win_counts_as_draw_for_ratings() -> None:
    fixtures = [_fixture()]
    # Underdog AUS (lower-rated here) survives to penalties and wins the shootout.
    shootout = {"M1": MatchResult("M1", 1, 1, home_penalties=5, away_penalties=4, locked=True)}
    updated = apply_results_to_ratings(_two_team_ratings(1700.0, 1800.0), fixtures, shootout)
    # Level after play is draw-strength evidence: the lower-rated side gains
    # (a draw beats expectation) but far less than a regulation win would give.
    regulation = apply_results_to_ratings(
        _two_team_ratings(1700.0, 1800.0), fixtures, {"M1": MatchResult("M1", 2, 1, locked=True)}
    )
    assert 1700.0 < updated["AUS"].rating < regulation["AUS"].rating


def test_host_advantage_shifts_probabilities_home() -> None:
    teams = build_demo_teams()
    predictor = MatchPredictor.from_teams(teams)
    neutral = predictor.predict(Fixture("T1", MatchStage.GROUP, "USA", "KOR", group="D", neutral_site=True))
    hosted = predictor.predict(Fixture("T1", MatchStage.GROUP, "USA", "KOR", group="D", neutral_site=False))

    assert hosted.home_win > neutral.home_win
    assert hosted.expected_home_goals > neutral.expected_home_goals
    assert any("Host advantage" in line for line in hosted.explanation)
    assert not any("Host advantage" in line for line in neutral.explanation)

    # The edge also carries into knockout eventual-win probability.
    neutral_ko = predictor.predict(Fixture("K1", MatchStage.ROUND_OF_16, "USA", "KOR", neutral_site=True))
    hosted_ko = predictor.predict(Fixture("K1", MatchStage.ROUND_OF_16, "USA", "KOR", neutral_site=False))
    assert hosted_ko.home_win > neutral_ko.home_win


def test_dixon_coles_boosts_draws_and_normalizes() -> None:
    from world_cup_oracle.models import scoreline_distribution

    plain = scoreline_distribution(1.3, 1.1, rho=0.0)
    corrected = scoreline_distribution(1.3, 1.1)  # default negative rho

    assert round(sum(plain.values()), 9) == 1.0
    assert round(sum(corrected.values()), 9) == 1.0
    draw_plain = sum(prob for (home, away), prob in plain.items() if home == away)
    draw_corrected = sum(prob for (home, away), prob in corrected.items() if home == away)
    assert draw_corrected > draw_plain
    assert corrected[(0, 0)] > plain[(0, 0)]
    assert corrected[(1, 0)] < plain[(1, 0)]
    # High-score cells relatively unaffected pre-normalization; rho=0 is identity.
    assert plain == scoreline_distribution(1.3, 1.1, rho=0.0)


def test_expected_goals_total_varies_with_matchup() -> None:
    teams = build_demo_teams()
    predictor = MatchPredictor.from_teams(teams)
    mismatch = predictor.predict(Fixture("T1", MatchStage.GROUP, "BRA", "HAI", group="C"))
    # Two mid-rated sides from the demo data for an even pairing.
    even = predictor.predict(Fixture("T2", MatchStage.GROUP, "KOR", "CZE", group="A"))
    # Totals are anchored, not pinned: a mismatch should out-total an even tie.
    assert mismatch.total_goals > even.total_goals
    assert abs(even.total_goals - predictor.average_total_goals) < 0.75


def test_knockout_result_with_embedded_teams_moves_ratings() -> None:
    from world_cup_oracle.domain import MatchStage

    ratings = _two_team_ratings(1804.0, 1756.0)
    # No fixture exists for this official id — the result carries its own teams.
    knockout = {
        "400099001": MatchResult(
            "400099001",
            home_goals=0,
            away_goals=2,
            locked=True,
            stage=MatchStage.ROUND_OF_16,
            home_team="AUS",
            away_team="USA",
        )
    }
    updated = apply_results_to_ratings(ratings, [], knockout)
    assert updated["USA"].rating > 1756.0
    assert updated["AUS"].rating < 1804.0


def test_apply_results_ignores_unlocked_and_unknown_fixtures() -> None:
    ratings = _two_team_ratings(1800.0, 1800.0)
    fixtures = [_fixture()]
    unlocked = apply_results_to_ratings(ratings, fixtures, {"M1": MatchResult("M1", 3, 0, locked=False)})
    unknown = apply_results_to_ratings(ratings, fixtures, {"ZZZ": MatchResult("ZZZ", 3, 0, locked=True)})
    assert unlocked["AUS"].rating == 1800.0
    assert unknown["AUS"].rating == 1800.0


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
