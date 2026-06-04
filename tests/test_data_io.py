from pathlib import Path

from world_cup_oracle.data.io import read_match_updates, read_team_adjustments, write_manual_templates
from world_cup_oracle.domain import MethodOfWin


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


def test_write_manual_templates_creates_headers(tmp_path: Path) -> None:
    paths = write_manual_templates(tmp_path)

    assert {path.name for path in paths} == {"match_updates.csv", "team_adjustments.csv"}
    assert "match_id" in (tmp_path / "match_updates.csv").read_text(encoding="utf-8")


def test_read_team_adjustments_returns_numeric_deltas(tmp_path: Path) -> None:
    path = tmp_path / "team_adjustments.csv"
    path.write_text(
        "team_code,rating_delta,attack_delta,defense_delta,discipline_delta,tempo_delta,notes\n"
        "BRA,12,0.1,-0.05,0.0,0.02,form\n",
        encoding="utf-8",
    )

    adjustments = read_team_adjustments(path)

    assert adjustments["BRA"]["rating_delta"] == 12
    assert adjustments["BRA"]["attack_delta"] == 0.1
