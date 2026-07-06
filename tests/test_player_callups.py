from pathlib import Path

import pytest

from world_cup_oracle.cli import main
from world_cup_oracle.data.io import read_team_adjustments, upsert_generated_team_adjustments
from world_cup_oracle.data.player_callups import (
    PlayerCallup,
    build_player_callup_adjustments,
    read_player_callups,
)


def test_read_player_callups_normalizes_source_rows(tmp_path: Path) -> None:
    path = tmp_path / "player_callups.csv"
    path.write_text(
        "team_code,player_name,position,expected_role,player_rating,minutes_share,availability,club_strength,market_value_eur,notes\n"
        "bra,Example Forward,ST,Starter,,65,90,88,12.5m,likely starter\n",
        encoding="utf-8",
    )

    callups = read_player_callups(path)

    assert callups[0].team_code == "BRA"
    assert callups[0].position_group == "FWD"
    assert callups[0].minutes_share == pytest.approx(0.65)
    assert callups[0].availability == pytest.approx(0.9)
    assert callups[0].market_value_eur == 12_500_000


def test_player_callup_adjustments_are_relative_and_bounded() -> None:
    callups = [
        *_squad("AAA", rating=88, position_cycle=["GK", "CB", "CM", "ST"]),
        *_squad("BBB", rating=60, position_cycle=["GK", "CB", "CM", "ST"]),
    ]

    adjustments = build_player_callup_adjustments(callups, baseline_score=70.0)

    by_team = {adjustment.team_code: adjustment for adjustment in adjustments}
    assert by_team["AAA"].rating_delta == 80.0
    assert by_team["AAA"].attack_delta > 0
    assert by_team["AAA"].defense_delta > 0
    assert by_team["BBB"].rating_delta < 0
    assert by_team["AAA"].as_adjustment_row()["notes"].startswith("player_callups:")


def test_player_callup_adjustments_reject_unknown_team_codes() -> None:
    callups = [PlayerCallup(team_code="XXX", player_name="Unknown", position="CM", player_rating=70)]

    with pytest.raises(ValueError, match="Unknown team codes"):
        build_player_callup_adjustments(callups, team_codes={"MEX"})


def test_upsert_generated_team_adjustments_replaces_previous_player_rows(tmp_path: Path) -> None:
    path = tmp_path / "team_adjustments.csv"
    path.write_text(
        "team_code,rating_delta,attack_delta,defense_delta,discipline_delta,tempo_delta,notes\n"
        "ARG,3,0,0,0,0,manual form\n"
        "ARG,8,0.010,0,0,0,player_callups: old\n",
        encoding="utf-8",
    )

    upsert_generated_team_adjustments(
        path,
        [
            {
                "team_code": "ARG",
                "rating_delta": "10",
                "attack_delta": "0.020",
                "defense_delta": "0.010",
                "discipline_delta": "0",
                "tempo_delta": "0.004",
                "notes": "player_callups: new",
            }
        ],
        note_prefix="player_callups:",
    )

    adjustments = read_team_adjustments(path)
    text = path.read_text(encoding="utf-8")
    assert adjustments["ARG"]["rating_delta"] == 13
    assert adjustments["ARG"]["attack_delta"] == pytest.approx(0.02)
    assert "player_callups: old" not in text
    assert "manual form" in text


def test_cli_apply_player_callups_writes_generated_adjustments(capsys, tmp_path: Path) -> None:
    teams_path = tmp_path / "teams.csv"
    callups_path = tmp_path / "player_callups.csv"
    output_path = tmp_path / "team_adjustments.csv"
    teams_path.write_text(
        "team_code,team_name,group,confederation,fifa_rank,seed_rating\n"
        "MEX,Mexico,A,CONCACAF,14,1760\n"
        "RSA,South Africa,A,CAF,58,1580\n",
        encoding="utf-8",
    )
    callups_path.write_text(
        "team_code,player_name,position,expected_role,player_rating,minutes_share,availability,club_strength,market_value_eur,notes\n"
        + "\n".join(_callup_rows("MEX", 82))
        + "\n"
        + "\n".join(_callup_rows("RSA", 65))
        + "\n",
        encoding="utf-8",
    )

    result = main(
        [
            "apply-player-callups",
            "--callups",
            str(callups_path),
            "--teams",
            str(teams_path),
            "--output",
            str(output_path),
        ]
    )

    assert result == 0
    assert "MEX" in capsys.readouterr().out
    assert "player_callups:" in output_path.read_text(encoding="utf-8")


def test_cli_apply_player_callups_empty_dry_run_is_successful(tmp_path: Path) -> None:
    callups_path = tmp_path / "player_callups.csv"
    callups_path.write_text(
        "team_code,player_name,position,expected_role,player_rating,minutes_share,availability,club_strength,market_value_eur,notes\n",
        encoding="utf-8",
    )

    assert main(["apply-player-callups", "--callups", str(callups_path), "--dry-run"]) == 0


def _squad(team_code: str, *, rating: float, position_cycle: list[str]) -> list[PlayerCallup]:
    return [
        PlayerCallup(
            team_code=team_code,
            player_name=f"{team_code} Player {index}",
            position=position_cycle[index % len(position_cycle)],
            expected_role="starter" if index < 11 else "rotation",
            player_rating=rating,
        )
        for index in range(18)
    ]


def _callup_rows(team_code: str, rating: float) -> list[str]:
    positions = ["GK", "CB", "CM", "ST"]
    return [
        f"{team_code},{team_code} Player {index},{positions[index % len(positions)]},starter,{rating},,,,,"
        for index in range(18)
    ]
