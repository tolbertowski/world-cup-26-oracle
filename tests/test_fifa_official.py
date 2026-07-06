import json
from pathlib import Path

from world_cup_oracle.cli import main
from world_cup_oracle.data.fifa_official import parse_fifa_calendar, sync_fifa_calendar
from world_cup_oracle.data.pipeline import release_check
from world_cup_oracle.domain import MethodOfWin


def test_parse_fifa_calendar_builds_group_teams_fixtures_and_results() -> None:
    payload = _fixture_payload()

    teams, fixtures, results = parse_fifa_calendar(payload)

    assert [team.code for team in teams] == ["CZE", "KOR", "MEX", "RSA"]
    assert fixtures[0].match_id == "400021443"
    assert fixtures[0].home_team == "MEX"
    assert fixtures[0].away_team == "RSA"
    assert fixtures[0].venue == "Mexico City Stadium (Mexico City)"
    assert results["400021443"].home_goals == 2
    assert results["400021443"].method == MethodOfWin.REGULATION


def test_sync_fifa_calendar_apply_writes_processed_and_updates(tmp_path: Path) -> None:
    source = tmp_path / "fifa.json"
    source.write_text(json.dumps(_fixture_payload(full_group=True)), encoding="utf-8")

    result = sync_fifa_calendar(
        raw_dir=tmp_path / "raw",
        cache_dir=tmp_path / "cache",
        processed_dir=tmp_path / "processed",
        manual_dir=tmp_path / "manual",
        source_json=source,
        apply=True,
        strict=False,
    )

    assert result.ok
    assert (tmp_path / "processed" / "teams.csv").exists()
    assert (tmp_path / "processed" / "fixtures.csv").exists()
    assert "400021443" in (tmp_path / "manual" / "match_updates.csv").read_text(encoding="utf-8")


def test_sync_preserves_fitted_seed_ratings(tmp_path: Path) -> None:
    source = tmp_path / "fifa.json"
    source.write_text(json.dumps(_fixture_payload(full_group=True)), encoding="utf-8")
    kwargs = dict(
        raw_dir=tmp_path / "raw",
        cache_dir=tmp_path / "cache",
        processed_dir=tmp_path / "processed",
        manual_dir=tmp_path / "manual",
        source_json=source,
        apply=True,
        strict=False,
    )
    sync_fifa_calendar(**kwargs)

    # Simulate a fit: give one team a real rating, then re-sync.
    teams_path = tmp_path / "processed" / "teams.csv"
    fitted = teams_path.read_text(encoding="utf-8").replace(",1500.0", ",1897.5", 1)
    teams_path.write_text(fitted, encoding="utf-8")

    sync_fifa_calendar(**kwargs)

    assert ",1897.5" in teams_path.read_text(encoding="utf-8")


def test_release_check_blocks_demo_data(tmp_path: Path) -> None:
    report = release_check(tmp_path / "processed")

    assert not report.ok
    assert "app would use demo data" in report.render()


def test_cli_sync_fifa_from_cached_json(capsys, tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "fifa.json"
    source.write_text(json.dumps(_fixture_payload(full_group=True)), encoding="utf-8")
    # Point the CLI at a scratch project root so the test never writes into the
    # repository's real data/ directories (it previously clobbered the cache).
    import world_cup_oracle.cli as cli

    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)

    assert main(["sync-fifa", "--source-json", str(source), "--no-strict"]) == 0
    out = capsys.readouterr().out

    assert "teams=" in out
    assert "cache=" in out


def test_parse_flags_host_country_fixtures_as_non_neutral() -> None:
    payload = _fixture_payload()
    # First match is MEX at a Mexican stadium; mark the stadium country.
    payload["Results"][0]["Stadium"]["IdCountry"] = "MEX"
    # Second match is KOR vs CZE at the same kind of venue — neutral for both.
    payload["Results"][1]["Stadium"]["IdCountry"] = "MEX"

    _, fixtures, _ = parse_fifa_calendar(payload)
    by_id = {fixture.match_id: fixture for fixture in fixtures}

    assert by_id["400021443"].neutral_site is False  # MEX at home in Mexico
    assert by_id["400021441"].neutral_site is True  # KOR vs CZE in Mexico


def test_parse_translates_knockout_bracket_sources() -> None:
    payload = _fixture_payload(full_group=True)
    r32 = _match("400021500", 73, "MEX", "Mexico", "RSA", "South Africa", "", "KO Stadium", "City")
    r32["StageName"] = [{"Locale": "en-GB", "Description": "Round of 32"}]
    r32["PlaceHolderA"] = "1A"
    r32["PlaceHolderB"] = "3ABCD"
    r16 = _match("400021501", 89, "", "", "", "", "", "KO Stadium", "City")
    r16["StageName"] = [{"Locale": "en-GB", "Description": "Round of 16"}]
    r16["Home"] = {}
    r16["Away"] = {}
    r16["PlaceHolderA"] = "W73"
    r16["PlaceHolderB"] = "W74"
    payload["Results"].extend([r32, r16])

    _, fixtures, _ = parse_fifa_calendar(payload)

    knockout = {fixture.match_id: fixture for fixture in fixtures if fixture.is_knockout}
    assert knockout["400021500"].home_team == "MEX"  # real teams win over placeholders
    assert knockout["400021500"].home_source == "1A"
    assert knockout["400021500"].away_source == "3ABCD"
    assert knockout["400021501"].home_team == ""
    assert knockout["400021501"].home_source == "W:400021500"  # W73 -> referenced match id
    assert knockout["400021501"].away_source == "W74"  # unknown number kept verbatim


def _fixture_payload(*, full_group: bool = False) -> dict:
    matches = [
        _match("400021443", 1, "MEX", "Mexico", "RSA", "South Africa", "Group A", "Mexico City Stadium", "Mexico City", 2, 0, 0),
        _match("400021441", 2, "KOR", "Korea Republic", "CZE", "Czechia", "Group A", "Guadalajara Stadium", "Guadalajara"),
    ]
    if full_group:
        matches.extend(
            [
                _match("400021442", 3, "MEX", "Mexico", "KOR", "Korea Republic", "Group A", "Example Stadium", "City"),
                _match("400021444", 4, "RSA", "South Africa", "CZE", "Czechia", "Group A", "Example Stadium", "City"),
                _match("400021445", 5, "MEX", "Mexico", "CZE", "Czechia", "Group A", "Example Stadium", "City"),
                _match("400021446", 6, "RSA", "South Africa", "KOR", "Korea Republic", "Group A", "Example Stadium", "City"),
            ]
        )
    return {"Results": matches}


def _match(
    match_id: str,
    match_number: int,
    home_code: str,
    home_name: str,
    away_code: str,
    away_name: str,
    group: str,
    stadium: str,
    city: str,
    home_score: int | None = None,
    away_score: int | None = None,
    status: int = 1,
) -> dict:
    return {
        "IdMatch": match_id,
        "MatchNumber": match_number,
        "Date": "2026-06-11T19:00:00Z",
        "MatchStatus": status,
        "ResultType": 1 if home_score is not None else 0,
        "StageName": [{"Locale": "en-GB", "Description": "First Stage"}],
        "GroupName": [{"Locale": "en-GB", "Description": group}],
        "Home": {
            "Abbreviation": home_code,
            "IdCountry": home_code,
            "TeamName": [{"Locale": "en-GB", "Description": home_name}],
        },
        "Away": {
            "Abbreviation": away_code,
            "IdCountry": away_code,
            "TeamName": [{"Locale": "en-GB", "Description": away_name}],
        },
        "HomeTeamScore": home_score,
        "AwayTeamScore": away_score,
        "HomeTeamPenaltyScore": None,
        "AwayTeamPenaltyScore": None,
        "Stadium": {
            "Name": [{"Locale": "en-GB", "Description": stadium}],
            "CityName": [{"Locale": "en-GB", "Description": city}],
        },
    }
