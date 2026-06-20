"""Player call-up aggregation for portfolio-grade team adjustments.

This module intentionally treats player data as an offline, reviewed input. The
live app keeps using team-level ratings; this layer turns a squad file into
small, explainable deltas that can be merged into manual team adjustments.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from math import log10
from pathlib import Path

from world_cup_oracle.data.io import GENERATED_PLAYER_ADJUSTMENT_PREFIX


NEUTRAL_SQUAD_SCORE = 70.0
NEUTRAL_PLAYER_RATING = 68.0
MIN_PLAYERS_FOR_FULL_CONFIDENCE = 18
DEFAULT_MAX_RATING_DELTA = 80.0

POSITION_ALIASES = {
    "GK": "GK",
    "G": "GK",
    "GOALKEEPER": "GK",
    "KEEPER": "GK",
    "DEF": "DEF",
    "D": "DEF",
    "CB": "DEF",
    "LB": "DEF",
    "RB": "DEF",
    "LWB": "DEF",
    "RWB": "DEF",
    "MID": "MID",
    "M": "MID",
    "DM": "MID",
    "CDM": "MID",
    "CM": "MID",
    "AM": "MID",
    "CAM": "MID",
    "LM": "MID",
    "RM": "MID",
    "FWD": "FWD",
    "FW": "FWD",
    "FORWARD": "FWD",
    "ST": "FWD",
    "CF": "FWD",
    "LW": "FWD",
    "RW": "FWD",
    "W": "FWD",
    "WINGER": "FWD",
}

ROLE_WEIGHTS = {
    "starter": 1.0,
    "key": 0.95,
    "regular": 0.82,
    "rotation": 0.6,
    "squad": 0.35,
    "bench": 0.25,
    "fringe": 0.12,
    "reserve": 0.12,
    "out": 0.0,
    "injured": 0.0,
}


@dataclass(frozen=True, slots=True)
class PlayerCallup:
    team_code: str
    player_name: str
    position: str
    expected_role: str = "squad"
    player_rating: float | None = None
    minutes_share: float | None = None
    availability: float = 1.0
    club_strength: float | None = None
    market_value_eur: float | None = None
    notes: str | None = None

    @property
    def position_group(self) -> str:
        return normalize_position(self.position)

    @property
    def estimated_rating(self) -> float:
        return estimate_player_rating(self)

    @property
    def involvement(self) -> float:
        if self.minutes_share is not None:
            base = self.minutes_share
        else:
            base = ROLE_WEIGHTS.get(self.expected_role.strip().lower(), ROLE_WEIGHTS["squad"])
        return _clamp(base * self.availability, 0.0, 1.0)


@dataclass(frozen=True, slots=True)
class SquadAdjustment:
    team_code: str
    player_count: int
    squad_score: float
    attack_score: float
    defense_score: float
    tempo_score: float
    baseline_score: float
    confidence: float
    rating_delta: float
    attack_delta: float
    defense_delta: float
    discipline_delta: float
    tempo_delta: float

    def as_adjustment_row(self) -> dict[str, str]:
        return {
            "team_code": self.team_code,
            "rating_delta": f"{self.rating_delta:.1f}",
            "attack_delta": f"{self.attack_delta:.3f}",
            "defense_delta": f"{self.defense_delta:.3f}",
            "discipline_delta": f"{self.discipline_delta:.3f}",
            "tempo_delta": f"{self.tempo_delta:.3f}",
            "notes": (
                f"{GENERATED_PLAYER_ADJUSTMENT_PREFIX} "
                f"squad={self.squad_score:.1f}; "
                f"baseline={self.baseline_score:.1f}; "
                f"players={self.player_count}; "
                f"confidence={self.confidence:.2f}"
            ),
        }


@dataclass(frozen=True, slots=True)
class _EvaluatedPlayer:
    player: PlayerCallup
    rating: float
    involvement: float

    @property
    def impact(self) -> float:
        return self.rating * max(0.05, self.involvement)


@dataclass(frozen=True, slots=True)
class _SquadProfile:
    player_count: int
    squad_score: float
    attack_score: float
    defense_score: float
    tempo_score: float
    confidence: float


def read_player_callups(path: Path) -> list[PlayerCallup]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        _require_columns(path, reader.fieldnames, ["team_code", "player_name", "position"])
        return [_player_callup_from_row(row) for row in reader if row.get("team_code") and row.get("player_name")]


def build_player_callup_adjustments(
    callups: list[PlayerCallup],
    *,
    team_codes: set[str] | None = None,
    baseline_score: float | None = None,
    max_rating_delta: float = DEFAULT_MAX_RATING_DELTA,
) -> list[SquadAdjustment]:
    grouped: dict[str, list[PlayerCallup]] = {}
    known_codes = {code.upper() for code in team_codes} if team_codes is not None else None
    unknown_codes = sorted({callup.team_code for callup in callups} - known_codes) if known_codes is not None else []
    if unknown_codes:
        raise ValueError(f"Unknown team codes in player callups: {', '.join(unknown_codes)}")
    for callup in callups:
        if known_codes is not None and callup.team_code not in known_codes:
            continue
        grouped.setdefault(callup.team_code, []).append(callup)

    profiles = {team_code: _build_squad_profile(players) for team_code, players in grouped.items()}
    if not profiles:
        return []

    baseline = baseline_score if baseline_score is not None else _baseline_from_profiles(profiles)
    adjustments = [
        _build_adjustment(
            team_code,
            profile,
            baseline_score=baseline,
            max_rating_delta=max_rating_delta,
        )
        for team_code, profile in profiles.items()
    ]
    return sorted(adjustments, key=lambda item: item.team_code)


def estimate_player_rating(callup: PlayerCallup) -> float:
    if callup.player_rating is not None:
        return _clamp(callup.player_rating, 40.0, 99.0)

    components: list[tuple[float, float]] = []
    if callup.market_value_eur is not None and callup.market_value_eur > 0:
        value_log = _clamp(log10(max(callup.market_value_eur, 1.0) / 1_000_000.0), -1.0, 2.2)
        components.append((56.0 + value_log * 11.0, 0.6))
    if callup.club_strength is not None:
        components.append((55.0 + _clamp(callup.club_strength, 0.0, 100.0) * 0.35, 0.4))
    if not components:
        return NEUTRAL_PLAYER_RATING
    total_weight = sum(weight for _, weight in components)
    return _clamp(sum(score * weight for score, weight in components) / total_weight, 40.0, 99.0)


def normalize_position(position: str) -> str:
    normalized = position.strip().upper().replace("-", "").replace(" ", "")
    return POSITION_ALIASES.get(normalized, "UNK")


def _player_callup_from_row(row: dict[str, str]) -> PlayerCallup:
    return PlayerCallup(
        team_code=row["team_code"].strip().upper(),
        player_name=row["player_name"].strip(),
        position=row["position"].strip().upper(),
        expected_role=(row.get("expected_role") or "squad").strip().lower(),
        player_rating=_optional_float(row.get("player_rating")),
        minutes_share=_optional_ratio(row.get("minutes_share")),
        availability=_optional_ratio(row.get("availability"), default=1.0),
        club_strength=_optional_float(row.get("club_strength")),
        market_value_eur=_optional_money(row.get("market_value_eur")),
        notes=(row.get("notes") or "").strip() or None,
    )


def _build_squad_profile(players: list[PlayerCallup]) -> _SquadProfile:
    evaluated = [
        _EvaluatedPlayer(player=player, rating=player.estimated_rating, involvement=player.involvement)
        for player in players
        if player.involvement > 0
    ]
    if not evaluated:
        return _SquadProfile(
            player_count=len(players),
            squad_score=NEUTRAL_SQUAD_SCORE,
            attack_score=NEUTRAL_SQUAD_SCORE,
            defense_score=NEUTRAL_SQUAD_SCORE,
            tempo_score=NEUTRAL_SQUAD_SCORE,
            confidence=0.0,
        )

    ranked = sorted(evaluated, key=lambda item: item.impact, reverse=True)
    core_score = _weighted_average(ranked[:11])
    depth_score = _weighted_average(ranked[11:18]) if len(ranked) > 11 else core_score
    squad_score = 0.78 * core_score + 0.22 * depth_score
    confidence = min(1.0, len(ranked) / MIN_PLAYERS_FOR_FULL_CONFIDENCE)
    return _SquadProfile(
        player_count=len(players),
        squad_score=squad_score,
        attack_score=_position_score(ranked, {"FWD": 0.64, "MID": 0.36}, fallback=squad_score),
        defense_score=_position_score(ranked, {"GK": 0.26, "DEF": 0.54, "MID": 0.20}, fallback=squad_score),
        tempo_score=_position_score(ranked, {"FWD": 0.45, "MID": 0.42, "DEF": 0.13}, fallback=squad_score),
        confidence=confidence,
    )


def _baseline_from_profiles(profiles: dict[str, _SquadProfile]) -> float:
    if len(profiles) == 1:
        return NEUTRAL_SQUAD_SCORE
    return sum(profile.squad_score for profile in profiles.values()) / len(profiles)


def _build_adjustment(
    team_code: str,
    profile: _SquadProfile,
    *,
    baseline_score: float,
    max_rating_delta: float,
) -> SquadAdjustment:
    confidence = max(0.25, profile.confidence)
    rating_delta = _clamp((profile.squad_score - baseline_score) * 6.0 * confidence, -max_rating_delta, max_rating_delta)
    attack_delta = _clamp((profile.attack_score - baseline_score) * 0.010 * confidence, -0.16, 0.16)
    defense_delta = _clamp((profile.defense_score - baseline_score) * 0.010 * confidence, -0.16, 0.16)
    tempo_delta = _clamp((profile.tempo_score - baseline_score) * 0.006 * confidence, -0.09, 0.09)
    return SquadAdjustment(
        team_code=team_code,
        player_count=profile.player_count,
        squad_score=profile.squad_score,
        attack_score=profile.attack_score,
        defense_score=profile.defense_score,
        tempo_score=profile.tempo_score,
        baseline_score=baseline_score,
        confidence=profile.confidence,
        rating_delta=rating_delta,
        attack_delta=attack_delta,
        defense_delta=defense_delta,
        discipline_delta=0.0,
        tempo_delta=tempo_delta,
    )


def _position_score(
    players: list[_EvaluatedPlayer],
    group_weights: dict[str, float],
    *,
    fallback: float,
) -> float:
    scores: list[tuple[float, float]] = []
    for group, group_weight in group_weights.items():
        candidates = [player for player in players if player.player.position_group == group]
        if candidates:
            scores.append((_weighted_average(candidates), group_weight))
    if not scores:
        return fallback
    total_weight = sum(weight for _, weight in scores)
    return sum(score * weight for score, weight in scores) / total_weight


def _weighted_average(players: list[_EvaluatedPlayer]) -> float:
    if not players:
        return NEUTRAL_SQUAD_SCORE
    total_weight = sum(max(0.05, player.involvement) for player in players)
    return sum(player.rating * max(0.05, player.involvement) for player in players) / total_weight


def _require_columns(path: Path, fieldnames: list[str] | None, required: list[str]) -> None:
    missing = [column for column in required if column not in (fieldnames or [])]
    if missing:
        raise ValueError(f"{path} is missing required columns: {', '.join(missing)}")


def _optional_float(value: str | None, *, default: float | None = None) -> float | None:
    if value is None or value.strip() == "":
        return default
    return float(value)


def _optional_ratio(value: str | None, *, default: float | None = None) -> float | None:
    parsed = _optional_float(value, default=default)
    if parsed is None:
        return None
    if parsed > 1.0:
        parsed = parsed / 100.0
    return _clamp(parsed, 0.0, 1.0)


def _optional_money(value: str | None) -> float | None:
    if value is None or value.strip() == "":
        return None
    cleaned = value.strip().lower().replace(",", "").replace("_", "")
    cleaned = cleaned.replace("eur", "").replace("usd", "").replace("gbp", "")
    cleaned = cleaned.replace("$", "").replace("\N{EURO SIGN}", "").replace("\N{POUND SIGN}", "")
    multiplier = 1.0
    if cleaned.endswith("m"):
        multiplier = 1_000_000.0
        cleaned = cleaned[:-1]
    elif cleaned.endswith("k"):
        multiplier = 1_000.0
        cleaned = cleaned[:-1]
    return float(cleaned) * multiplier


def _clamp(value: float, lower: float, upper: float) -> float:
    return min(upper, max(lower, value))
