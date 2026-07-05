"""Official FIFA public calendar adapter.

FIFA's public site exposes tournament calendars through `api.fifa.com`. This
adapter keeps that source outside the live app: sync the official snapshot,
validate it, then let the app load processed files.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from world_cup_oracle.data.io import read_match_updates, write_match_updates
from world_cup_oracle.data.pipeline import (
    FIXTURE_SOURCE_COLUMNS,
    TEAM_SOURCE_COLUMNS,
    DataValidationReport,
    export_processed_data,
    read_teams_csv,
    validate_tournament_data,
)
from world_cup_oracle.domain import Fixture, MatchResult, MatchStage, MethodOfWin, Team


FIFA_COMPETITION_ID = "17"
FIFA_WORLD_CUP_2026_SEASON_ID = "285023"
FIFA_CALENDAR_URL = "https://api.fifa.com/api/v3/calendar/matches"
FIFA_COMPLETED_STATUS = 0


@dataclass(frozen=True, slots=True)
class FifaSyncResult:
    report: DataValidationReport
    teams: list[Team]
    fixtures: list[Fixture]
    completed_results: dict[str, MatchResult]
    cache_path: Path | None = None
    raw_paths: tuple[Path, Path] | None = None
    processed_paths: tuple[Path, Path] | None = None
    updates_path: Path | None = None

    @property
    def ok(self) -> bool:
        return self.report.ok


def fetch_fifa_calendar(
    *,
    season_id: str = FIFA_WORLD_CUP_2026_SEASON_ID,
    language: str = "en",
    count: int = 500,
) -> dict:
    query = urlencode({"language": language, "count": count, "idSeason": season_id})
    request = Request(
        f"{FIFA_CALENDAR_URL}?{query}",
        headers={"User-Agent": "world-cup-26-oracle/0.1 (+https://www.fifa.com)"},
    )
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def load_fifa_calendar(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_fifa_cache(payload: dict, cache_dir: Path, *, season_id: str) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"fifa_calendar_{season_id}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def parse_fifa_calendar(payload: dict) -> tuple[list[Team], list[Fixture], dict[str, MatchResult]]:
    matches = payload.get("Results", [])
    group_matches = [match for match in matches if _stage_from_match(match) == MatchStage.GROUP]
    knockout_matches = sorted(
        (match for match in matches if _stage_from_match(match) != MatchStage.GROUP),
        key=lambda match: match.get("MatchNumber") or 0,
    )
    teams = _teams_from_group_matches(group_matches)
    number_to_id = {
        match.get("MatchNumber"): str(match.get("IdMatch"))
        for match in matches
        if match.get("MatchNumber") and match.get("IdMatch")
    }
    fixtures = [_fixture_from_match(match) for match in group_matches]
    fixtures += [_knockout_fixture_from_match(match, number_to_id) for match in knockout_matches]
    completed_results = {
        result.match_id: result
        for result in (_result_from_match(match) for match in matches)
        if result is not None
    }
    return teams, fixtures, completed_results


def sync_fifa_calendar(
    *,
    raw_dir: Path,
    cache_dir: Path,
    processed_dir: Path,
    manual_dir: Path,
    source_json: Path | None = None,
    apply: bool = False,
    update_results: bool = True,
    strict: bool = True,
    season_id: str = FIFA_WORLD_CUP_2026_SEASON_ID,
    language: str = "en",
) -> FifaSyncResult:
    payload = load_fifa_calendar(source_json) if source_json else fetch_fifa_calendar(season_id=season_id, language=language)
    cache_path = write_fifa_cache(payload, cache_dir, season_id=season_id)
    teams, fixtures, completed_results = parse_fifa_calendar(payload)
    report = validate_tournament_data(teams, fixtures, strict=strict)
    raw_paths = write_official_raw_files(teams, fixtures, raw_dir) if apply else None
    processed_paths = None
    updates_path = None

    if apply and report.ok:
        teams = _preserve_fitted_seed_ratings(teams, processed_dir)
        processed = export_processed_data(teams, fixtures, processed_dir)
        processed_paths = (processed[0], processed[1])
        if update_results:
            updates_path = manual_dir / "match_updates.csv"
            existing = read_match_updates(updates_path)
            merged = {**existing, **completed_results}
            write_match_updates(updates_path, merged)

    return FifaSyncResult(
        report=report,
        teams=teams,
        fixtures=fixtures,
        completed_results=completed_results,
        cache_path=cache_path,
        raw_paths=raw_paths,
        processed_paths=processed_paths,
        updates_path=updates_path,
    )


def _preserve_fitted_seed_ratings(teams: list[Team], processed_dir: Path) -> list[Team]:
    """Carry fitted seed ratings over a calendar re-sync.

    The FIFA calendar knows nothing about ratings, so a matchday sync must not
    reset `seed_rating` (fit by `fit-ratings`) back to the 1500 default.
    """
    teams_path = processed_dir / "teams.csv"
    if not teams_path.exists():
        return teams
    existing = {team.code: team.seed_rating for team in read_teams_csv(teams_path)}
    return [
        replace(team, seed_rating=existing[team.code]) if team.code in existing else team
        for team in teams
    ]


def write_official_raw_files(teams: list[Team], fixtures: list[Fixture], raw_dir: Path) -> tuple[Path, Path]:
    raw_dir.mkdir(parents=True, exist_ok=True)
    teams_path = raw_dir / "teams.csv"
    fixtures_path = raw_dir / "fixtures.csv"
    with teams_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=TEAM_SOURCE_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for team in teams:
            writer.writerow(
                {
                    "team_code": team.code,
                    "team_name": team.name,
                    "group": team.group or "",
                    "confederation": team.confederation or "",
                    "fifa_rank": team.fifa_rank or "",
                    "seed_rating": team.seed_rating,
                }
            )
    with fixtures_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIXTURE_SOURCE_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for fixture in fixtures:
            writer.writerow(
                {
                    "match_id": fixture.match_id,
                    "stage": fixture.stage.value,
                    "home_team": fixture.home_team,
                    "away_team": fixture.away_team,
                    "group": fixture.group or "",
                    "kickoff": fixture.kickoff or "",
                    "venue": fixture.venue or "",
                    "neutral_site": str(fixture.neutral_site).lower(),
                }
            )
    return teams_path, fixtures_path


def _teams_from_group_matches(matches: list[dict]) -> list[Team]:
    teams: dict[str, Team] = {}
    group_by_code: dict[str, str] = {}
    for match in matches:
        group = _group_from_match(match)
        for side in ("Home", "Away"):
            team_payload = match.get(side) or {}
            code = team_payload.get("Abbreviation") or team_payload.get("IdCountry")
            if not code:
                continue
            group_by_code.setdefault(code, group or "")
            teams[code] = Team(
                code=code,
                name=_team_name(team_payload),
                group=group_by_code.get(code) or None,
                confederation=None,
                fifa_rank=None,
                seed_rating=1500.0,
            )
    return sorted(teams.values(), key=lambda team: ((team.group or ""), team.code))


def _fixture_from_match(match: dict) -> Fixture:
    home = match.get("Home") or {}
    away = match.get("Away") or {}
    stadium = match.get("Stadium") or {}
    venue_name = _localized(stadium.get("Name"))
    city = _localized(stadium.get("CityName"))
    venue = f"{venue_name} ({city})" if venue_name and city else venue_name or city
    match_number = match.get("MatchNumber")
    match_id = str(match.get("IdMatch") or f"M{match_number:03d}")
    return Fixture(
        match_id=match_id,
        stage=_stage_from_match(match),
        home_team=home.get("Abbreviation") or home.get("IdCountry") or match.get("PlaceHolderA") or "",
        away_team=away.get("Abbreviation") or away.get("IdCountry") or match.get("PlaceHolderB") or "",
        group=_group_from_match(match),
        kickoff=match.get("Date"),
        venue=venue,
        neutral_site=True,
    )


def _knockout_fixture_from_match(match: dict, number_to_id: dict) -> Fixture:
    """Knockout fixture with bracket provenance instead of placeholder team codes.

    Future matches have no teams yet; PlaceHolderA/B carry either a seed label
    ("1A", "2B", "3ABCDF") or a reference to an earlier match ("W89" = winner of
    match number 89, "RU101" = loser of semi-final 101), which we translate to
    the referenced match id.
    """
    base = _fixture_from_match(match)
    home = match.get("Home") or {}
    away = match.get("Away") or {}
    return replace(
        base,
        home_team=home.get("Abbreviation") or home.get("IdCountry") or "",
        away_team=away.get("Abbreviation") or away.get("IdCountry") or "",
        home_source=_translate_placeholder(match.get("PlaceHolderA"), number_to_id),
        away_source=_translate_placeholder(match.get("PlaceHolderB"), number_to_id),
    )


def _translate_placeholder(value: str | None, number_to_id: dict) -> str | None:
    if not value:
        return None
    label = value.strip().upper()
    if label.startswith("RU") and label[2:].isdigit():
        referenced = number_to_id.get(int(label[2:]))
        return f"RU:{referenced}" if referenced else label
    if label.startswith("W") and label[1:].isdigit():
        referenced = number_to_id.get(int(label[1:]))
        return f"W:{referenced}" if referenced else label
    return label  # seed labels such as 1A, 2B, 3ABCDF


def _result_from_match(match: dict) -> MatchResult | None:
    home_score = match.get("HomeTeamScore")
    away_score = match.get("AwayTeamScore")
    if match.get("MatchStatus") != FIFA_COMPLETED_STATUS or home_score is None or away_score is None:
        return None
    home_penalties = match.get("HomeTeamPenaltyScore")
    away_penalties = match.get("AwayTeamPenaltyScore")
    result_type = match.get("ResultType")
    if home_penalties is not None and away_penalties is not None:
        method = MethodOfWin.PENALTIES
    elif result_type == 2:
        method = MethodOfWin.PENALTIES
    elif home_score == away_score:
        method = MethodOfWin.DRAW
    else:
        method = MethodOfWin.REGULATION
    home = match.get("Home") or {}
    away = match.get("Away") or {}
    return MatchResult(
        match_id=str(match.get("IdMatch")),
        home_goals=int(home_score),
        away_goals=int(away_score),
        home_penalties=_optional_int(home_penalties),
        away_penalties=_optional_int(away_penalties),
        method=method,
        locked=True,
        notes="official_fifa_sync",
        # Stage and team codes let knockout results be matched to bracket slots
        # by team pair, since the simulator generates its own knockout ids.
        stage=_stage_from_match(match),
        home_team=(home.get("Abbreviation") or home.get("IdCountry")) or None,
        away_team=(away.get("Abbreviation") or away.get("IdCountry")) or None,
    )


def _stage_from_match(match: dict) -> MatchStage:
    stage = _localized(match.get("StageName"))
    mapping = {
        "First Stage": MatchStage.GROUP,
        "Round of 32": MatchStage.ROUND_OF_32,
        "Round of 16": MatchStage.ROUND_OF_16,
        "Quarter-final": MatchStage.QUARTER_FINAL,
        "Semi-final": MatchStage.SEMI_FINAL,
        "Play-off for third place": MatchStage.THIRD_PLACE,
        "Final": MatchStage.FINAL,
    }
    return mapping.get(stage, MatchStage.GROUP)


def _group_from_match(match: dict) -> str | None:
    group = _localized(match.get("GroupName"))
    if not group:
        return None
    return group.replace("Group ", "").strip()


def _team_name(team_payload: dict) -> str:
    return _localized(team_payload.get("TeamName")) or team_payload.get("ShortClubName") or team_payload.get("Abbreviation") or ""


def _localized(values: list[dict] | None, *, locale: str = "en-GB") -> str | None:
    if not values:
        return None
    for value in values:
        if value.get("Locale") == locale:
            return value.get("Description")
    return values[0].get("Description")


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)
