"""Tournament rules for the 48-team World Cup format."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from itertools import count

from world_cup_oracle.domain import (
    Fixture,
    MatchResult,
    MatchStage,
    QualifiedTeam,
    StandingRow,
    Team,
)


GROUPS = tuple("ABCDEFGHIJKL")


@dataclass(frozen=True, slots=True)
class KnockoutFixtureTemplate:
    match_id: str
    stage: MatchStage
    home_seed: str
    away_seed: str


@dataclass(frozen=True, slots=True)
class GroupStageResult:
    standings: dict[str, list[StandingRow]]
    automatic_qualifiers: list[QualifiedTeam]
    third_place_qualifiers: list[QualifiedTeam]

    @property
    def qualified(self) -> list[QualifiedTeam]:
        return [*self.automatic_qualifiers, *self.third_place_qualifiers]


DEFAULT_R32_TEMPLATES: tuple[KnockoutFixtureTemplate, ...] = (
    KnockoutFixtureTemplate("R32-01", MatchStage.ROUND_OF_32, "1A", "3C/3D/3E/3F"),
    KnockoutFixtureTemplate("R32-02", MatchStage.ROUND_OF_32, "2B", "2E"),
    KnockoutFixtureTemplate("R32-03", MatchStage.ROUND_OF_32, "1F", "2C"),
    KnockoutFixtureTemplate("R32-04", MatchStage.ROUND_OF_32, "1D", "3B/3E/3F/3I"),
    KnockoutFixtureTemplate("R32-05", MatchStage.ROUND_OF_32, "1C", "3A/3D/3E/3F"),
    KnockoutFixtureTemplate("R32-06", MatchStage.ROUND_OF_32, "2A", "2D"),
    KnockoutFixtureTemplate("R32-07", MatchStage.ROUND_OF_32, "1E", "2F"),
    KnockoutFixtureTemplate("R32-08", MatchStage.ROUND_OF_32, "1B", "3A/3C/3D/3E"),
    KnockoutFixtureTemplate("R32-09", MatchStage.ROUND_OF_32, "1G", "3H/3I/3J/3K"),
    KnockoutFixtureTemplate("R32-10", MatchStage.ROUND_OF_32, "2H", "2J"),
    KnockoutFixtureTemplate("R32-11", MatchStage.ROUND_OF_32, "1I", "2G"),
    KnockoutFixtureTemplate("R32-12", MatchStage.ROUND_OF_32, "1K", "3E/3F/3I/3J"),
    KnockoutFixtureTemplate("R32-13", MatchStage.ROUND_OF_32, "1H", "3G/3I/3J/3K"),
    KnockoutFixtureTemplate("R32-14", MatchStage.ROUND_OF_32, "2I", "2K"),
    KnockoutFixtureTemplate("R32-15", MatchStage.ROUND_OF_32, "1J", "2L"),
    KnockoutFixtureTemplate("R32-16", MatchStage.ROUND_OF_32, "1L", "3G/3H/3I/3J"),
)


def group_teams(teams: list[Team]) -> dict[str, list[Team]]:
    grouped: dict[str, list[Team]] = defaultdict(list)
    for team in teams:
        if team.group is None:
            continue
        grouped[team.group].append(team)
    return dict(grouped)


def calculate_group_stage(
    teams: list[Team],
    fixtures: list[Fixture],
    results: dict[str, MatchResult] | None = None,
    third_place_count: int = 8,
) -> GroupStageResult:
    results = results or {}
    grouped = group_teams(teams)
    standings: dict[str, list[StandingRow]] = {}
    automatic: list[QualifiedTeam] = []
    thirds: list[QualifiedTeam] = []

    for group, group_members in sorted(grouped.items()):
        rows = _group_rows(group_members)
        group_fixtures = [fixture for fixture in fixtures if fixture.group == group]
        for fixture in group_fixtures:
            result = results.get(fixture.match_id)
            if result is None or not result.locked:
                continue
            home_delta, away_delta = _fair_play_deltas(result)
            rows[fixture.home_team] = rows[fixture.home_team].record_match(
                result.home_goals,
                result.away_goals,
                home_delta,
            )
            rows[fixture.away_team] = rows[fixture.away_team].record_match(
                result.away_goals,
                result.home_goals,
                away_delta,
            )

        ranked = rank_group(list(rows.values()), group_fixtures, results)
        standings[group] = ranked
        for rank, row in enumerate(ranked[:2], start=1):
            automatic.append(QualifiedTeam(row.team_code, group, rank, row))
        if len(ranked) >= 3:
            row = ranked[2]
            thirds.append(QualifiedTeam(row.team_code, group, 3, row))

    best_thirds = rank_third_place_teams(thirds)[:third_place_count]
    return GroupStageResult(standings, automatic, best_thirds)


def rank_group(
    rows: list[StandingRow],
    fixtures: list[Fixture],
    results: dict[str, MatchResult],
) -> list[StandingRow]:
    globally_ranked = sorted(rows, key=_global_group_key)
    ranked: list[StandingRow] = []
    index = 0
    while index < len(globally_ranked):
        tied = [globally_ranked[index]]
        base = _global_without_fair_play(globally_ranked[index])
        index += 1
        while index < len(globally_ranked) and _global_without_fair_play(globally_ranked[index]) == base:
            tied.append(globally_ranked[index])
            index += 1
        if len(tied) == 1:
            ranked.extend(tied)
            continue
        ranked.extend(_rank_tied_rows(tied, fixtures, results))
    return ranked


def rank_third_place_teams(thirds: list[QualifiedTeam]) -> list[QualifiedTeam]:
    return sorted(thirds, key=lambda team: _global_group_key(team.standing))


def build_round_of_32(
    qualifiers: list[QualifiedTeam],
    templates: tuple[KnockoutFixtureTemplate, ...] = DEFAULT_R32_TEMPLATES,
) -> list[Fixture]:
    by_seed = {team.seed_label: team for team in qualifiers}
    third_seeds = {team.group: team for team in qualifiers if team.rank == 3}
    used_thirds: set[str] = set()
    fixtures: list[Fixture] = []

    for template in templates:
        home = _resolve_seed(template.home_seed, by_seed, third_seeds, used_thirds)
        away = _resolve_seed(template.away_seed, by_seed, third_seeds, used_thirds)
        fixtures.append(
            Fixture(
                match_id=template.match_id,
                stage=template.stage,
                home_team=home.team_code,
                away_team=away.team_code,
            )
        )
    return fixtures


def build_next_round_fixtures(
    winners: list[str],
    stage: MatchStage,
    prefix: str,
) -> list[Fixture]:
    if len(winners) % 2 != 0:
        raise ValueError("Knockout winners must be paired evenly.")
    ids = count(1)
    fixtures: list[Fixture] = []
    for index in range(0, len(winners), 2):
        fixtures.append(
            Fixture(
                match_id=f"{prefix}-{next(ids):02d}",
                stage=stage,
                home_team=winners[index],
                away_team=winners[index + 1],
            )
        )
    return fixtures


def winner_from_result(fixture: Fixture, result: MatchResult) -> str | None:
    winner_side = result.winner_side
    if winner_side == "home":
        return fixture.home_team
    if winner_side == "away":
        return fixture.away_team
    return None


def _group_rows(teams: list[Team]) -> dict[str, StandingRow]:
    return {
        team.code: StandingRow(
            team_code=team.code,
            group=team.group or "",
            seed_rating=team.seed_rating,
        )
        for team in teams
    }


def _fair_play_deltas(result: MatchResult) -> tuple[int, int]:
    home = -1 * (result.home_yellow_cards or 0) - 4 * (result.home_red_cards or 0)
    away = -1 * (result.away_yellow_cards or 0) - 4 * (result.away_red_cards or 0)
    return home, away


def _global_without_fair_play(row: StandingRow) -> tuple[int, int, int]:
    return (row.points, row.goal_difference, row.goals_for)


def _global_group_key(row: StandingRow) -> tuple[int, int, int, int, float, str]:
    return (
        -row.points,
        -row.goal_difference,
        -row.goals_for,
        -row.fair_play_points,
        -row.seed_rating,
        row.team_code,
    )


def _rank_tied_rows(
    tied: list[StandingRow],
    fixtures: list[Fixture],
    results: dict[str, MatchResult],
) -> list[StandingRow]:
    tied_codes = {row.team_code for row in tied}
    h2h_rows = {row.team_code: StandingRow(row.team_code, row.group) for row in tied}
    for fixture in fixtures:
        if fixture.home_team not in tied_codes or fixture.away_team not in tied_codes:
            continue
        result = results.get(fixture.match_id)
        if result is None or not result.locked:
            continue
        h2h_rows[fixture.home_team] = h2h_rows[fixture.home_team].record_match(
            result.home_goals,
            result.away_goals,
        )
        h2h_rows[fixture.away_team] = h2h_rows[fixture.away_team].record_match(
            result.away_goals,
            result.home_goals,
        )

    by_code = {row.team_code: row for row in tied}

    def key(row: StandingRow) -> tuple[int, int, int, int, int, float, str]:
        h2h = h2h_rows[row.team_code]
        return (
            -h2h.points,
            -h2h.goal_difference,
            -h2h.goals_for,
            -row.fair_play_points,
            -row.points,
            -row.seed_rating,
            row.team_code,
        )

    return [by_code[row.team_code] for row in sorted(tied, key=key)]


def _resolve_seed(
    seed_expression: str,
    by_seed: dict[str, QualifiedTeam],
    third_seeds: dict[str, QualifiedTeam],
    used_thirds: set[str],
) -> QualifiedTeam:
    if "/" not in seed_expression:
        if seed_expression in by_seed:
            return by_seed[seed_expression]
        raise KeyError(f"Missing qualifier for seed {seed_expression}.")

    candidates = [seed.replace("3", "", 1) for seed in seed_expression.split("/")]
    for group in candidates:
        if group in third_seeds and group not in used_thirds:
            used_thirds.add(group)
            return third_seeds[group]

    for group, qualifier in sorted(third_seeds.items()):
        if group not in used_thirds:
            used_thirds.add(group)
            return qualifier
    raise KeyError(f"No available third-place qualifier for seed {seed_expression}.")
