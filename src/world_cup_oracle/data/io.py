"""CSV import/export helpers for manual-assisted tournament updates."""

from __future__ import annotations

import csv
from pathlib import Path
from urllib.request import urlopen

from world_cup_oracle.domain import MatchResult, MethodOfWin, TeamRating


MANUAL_MATCH_COLUMNS = [
    "match_id",
    "home_goals",
    "away_goals",
    "home_penalties",
    "away_penalties",
    "home_yellow_cards",
    "away_yellow_cards",
    "home_red_cards",
    "away_red_cards",
    "home_corners",
    "away_corners",
    "played_at",
    "notes",
]
TEAM_ADJUSTMENT_COLUMNS = [
    "team_code",
    "rating_delta",
    "attack_delta",
    "defense_delta",
    "discipline_delta",
    "tempo_delta",
    "notes",
]


def read_match_updates(path: Path) -> dict[str, MatchResult]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        updates: dict[str, MatchResult] = {}
        for row in reader:
            if not row.get("match_id") or row.get("home_goals", "") == "":
                continue
            result = MatchResult(
                match_id=row["match_id"],
                home_goals=_int(row.get("home_goals")),
                away_goals=_int(row.get("away_goals")),
                home_penalties=_optional_int(row.get("home_penalties")),
                away_penalties=_optional_int(row.get("away_penalties")),
                home_yellow_cards=_optional_int(row.get("home_yellow_cards")),
                away_yellow_cards=_optional_int(row.get("away_yellow_cards")),
                home_red_cards=_optional_int(row.get("home_red_cards")),
                away_red_cards=_optional_int(row.get("away_red_cards")),
                home_corners=_optional_int(row.get("home_corners")),
                away_corners=_optional_int(row.get("away_corners")),
                method=_infer_method(row),
                locked=True,
                notes=row.get("notes") or None,
            )
            updates[result.match_id] = result
    return updates


def read_team_adjustments(path: Path) -> dict[str, dict[str, float]]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        adjustments: dict[str, dict[str, float]] = {}
        for row in reader:
            code = row.get("team_code")
            if not code:
                continue
            adjustments[code] = {
                "rating_delta": _float(row.get("rating_delta")),
                "attack_delta": _float(row.get("attack_delta")),
                "defense_delta": _float(row.get("defense_delta")),
                "discipline_delta": _float(row.get("discipline_delta")),
                "tempo_delta": _float(row.get("tempo_delta")),
            }
    return adjustments


def apply_team_adjustments(
    ratings: dict[str, TeamRating],
    adjustments: dict[str, dict[str, float]],
) -> dict[str, TeamRating]:
    adjusted = dict(ratings)
    for code, deltas in adjustments.items():
        if code not in adjusted:
            continue
        rating = adjusted[code]
        adjusted[code] = TeamRating(
            team_code=rating.team_code,
            rating=rating.rating + deltas.get("rating_delta", 0.0),
            attack=max(0.2, rating.attack + deltas.get("attack_delta", 0.0)),
            defense=max(0.2, rating.defense + deltas.get("defense_delta", 0.0)),
            discipline=max(0.2, rating.discipline + deltas.get("discipline_delta", 0.0)),
            tempo=max(0.2, rating.tempo + deltas.get("tempo_delta", 0.0)),
            recent_form=rating.recent_form,
        )
    return adjusted


def write_manual_templates(manual_dir: Path) -> list[Path]:
    manual_dir.mkdir(parents=True, exist_ok=True)
    match_path = manual_dir / "match_updates.csv"
    team_path = manual_dir / "team_adjustments.csv"
    _write_header_if_missing(match_path, MANUAL_MATCH_COLUMNS)
    _write_header_if_missing(team_path, TEAM_ADJUSTMENT_COLUMNS)
    return [match_path, team_path]


def cache_url(url: str, cache_dir: Path, name: str | None = None) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    filename = name or url.rstrip("/").split("/")[-1] or "snapshot"
    target = cache_dir / filename
    with urlopen(url, timeout=30) as response:
        target.write_bytes(response.read())
    return target


def _write_header_if_missing(path: Path, columns: list[str]) -> None:
    if path.exists():
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)


def _infer_method(row: dict[str, str]) -> MethodOfWin:
    home_goals = _int(row.get("home_goals"))
    away_goals = _int(row.get("away_goals"))
    if home_goals == away_goals and row.get("home_penalties") and row.get("away_penalties"):
        return MethodOfWin.PENALTIES
    if home_goals == away_goals:
        return MethodOfWin.DRAW
    return MethodOfWin.REGULATION


def _int(value: str | None) -> int:
    if value is None or value == "":
        return 0
    return int(value)


def _optional_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _float(value: str | None) -> float:
    if value is None or value == "":
        return 0.0
    return float(value)
