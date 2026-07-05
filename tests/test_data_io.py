from pathlib import Path

from world_cup_oracle.data.io import (
    read_match_updates,
    read_team_adjustments,
    write_manual_templates,
    write_match_updates,
)
from world_cup_oracle.cli import main
from world_cup_oracle.domain import MatchResult, MatchStage, MethodOfWin


def test_read_match_updates_parses_locked_results(tmp_path: Path) -> None:
    path = tmp_path / "match_updates.csv"
    path.write_text(
        "match_id,home_goals,away_goals,home_penalties,away_penalties,home_yellow_cards,away_yellow_cards,home_red_cards,away_red_cards,home_corners,away_corners,played_at,notes\n"
        "G001,2,2,4,5,1,3,0,1,6,4,2026-06-11,shootout test\n",
        encoding="utf-8",
    )

    updates = read_match_updates(path)

    assert updates["G001"].winner_side == "away"
    assert updates["G001"].method == MethodOfWin.PENALTIES
    assert updates["G001"].home_corners == 6
    # Legacy files without stage/team columns still parse; provenance is None.
    assert updates["G001"].stage is None
    assert updates["G001"].team_pair is None


def test_match_updates_round_trip_stage_and_teams(tmp_path: Path) -> None:
    path = tmp_path / "match_updates.csv"
    knockout = MatchResult(
        match_id="400021999",
        home_goals=1,
        away_goals=1,
        home_penalties=4,
        away_penalties=5,
        method=MethodOfWin.PENALTIES,
        stage=MatchStage.ROUND_OF_32,
        home_team="AUS",
        away_team="USA",
    )
    write_match_updates(path, {knockout.match_id: knockout})

    reread = read_match_updates(path)["400021999"]

    assert reread.stage == MatchStage.ROUND_OF_32
    assert reread.home_team == "AUS"
    assert reread.away_team == "USA"
    assert reread.team_pair == frozenset({"AUS", "USA"})
    assert reread.winner_team == "USA"


def test_write_manual_templates_creates_headers(tmp_path: Path) -> None:
    paths = write_manual_templates(tmp_path)

    assert {path.name for path in paths} == {"match_updates.csv", "team_adjustments.csv", "player_callups.csv"}
    assert "match_id" in (tmp_path / "match_updates.csv").read_text(encoding="utf-8")
    assert "player_name" in (tmp_path / "player_callups.csv").read_text(encoding="utf-8")


def test_read_team_adjustments_returns_numeric_deltas(tmp_path: Path) -> None:
    path = tmp_path / "team_adjustments.csv"
    path.write_text(
        "team_code,rating_delta,attack_delta,defense_delta,discipline_delta,tempo_delta,notes\n"
        "bra,12,0.1,-0.05,0.0,0.02,form\n",
        encoding="utf-8",
    )

    adjustments = read_team_adjustments(path)

    assert adjustments["BRA"]["rating_delta"] == 12
    assert adjustments["BRA"]["attack_delta"] == 0.1


def test_cli_validate_snapshot(capsys, tmp_path: Path) -> None:
    teams_path = tmp_path / "teams.csv"
    fixtures_path = tmp_path / "fixtures.csv"
    teams_path.write_text(
        "team_code,team_name,group,confederation,fifa_rank,seed_rating\n"
        "MEX,Mexico,A,CONCACAF,14,1760\n"
        "RSA,South Africa,A,CAF,58,1580\n"
        "KOR,South Korea,A,AFC,23,1715\n"
        "CZE,Czech Republic,A,UEFA,32,1685\n",
        encoding="utf-8",
    )
    fixtures_path.write_text(
        "match_id,stage,home_team,away_team,group,kickoff,venue,neutral_site\n"
        "G001,group,MEX,RSA,A,,,true\n",
        encoding="utf-8",
    )

    assert main(["validate-snapshot", "--teams", str(teams_path), "--fixtures", str(fixtures_path)]) == 0
    assert "WARNING" in capsys.readouterr().out
