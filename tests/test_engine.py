from world_cup_oracle.data import build_demo_fixtures, build_demo_teams
from world_cup_oracle.domain import Fixture, MatchResult, MatchStage, MethodOfWin, Team
from world_cup_oracle.engine import (
    build_round_of_32,
    calculate_group_stage,
    winner_from_result,
)


def test_group_standings_rank_points_goal_difference_and_goals() -> None:
    teams = [
        Team("A1", "Alpha", group="A", seed_rating=1600),
        Team("A2", "Beta", group="A", seed_rating=1500),
        Team("A3", "Gamma", group="A", seed_rating=1550),
        Team("A4", "Delta", group="A", seed_rating=1450),
    ]
    fixtures = [
        Fixture("M1", MatchStage.GROUP, "A1", "A2", group="A"),
        Fixture("M2", MatchStage.GROUP, "A3", "A4", group="A"),
        Fixture("M3", MatchStage.GROUP, "A1", "A3", group="A"),
        Fixture("M4", MatchStage.GROUP, "A2", "A4", group="A"),
        Fixture("M5", MatchStage.GROUP, "A1", "A4", group="A"),
        Fixture("M6", MatchStage.GROUP, "A2", "A3", group="A"),
    ]
    results = {
        "M1": MatchResult("M1", 1, 0),
        "M2": MatchResult("M2", 0, 0),
        "M3": MatchResult("M3", 0, 2),
        "M4": MatchResult("M4", 2, 1),
        "M5": MatchResult("M5", 2, 0),
        "M6": MatchResult("M6", 1, 1),
    }

    stage = calculate_group_stage(teams, fixtures, results, third_place_count=0)

    assert [row.team_code for row in stage.standings["A"]] == ["A1", "A3", "A2", "A4"]
    assert stage.standings["A"][0].points == 6


def test_group_standings_use_head_to_head_before_seed_rating() -> None:
    teams = [
        Team("A1", "Alpha", group="A", seed_rating=1400),
        Team("A2", "Beta", group="A", seed_rating=1700),
        Team("A3", "Gamma", group="A", seed_rating=1500),
        Team("A4", "Delta", group="A", seed_rating=1450),
    ]
    fixtures = [
        Fixture("M1", MatchStage.GROUP, "A1", "A2", group="A"),
        Fixture("M2", MatchStage.GROUP, "A1", "A3", group="A"),
        Fixture("M3", MatchStage.GROUP, "A1", "A4", group="A"),
        Fixture("M4", MatchStage.GROUP, "A2", "A3", group="A"),
        Fixture("M5", MatchStage.GROUP, "A2", "A4", group="A"),
        Fixture("M6", MatchStage.GROUP, "A3", "A4", group="A"),
    ]
    results = {
        "M1": MatchResult("M1", 1, 0),
        "M2": MatchResult("M2", 0, 1),
        "M3": MatchResult("M3", 1, 1),
        "M4": MatchResult("M4", 1, 1),
        "M5": MatchResult("M5", 1, 0),
        "M6": MatchResult("M6", 0, 0),
    }

    stage = calculate_group_stage(teams, fixtures, results, third_place_count=0)

    order = [row.team_code for row in stage.standings["A"]]
    assert order.index("A1") < order.index("A2")


def test_demo_group_stage_selects_eight_thirds_and_builds_unique_round_of_32() -> None:
    stage = calculate_group_stage(
        build_demo_teams(),
        build_demo_fixtures(),
        third_place_count=8,
    )

    round_of_32 = build_round_of_32(stage.qualified)
    teams = [team for fixture in round_of_32 for team in (fixture.home_team, fixture.away_team)]

    assert len(stage.automatic_qualifiers) == 24
    assert len(stage.third_place_qualifiers) == 8
    assert len(round_of_32) == 16
    assert len(teams) == len(set(teams))


def test_winner_from_result_uses_penalties_for_knockout_draws() -> None:
    fixture = Fixture("K1", MatchStage.ROUND_OF_32, "A1", "A2")
    result = MatchResult(
        "K1",
        1,
        1,
        home_penalties=4,
        away_penalties=5,
        method=MethodOfWin.PENALTIES,
    )

    assert winner_from_result(fixture, result) == "A2"
