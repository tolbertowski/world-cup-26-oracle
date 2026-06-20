"""Tournament source-data pipeline.

This module converts raw CSV snapshots into the typed objects used by the app.
It is intentionally local-file first: fetch/cache data separately, review it,
then import it into `data/processed/`.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field, replace
from pathlib import Path

from world_cup_oracle.data.sample import build_demo_fixtures, build_demo_teams
from world_cup_oracle.domain import Fixture, MatchStage, Team


TEAM_SOURCE_COLUMNS = [
    "team_code",
    "team_name",
    "group",
    "confederation",
    "fifa_rank",
    "seed_rating",
]
FIXTURE_SOURCE_COLUMNS = [
    "match_id",
    "stage",
    "home_team",
    "away_team",
    "group",
    "kickoff",
    "venue",
    "neutral_site",
]


@dataclass(frozen=True, slots=True)
class DataValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def render(self) -> str:
        lines: list[str] = []
        lines.extend(f"ERROR: {message}" for message in self.errors)
        lines.extend(f"WARNING: {message}" for message in self.warnings)
        if not lines:
            return "OK"
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class TournamentData:
    teams: list[Team]
    fixtures: list[Fixture]
    source: str


def write_source_templates(raw_dir: Path) -> list[Path]:
    raw_dir.mkdir(parents=True, exist_ok=True)
    team_path = raw_dir / "teams_template.csv"
    fixture_path = raw_dir / "fixtures_template.csv"
    _write_header_if_missing(team_path, TEAM_SOURCE_COLUMNS)
    _write_header_if_missing(fixture_path, FIXTURE_SOURCE_COLUMNS)
    return [team_path, fixture_path]


def read_teams_csv(path: Path) -> list[Team]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        _require_columns(path, reader.fieldnames, TEAM_SOURCE_COLUMNS)
        return [_team_from_row(row) for row in reader if row.get("team_code")]


def read_fixtures_csv(path: Path) -> list[Fixture]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        _require_columns(path, reader.fieldnames, FIXTURE_SOURCE_COLUMNS)
        return [_fixture_from_row(row) for row in reader if row.get("match_id")]


def import_tournament_snapshot(
    teams_path: Path,
    fixtures_path: Path,
    processed_dir: Path,
    *,
    strict: bool = False,
) -> DataValidationReport:
    teams = read_teams_csv(teams_path)
    fixtures = read_fixtures_csv(fixtures_path)
    report = validate_tournament_data(teams, fixtures, strict=strict)
    if not report.ok:
        return report
    export_processed_data(teams, fixtures, processed_dir)
    return report


def write_teams_csv(teams: list[Team], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
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
    return path


def update_seed_ratings(teams_path: Path, ratings_by_code: dict[str, float]) -> list[Team]:
    """Rewrite teams.csv with new seed ratings, leaving unmatched teams unchanged."""
    teams = read_teams_csv(teams_path)
    updated = [
        replace(team, seed_rating=round(ratings_by_code[team.code], 1)) if team.code in ratings_by_code else team
        for team in teams
    ]
    write_teams_csv(updated, teams_path)
    return updated


def export_processed_data(teams: list[Team], fixtures: list[Fixture], processed_dir: Path) -> list[Path]:
    processed_dir.mkdir(parents=True, exist_ok=True)
    teams_path = write_teams_csv(teams, processed_dir / "teams.csv")
    fixtures_path = processed_dir / "fixtures.csv"
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
    return [teams_path, fixtures_path]


def load_processed_or_demo(processed_dir: Path) -> TournamentData:
    teams_path = processed_dir / "teams.csv"
    fixtures_path = processed_dir / "fixtures.csv"
    if teams_path.exists() and fixtures_path.exists():
        return TournamentData(
            teams=read_teams_csv(teams_path),
            fixtures=read_fixtures_csv(fixtures_path),
            source="processed",
        )
    return TournamentData(
        teams=build_demo_teams(),
        fixtures=build_demo_fixtures(),
        source="demo",
    )


def release_check(processed_dir: Path) -> DataValidationReport:
    teams_path = processed_dir / "teams.csv"
    fixtures_path = processed_dir / "fixtures.csv"
    if not teams_path.exists() or not fixtures_path.exists():
        return DataValidationReport(errors=["Release blocked: processed official data is missing; app would use demo data."])
    teams = read_teams_csv(teams_path)
    fixtures = read_fixtures_csv(fixtures_path)
    return validate_tournament_data(teams, fixtures, strict=True)


def validate_tournament_data(
    teams: list[Team],
    fixtures: list[Fixture],
    *,
    strict: bool = False,
) -> DataValidationReport:
    errors: list[str] = []
    warnings: list[str] = []
    team_codes = [team.code for team in teams]
    unique_codes = set(team_codes)

    if len(unique_codes) != len(team_codes):
        errors.append("Team codes must be unique.")
    if any(not team.code.isupper() for team in teams):
        warnings.append("Team codes should be uppercase.")

    grouped: dict[str, list[str]] = {}
    for team in teams:
        if team.group:
            grouped.setdefault(team.group, []).append(team.code)
    for group, codes in sorted(grouped.items()):
        if strict and len(codes) != 4:
            errors.append(f"Group {group} must contain exactly 4 teams in strict mode.")
        elif len(codes) != 4:
            warnings.append(f"Group {group} has {len(codes)} teams; expected 4 for the 2026 format.")

    match_ids = [fixture.match_id for fixture in fixtures]
    if len(set(match_ids)) != len(match_ids):
        errors.append("Match IDs must be unique.")

    for fixture in fixtures:
        if fixture.home_team not in unique_codes:
            errors.append(f"{fixture.match_id} references unknown home team {fixture.home_team}.")
        if fixture.away_team not in unique_codes:
            errors.append(f"{fixture.match_id} references unknown away team {fixture.away_team}.")
        if fixture.home_team == fixture.away_team:
            errors.append(f"{fixture.match_id} uses the same home and away team.")
        if fixture.stage == MatchStage.GROUP and not fixture.group:
            errors.append(f"{fixture.match_id} is a group fixture but has no group.")

    group_fixtures = [fixture for fixture in fixtures if fixture.stage == MatchStage.GROUP]
    if strict:
        if len(teams) != 48:
            errors.append("Strict 2026 snapshots must contain 48 teams.")
        if len(group_fixtures) != 72:
            errors.append("Strict 2026 snapshots must contain 72 group fixtures.")
    elif len(group_fixtures) and len(group_fixtures) != 72:
        warnings.append("Group fixture count is not 72; this may be a partial or demo snapshot.")

    return DataValidationReport(errors=errors, warnings=warnings)


def _team_from_row(row: dict[str, str]) -> Team:
    return Team(
        code=_clean_code(row["team_code"]),
        name=row["team_name"].strip(),
        group=_optional_text(row.get("group")),
        confederation=_optional_text(row.get("confederation")),
        fifa_rank=_optional_int(row.get("fifa_rank")),
        seed_rating=_optional_float(row.get("seed_rating"), default=1500.0),
    )


def _fixture_from_row(row: dict[str, str]) -> Fixture:
    return Fixture(
        match_id=row["match_id"].strip(),
        stage=MatchStage(row.get("stage", MatchStage.GROUP.value).strip() or MatchStage.GROUP.value),
        home_team=_clean_code(row["home_team"]),
        away_team=_clean_code(row["away_team"]),
        group=_optional_text(row.get("group")),
        kickoff=_optional_text(row.get("kickoff")),
        venue=_optional_text(row.get("venue")),
        neutral_site=_optional_bool(row.get("neutral_site"), default=True),
    )


def _require_columns(path: Path, fieldnames: list[str] | None, required: list[str]) -> None:
    missing = [column for column in required if column not in (fieldnames or [])]
    if missing:
        raise ValueError(f"{path} is missing required columns: {', '.join(missing)}")


def _write_header_if_missing(path: Path, columns: list[str]) -> None:
    if path.exists():
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(columns)


def _clean_code(value: str) -> str:
    return value.strip().upper()


def _optional_text(value: str | None) -> str | None:
    if value is None or value.strip() == "":
        return None
    return value.strip()


def _optional_int(value: str | None) -> int | None:
    if value is None or value.strip() == "":
        return None
    return int(value)


def _optional_float(value: str | None, *, default: float) -> float:
    if value is None or value.strip() == "":
        return default
    return float(value)


def _optional_bool(value: str | None, *, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y"}
