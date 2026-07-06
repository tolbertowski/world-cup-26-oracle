"""Fit team ratings from historical international results.

Source: martj42/international_results (results.csv) — every men's senior
international from 1872 to present, *goals only*. This module replays that
history into:

* an Elo-style overall rating per team, and
* goal-based attacking / defending strengths,

both recency- and competition-weighted. The dataset has no corner, card, or
referee information, so this module deliberately says nothing about discipline
or tempo — those stay heuristic in the model.

The fit is pure-stdlib and deterministic so it can run offline from a cached
snapshot and be unit tested without network access.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from world_cup_oracle.domain import Team
from world_cup_oracle.models import attack_from_rating, defense_from_rating


RESULTS_URL = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"

BASE_RATING = 1500.0
DEFAULT_K_FACTOR = 32.0
DEFAULT_HOME_ADVANTAGE = 70.0
DEFAULT_HALF_LIFE_DAYS = 1095.0
# World Cup 2026 kickoff: the fit drops matches from this date onward so that
# live-tournament games only enter the model through match_updates.csv (which
# updates ratings in-tournament) and are never double-counted by a re-fit.
WORLD_CUP_2026_START = date(2026, 6, 11)

ATTACK_BOUNDS = (0.75, 1.4)
DEFENSE_BOUNDS = (0.75, 1.4)
MAX_STRENGTH_DELTA = 0.25
# Goal-ratio deviations are large (top sides score ~2x the global mean); damp them
# so multipliers spread across the bounds instead of all saturating the cap.
STRENGTH_DAMPING = 0.3

# martj42 spellings that differ from our team_name column. Everything else
# matches verbatim, so only the exceptions live here.
TEAM_ALIASES: dict[str, str] = {
    "Czech Republic": "CZE",
    "South Korea": "KOR",
    "Turkey": "TUR",
    "United States": "USA",
    "Ivory Coast": "CIV",
    "Iran": "IRN",
    "Cape Verde": "CPV",
    "DR Congo": "COD",
}

_REQUIRED_COLUMNS = ["date", "home_team", "away_team", "home_score", "away_score", "tournament", "neutral"]


@dataclass(frozen=True, slots=True)
class MatchRecord:
    match_date: date
    home_team: str
    away_team: str
    home_goals: int
    away_goals: int
    tournament: str
    neutral: bool


@dataclass(frozen=True, slots=True)
class FittedRating:
    team_code: str
    source_name: str
    elo: float
    attack: float
    defense: float
    matches: int

    @property
    def attack_delta(self) -> float:
        return _clamp(self.attack - attack_from_rating(self.elo), -MAX_STRENGTH_DELTA, MAX_STRENGTH_DELTA)

    @property
    def defense_delta(self) -> float:
        return _clamp(self.defense - defense_from_rating(self.elo), -MAX_STRENGTH_DELTA, MAX_STRENGTH_DELTA)

    def as_adjustment_row(self, *, note_prefix: str) -> dict[str, str]:
        return {
            "team_code": self.team_code,
            "rating_delta": "0.0",
            "attack_delta": f"{self.attack_delta:.3f}",
            "defense_delta": f"{self.defense_delta:.3f}",
            "discipline_delta": "0.0",
            "tempo_delta": "0.0",
            "notes": (
                f"{note_prefix} elo={self.elo:.0f}; "
                f"attack={self.attack:.2f}; defense={self.defense:.2f}; matches={self.matches}"
            ),
        }


def read_results(path: Path) -> list[MatchRecord]:
    """Read results.csv, skipping unplayed (NA score) rows."""
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        _require_columns(path, reader.fieldnames)
        records: list[MatchRecord] = []
        for row in reader:
            home_goals = _optional_int(row.get("home_score"))
            away_goals = _optional_int(row.get("away_score"))
            if home_goals is None or away_goals is None:
                continue
            match_date = _parse_date(row.get("date"))
            if match_date is None:
                continue
            records.append(
                MatchRecord(
                    match_date=match_date,
                    home_team=(row.get("home_team") or "").strip(),
                    away_team=(row.get("away_team") or "").strip(),
                    home_goals=home_goals,
                    away_goals=away_goals,
                    tournament=(row.get("tournament") or "").strip(),
                    neutral=(row.get("neutral") or "").strip().upper() == "TRUE",
                )
            )
        return records


def competition_weight(tournament: str) -> float:
    name = tournament.lower()
    if "friendly" in name:
        return 0.3
    if "qualification" in name or "qualifier" in name:
        return 0.8 if "world cup" in name else 0.7
    if "fifa world cup" in name:
        return 1.0
    if "confederations cup" in name:
        return 0.8
    if "nations league" in name:
        return 0.7
    # Continental finals: Euro, Copa América, AFC Asian Cup, Africa Cup of Nations, Gold Cup, etc.
    if any(token in name for token in ("uefa euro", "copa am", "asian cup", "african", "gold cup", "championship", "nations cup")):
        return 0.9
    return 0.5


def time_weight(match_date: date, as_of: date, half_life_days: float = DEFAULT_HALF_LIFE_DAYS) -> float:
    age_days = max(0, (as_of - match_date).days)
    return 0.5 ** (age_days / half_life_days)


def fit_elo(
    records: list[MatchRecord],
    *,
    k_factor: float = DEFAULT_K_FACTOR,
    home_advantage: float = DEFAULT_HOME_ADVANTAGE,
) -> dict[str, float]:
    """Running Elo over the full history, K scaled by match importance.

    No explicit time decay: Elo is a running estimate, so recent results
    naturally dominate the current rating as older ones wash out. (Time decay is
    applied in the attack/defense fit, which pools averages rather than updating
    a running value.)
    """
    ratings: dict[str, float] = {}
    for record in sorted(records, key=lambda item: item.match_date):
        home = ratings.get(record.home_team, BASE_RATING)
        away = ratings.get(record.away_team, BASE_RATING)
        advantage = 0.0 if record.neutral else home_advantage
        expected_home = 1.0 / (1.0 + 10 ** ((away - (home + advantage)) / 400.0))
        actual_home = _actual_home_score(record)
        movement = k_factor * competition_weight(record.tournament) * (actual_home - expected_home)
        ratings[record.home_team] = home + movement
        ratings[record.away_team] = away - movement
    return ratings


def fit_attack_defense(
    records: list[MatchRecord],
    *,
    as_of: date | None = None,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
) -> dict[str, tuple[float, float]]:
    """Return {team: (attack_multiplier, defense_multiplier)} on the model scale.

    Higher attack = scores more than average; higher defense = concedes less.
    Strengths are weighted, normalized to a league mean of 1.0, then refined with
    one opponent-strength correction pass.
    """
    as_of = as_of or _max_date(records)
    weighted = [
        (record, competition_weight(record.tournament) * time_weight(record.match_date, as_of, half_life_days))
        for record in records
    ]

    raw_attack, raw_concede = _strength_pass(weighted, opponent_attack=None, opponent_concede=None)
    # Refine once using the first-pass opponent strengths.
    attack, concede = _strength_pass(weighted, opponent_attack=raw_attack, opponent_concede=raw_concede)

    return {
        team: (
            _clamp(1.0 + STRENGTH_DAMPING * (attack[team] - 1.0), *ATTACK_BOUNDS),
            # Conceding fewer goals than average (concede < 1) means a higher defense multiplier.
            _clamp(1.0 + STRENGTH_DAMPING * (1.0 - concede[team]), *DEFENSE_BOUNDS),
        )
        for team in attack
    }


def fit_average_goals(
    records: list[MatchRecord],
    *,
    as_of: date | None = None,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
) -> float:
    """Weighted mean of total goals per match, on the same recency/importance
    weights as the strength fit. Anchors the model's expected-goals baseline to
    what competitive internationals actually produce instead of a hardcoded
    constant."""
    as_of = as_of or _max_date(records)
    total_goals = 0.0
    total_weight = 0.0
    for record in records:
        weight = competition_weight(record.tournament) * time_weight(record.match_date, as_of, half_life_days)
        total_goals += weight * (record.home_goals + record.away_goals)
        total_weight += weight
    if total_weight == 0:
        return 2.62
    return total_goals / total_weight


def fit_team_ratings(
    records: list[MatchRecord],
    teams: list[Team],
    *,
    k_factor: float = DEFAULT_K_FACTOR,
    home_advantage: float = DEFAULT_HOME_ADVANTAGE,
    as_of: date | None = None,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
) -> tuple[list[FittedRating], list[str]]:
    """Fit ratings over the full dataset and project onto our tournament teams.

    Returns (fitted ratings sorted by team_code, unmatched team_codes).
    """
    as_of = as_of or _max_date(records)
    elo = fit_elo(records, k_factor=k_factor, home_advantage=home_advantage)
    strengths = fit_attack_defense(records, as_of=as_of, half_life_days=half_life_days)
    match_counts = _match_counts(records)

    fitted: list[FittedRating] = []
    unmatched: list[str] = []
    for team in teams:
        source = source_name_for(team)
        if source not in elo:
            unmatched.append(team.code)
            continue
        attack, defense = strengths.get(source, (1.0, 1.0))
        fitted.append(
            FittedRating(
                team_code=team.code,
                source_name=source,
                elo=elo[source],
                attack=attack,
                defense=defense,
                matches=match_counts.get(source, 0),
            )
        )
    fitted.sort(key=lambda item: item.team_code)
    return fitted, sorted(unmatched)


def source_name_for(team: Team) -> str:
    for source, code in TEAM_ALIASES.items():
        if code == team.code:
            return source
    return team.name


def _strength_pass(
    weighted: list[tuple[MatchRecord, float]],
    *,
    opponent_attack: dict[str, float] | None,
    opponent_concede: dict[str, float] | None,
) -> tuple[dict[str, float], dict[str, float]]:
    goals_for: dict[str, float] = {}
    goals_against: dict[str, float] = {}
    weight_sum: dict[str, float] = {}
    total_goals = 0.0
    total_weight = 0.0
    for record, weight in weighted:
        for team, opponent, scored, conceded in (
            (record.home_team, record.away_team, record.home_goals, record.away_goals),
            (record.away_team, record.home_team, record.away_goals, record.home_goals),
        ):
            adj_scored = scored
            adj_conceded = conceded
            if opponent_concede is not None:
                adj_scored = scored / max(0.2, opponent_concede.get(opponent, 1.0))
            if opponent_attack is not None:
                adj_conceded = conceded / max(0.2, opponent_attack.get(opponent, 1.0))
            goals_for[team] = goals_for.get(team, 0.0) + weight * adj_scored
            goals_against[team] = goals_against.get(team, 0.0) + weight * adj_conceded
            weight_sum[team] = weight_sum.get(team, 0.0) + weight
            total_goals += weight * scored
            total_weight += weight
    league_avg = (total_goals / total_weight) if total_weight else 1.0
    league_avg = max(0.2, league_avg)
    attack = {team: (goals_for[team] / weight_sum[team]) / league_avg for team in goals_for if weight_sum[team] > 0}
    concede = {team: (goals_against[team] / weight_sum[team]) / league_avg for team in goals_against if weight_sum[team] > 0}
    return attack, concede


def _match_counts(records: list[MatchRecord]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        counts[record.home_team] = counts.get(record.home_team, 0) + 1
        counts[record.away_team] = counts.get(record.away_team, 0) + 1
    return counts


def _actual_home_score(record: MatchRecord) -> float:
    if record.home_goals > record.away_goals:
        return 1.0
    if record.home_goals < record.away_goals:
        return 0.0
    return 0.5


def _max_date(records: list[MatchRecord]) -> date:
    if not records:
        return date.today()
    return max(record.match_date for record in records)


def _require_columns(path: Path, fieldnames: list[str] | None) -> None:
    missing = [column for column in _REQUIRED_COLUMNS if column not in (fieldnames or [])]
    if missing:
        raise ValueError(f"{path} is missing required columns: {', '.join(missing)}")


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _optional_int(value: str | None) -> int | None:
    if value is None:
        return None
    text = value.strip()
    if text == "" or text.upper() == "NA":
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _clamp(value: float, lower: float, upper: float) -> float:
    return min(upper, max(lower, value))
