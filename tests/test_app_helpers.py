from world_cup_oracle.app import _fixture_rows, _probability_rows
from world_cup_oracle.domain import Fixture, MatchStage


def test_probability_rows_handles_empty_probabilities() -> None:
    rows = _probability_rows({}, {})

    assert list(rows.columns) == ["Label", "Code", "Probability"]
    assert rows.empty


def test_fixture_rows_show_team_names_and_local_kickoff() -> None:
    rows = _fixture_rows(
        [Fixture("400021443", MatchStage.GROUP, "MEX", "RSA", group="A", kickoff="2026-06-11T19:00:00Z", venue="Mexico City Stadium")],
        {"MEX": "Mexico", "RSA": "South Africa"},
    )

    assert rows.iloc[0]["Home"] == "Mexico"
    assert rows.iloc[0]["Away"] == "South Africa"
    assert rows.iloc[0]["Kickoff"] == "12 Jun 2026, 05:00"
