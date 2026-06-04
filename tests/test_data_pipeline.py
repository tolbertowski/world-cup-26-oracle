from pathlib import Path

from world_cup_oracle.data.pipeline import (
    export_processed_data,
    import_tournament_snapshot,
    load_processed_or_demo,
    read_fixtures_csv,
    read_teams_csv,
    validate_tournament_data,
    write_source_templates,
)
from world_cup_oracle.domain import MatchStage


def test_source_templates_are_created(tmp_path: Path) -> None:
    paths = write_source_templates(tmp_path)

    assert {path.name for path in paths} == {"teams_template.csv", "fixtures_template.csv"}
    assert "team_code" in (tmp_path / "teams_template.csv").read_text(encoding="utf-8")
    assert "match_id" in (tmp_path / "fixtures_template.csv").read_text(encoding="utf-8")


def test_read_source_csvs_normalizes_typed_objects(tmp_path: Path) -> None:
    teams_path, fixtures_path = _write_valid_partial_snapshot(tmp_path)

    teams = read_teams_csv(teams_path)
    fixtures = read_fixtures_csv(fixtures_path)

    assert teams[0].code == "MEX"
    assert teams[0].seed_rating == 1760
    assert fixtures[0].stage == MatchStage.GROUP
    assert fixtures[0].neutral_site is True


def test_validation_reports_unknown_fixture_team(tmp_path: Path) -> None:
    teams_path, fixtures_path = _write_valid_partial_snapshot(tmp_path)
    fixtures_path.write_text(
        "match_id,stage,home_team,away_team,group,kickoff,venue,neutral_site\n"
        "G001,group,MEX,XXX,A,,,true\n",
        encoding="utf-8",
    )

    report = validate_tournament_data(read_teams_csv(teams_path), read_fixtures_csv(fixtures_path))

    assert not report.ok
    assert "unknown away team XXX" in report.render()


def test_import_snapshot_exports_processed_data(tmp_path: Path) -> None:
    teams_path, fixtures_path = _write_valid_partial_snapshot(tmp_path)
    processed_dir = tmp_path / "processed"

    report = import_tournament_snapshot(teams_path, fixtures_path, processed_dir)
    loaded = load_processed_or_demo(processed_dir)

    assert report.ok
    assert loaded.source == "processed"
    assert len(loaded.teams) == 4
    assert len(loaded.fixtures) == 6


def test_load_processed_or_demo_falls_back_without_processed_files(tmp_path: Path) -> None:
    loaded = load_processed_or_demo(tmp_path)

    assert loaded.source == "demo"
    assert len(loaded.teams) == 48
    assert len(loaded.fixtures) == 72


def test_strict_validation_requires_full_2026_snapshot(tmp_path: Path) -> None:
    teams_path, fixtures_path = _write_valid_partial_snapshot(tmp_path)

    report = validate_tournament_data(
        read_teams_csv(teams_path),
        read_fixtures_csv(fixtures_path),
        strict=True,
    )

    assert not report.ok
    assert "48 teams" in report.render()
    assert "72 group fixtures" in report.render()


def test_export_processed_data_round_trips(tmp_path: Path) -> None:
    teams_path, fixtures_path = _write_valid_partial_snapshot(tmp_path)
    processed_paths = export_processed_data(
        read_teams_csv(teams_path),
        read_fixtures_csv(fixtures_path),
        tmp_path / "processed",
    )

    assert {path.name for path in processed_paths} == {"teams.csv", "fixtures.csv"}
    assert len(read_teams_csv(tmp_path / "processed" / "teams.csv")) == 4


def _write_valid_partial_snapshot(tmp_path: Path) -> tuple[Path, Path]:
    teams_path = tmp_path / "teams.csv"
    fixtures_path = tmp_path / "fixtures.csv"
    teams_path.write_text(
        "team_code,team_name,group,confederation,fifa_rank,seed_rating\n"
        "mex,Mexico,A,CONCACAF,14,1760\n"
        "rsa,South Africa,A,CAF,58,1580\n"
        "kor,South Korea,A,AFC,23,1715\n"
        "cze,Czech Republic,A,UEFA,32,1685\n",
        encoding="utf-8",
    )
    fixtures_path.write_text(
        "match_id,stage,home_team,away_team,group,kickoff,venue,neutral_site\n"
        "G001,group,MEX,RSA,A,2026-06-11T20:00:00Z,Estadio Azteca,true\n"
        "G002,group,KOR,CZE,A,,,true\n"
        "G003,group,MEX,KOR,A,,,true\n"
        "G004,group,RSA,CZE,A,,,true\n"
        "G005,group,MEX,CZE,A,,,true\n"
        "G006,group,RSA,KOR,A,,,true\n",
        encoding="utf-8",
    )
    return teams_path, fixtures_path
