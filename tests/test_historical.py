from __future__ import annotations

from datetime import date
from pathlib import Path

from world_cup_oracle.data.historical import (
    competition_weight,
    fit_attack_defense,
    fit_average_goals,
    fit_elo,
    fit_team_ratings,
    read_results,
    source_name_for,
    time_weight,
)
from world_cup_oracle.data.io import (
    GENERATED_PLAYER_ADJUSTMENT_PREFIX,
    GENERATED_RATINGS_PREFIX,
    read_team_adjustments,
    upsert_generated_team_adjustments,
)
from world_cup_oracle.domain import Team


RESULTS_HEADER = "date,home_team,away_team,home_score,away_score,tournament,city,country,neutral\n"


def _write_results(path: Path, rows: list[str]) -> Path:
    path.write_text(RESULTS_HEADER + "".join(row + "\n" for row in rows), encoding="utf-8")
    return path


def test_read_results_skips_unplayed_and_bad_rows(tmp_path: Path) -> None:
    path = _write_results(
        tmp_path / "results.csv",
        [
            "2022-11-20,Qatar,Ecuador,0,2,FIFA World Cup,Al Khor,Qatar,FALSE",
            "2026-06-27,Jordan,Argentina,NA,NA,FIFA World Cup,Arlington,United States,TRUE",
            "not-a-date,Foo,Bar,1,1,Friendly,X,Y,FALSE",
        ],
    )
    records = read_results(path)
    assert len(records) == 1
    assert records[0].home_team == "Qatar"
    assert records[0].neutral is False


def test_competition_weight_orders_importance() -> None:
    assert competition_weight("FIFA World Cup") == 1.0
    assert competition_weight("FIFA World Cup qualification") == 0.8
    assert competition_weight("UEFA Euro") == 0.9
    assert competition_weight("Friendly") == 0.3
    assert competition_weight("Some Local Cup") == 0.5


def test_time_weight_favours_recent() -> None:
    as_of = date(2026, 6, 1)
    recent = time_weight(date(2026, 1, 1), as_of, half_life_days=1095)
    old = time_weight(date(2010, 1, 1), as_of, half_life_days=1095)
    assert recent > old
    assert 0.0 < old < recent <= 1.0


def test_fit_elo_rewards_winners(tmp_path: Path) -> None:
    rows = [f"2025-0{month}-01,Alpha,Bravo,3,0,FIFA World Cup,City,Country,TRUE" for month in range(1, 7)]
    records = read_results(_write_results(tmp_path / "results.csv", rows))
    elo = fit_elo(records)
    assert elo["Alpha"] > 1500 > elo["Bravo"]


def test_fit_attack_defense_separates_styles(tmp_path: Path) -> None:
    rows = [
        "2025-01-01,Striker,Wall,4,0,FIFA World Cup,C,X,TRUE",
        "2025-02-01,Striker,Average,3,1,FIFA World Cup,C,X,TRUE",
        "2025-03-01,Wall,Average,0,0,FIFA World Cup,C,X,TRUE",
        "2025-04-01,Average,Striker,1,3,FIFA World Cup,C,X,TRUE",
        "2025-05-01,Average,Wall,0,0,FIFA World Cup,C,X,TRUE",
    ]
    records = read_results(_write_results(tmp_path / "results.csv", rows))
    strengths = fit_attack_defense(records)
    assert strengths["Striker"][0] > strengths["Average"][0]  # attack
    assert strengths["Wall"][1] > strengths["Striker"][1]  # defense (higher = better)


def test_fit_average_goals_weights_recent_matches(tmp_path: Path) -> None:
    rows = [
        # Old low-scoring era vs recent higher-scoring matches.
        "2000-01-01,Alpha,Bravo,0,0,FIFA World Cup,C,X,TRUE",
        "2025-01-01,Alpha,Bravo,2,1,FIFA World Cup,C,X,TRUE",
        "2025-02-01,Bravo,Alpha,1,2,FIFA World Cup,C,X,TRUE",
    ]
    records = read_results(_write_results(tmp_path / "results.csv", rows))
    average = fit_average_goals(records)
    # Recency weighting pulls the mean toward the recent 3-goal matches.
    assert 2.5 < average <= 3.0
    assert fit_average_goals([]) == 2.62  # safe fallback


def test_source_name_for_uses_aliases() -> None:
    assert source_name_for(Team(code="KOR", name="Korea Republic")) == "South Korea"
    assert source_name_for(Team(code="USA", name="USA")) == "United States"
    # No alias -> falls back to the team_name verbatim.
    assert source_name_for(Team(code="BRA", name="Brazil")) == "Brazil"


def test_fit_team_ratings_resolves_aliases_and_reports_unmatched(tmp_path: Path) -> None:
    rows = [
        "2025-01-01,Brazil,South Korea,2,0,FIFA World Cup,C,X,TRUE",
        "2025-02-01,South Korea,Brazil,0,1,FIFA World Cup,C,X,TRUE",
    ]
    records = read_results(_write_results(tmp_path / "results.csv", rows))
    teams = [
        Team(code="BRA", name="Brazil"),
        Team(code="KOR", name="Korea Republic"),
        Team(code="ZZZ", name="Neverland"),
    ]
    fitted, unmatched = fit_team_ratings(records, teams)
    codes = {item.team_code for item in fitted}
    assert codes == {"BRA", "KOR"}
    assert unmatched == ["ZZZ"]
    bra = next(item for item in fitted if item.team_code == "BRA")
    assert bra.source_name == "Brazil"
    assert bra.elo > 1500


def test_cli_fit_ratings_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    """Re-running fit-ratings must replace its generated block, not append to it."""
    import world_cup_oracle.cli as cli
    from world_cup_oracle.cli import main

    monkeypatch.setattr(cli, "MODEL_PARAMS_PATH", tmp_path / "model_params.json")
    results_path = _write_results(
        tmp_path / "results.csv",
        [
            "2025-01-01,Brazil,South Korea,2,0,FIFA World Cup,C,X,TRUE",
            "2025-02-01,South Korea,Brazil,0,1,FIFA World Cup,C,X,TRUE",
        ],
    )
    teams_path = tmp_path / "teams.csv"
    teams_path.write_text(
        "team_code,team_name,group,confederation,fifa_rank,seed_rating\n"
        "BRA,Brazil,A,,,1500.0\n"
        "KOR,Korea Republic,A,,,1500.0\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "team_adjustments.csv"
    args = [
        "fit-ratings",
        "--results", str(results_path),
        "--teams", str(teams_path),
        "--output", str(output_path),
    ]

    assert main(args) == 0
    assert main(args) == 0

    text = output_path.read_text(encoding="utf-8")
    assert text.count("international_results:") == 2  # one row per team, no stacking
    adjustments = read_team_adjustments(output_path)
    assert len(adjustments) == 2


def test_generated_rows_are_idempotent_and_coexist(tmp_path: Path) -> None:
    path = tmp_path / "team_adjustments.csv"
    # A manual row and a player_callups row must survive the ratings upsert.
    path.write_text(
        "team_code,rating_delta,attack_delta,defense_delta,discipline_delta,tempo_delta,notes\n"
        "ARG,5.0,0.0,0.0,0.0,0.0,manual tweak\n"
        "BRA,2.0,0.0,0.0,0.0,0.0,player_callups: squad=80\n",
        encoding="utf-8",
    )
    ratings_row = {
        "team_code": "BRA",
        "rating_delta": "0.0",
        "attack_delta": "0.150",
        "defense_delta": "0.100",
        "discipline_delta": "0.0",
        "tempo_delta": "0.0",
        "notes": f"{GENERATED_RATINGS_PREFIX} elo=2000",
    }
    upsert_generated_team_adjustments(path, [ratings_row], note_prefix=GENERATED_RATINGS_PREFIX)
    upsert_generated_team_adjustments(path, [ratings_row], note_prefix=GENERATED_RATINGS_PREFIX)

    text = path.read_text(encoding="utf-8")
    assert text.count(GENERATED_RATINGS_PREFIX) == 1  # no duplication on re-run
    assert "manual tweak" in text
    assert GENERATED_PLAYER_ADJUSTMENT_PREFIX in text  # player_callups row preserved

    adjustments = read_team_adjustments(path)
    # BRA now carries both the manual rating_delta and the generated attack_delta.
    assert adjustments["BRA"]["rating_delta"] == 2.0
    assert round(adjustments["BRA"]["attack_delta"], 3) == 0.150
