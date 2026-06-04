"""Typed domain objects shared by data, models, simulation, and UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class MatchStage(StrEnum):
    GROUP = "group"
    ROUND_OF_32 = "round_of_32"
    ROUND_OF_16 = "round_of_16"
    QUARTER_FINAL = "quarter_final"
    SEMI_FINAL = "semi_final"
    THIRD_PLACE = "third_place"
    FINAL = "final"


class MethodOfWin(StrEnum):
    DRAW = "draw"
    REGULATION = "regulation"
    EXTRA_TIME = "extra_time"
    PENALTIES = "penalties"


@dataclass(frozen=True, slots=True)
class Team:
    code: str
    name: str
    group: str | None = None
    confederation: str | None = None
    fifa_rank: int | None = None
    seed_rating: float = 1500.0


@dataclass(frozen=True, slots=True)
class Fixture:
    match_id: str
    stage: MatchStage
    home_team: str
    away_team: str
    group: str | None = None
    kickoff: str | None = None
    venue: str | None = None
    neutral_site: bool = True

    @property
    def is_knockout(self) -> bool:
        return self.stage != MatchStage.GROUP


@dataclass(frozen=True, slots=True)
class MatchResult:
    match_id: str
    home_goals: int
    away_goals: int
    home_penalties: int | None = None
    away_penalties: int | None = None
    home_yellow_cards: int | None = None
    away_yellow_cards: int | None = None
    home_red_cards: int | None = None
    away_red_cards: int | None = None
    home_corners: int | None = None
    away_corners: int | None = None
    method: MethodOfWin | None = None
    locked: bool = True
    notes: str | None = None

    @property
    def is_draw_after_play(self) -> bool:
        return self.home_goals == self.away_goals

    @property
    def winner_side(self) -> str | None:
        if self.home_goals > self.away_goals:
            return "home"
        if self.away_goals > self.home_goals:
            return "away"
        if self.home_penalties is None or self.away_penalties is None:
            return None
        if self.home_penalties > self.away_penalties:
            return "home"
        if self.away_penalties > self.home_penalties:
            return "away"
        return None


@dataclass(frozen=True, slots=True)
class TeamRating:
    team_code: str
    rating: float = 1500.0
    attack: float = 1.0
    defense: float = 1.0
    discipline: float = 1.0
    tempo: float = 1.0
    recent_form: float = 0.0


@dataclass(frozen=True, slots=True)
class StandingRow:
    team_code: str
    group: str
    played: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    goals_for: int = 0
    goals_against: int = 0
    points: int = 0
    fair_play_points: int = 0
    seed_rating: float = 1500.0

    @property
    def goal_difference(self) -> int:
        return self.goals_for - self.goals_against

    def record_match(
        self,
        goals_for: int,
        goals_against: int,
        fair_play_delta: int = 0,
    ) -> "StandingRow":
        wins = self.wins + int(goals_for > goals_against)
        draws = self.draws + int(goals_for == goals_against)
        losses = self.losses + int(goals_for < goals_against)
        points = self.points + (3 if goals_for > goals_against else 1 if goals_for == goals_against else 0)
        return StandingRow(
            team_code=self.team_code,
            group=self.group,
            played=self.played + 1,
            wins=wins,
            draws=draws,
            losses=losses,
            goals_for=self.goals_for + goals_for,
            goals_against=self.goals_against + goals_against,
            points=points,
            fair_play_points=self.fair_play_points + fair_play_delta,
            seed_rating=self.seed_rating,
        )


@dataclass(frozen=True, slots=True)
class QualifiedTeam:
    team_code: str
    group: str
    rank: int
    standing: StandingRow

    @property
    def seed_label(self) -> str:
        return f"{self.rank}{self.group}"


@dataclass(frozen=True, slots=True)
class MatchPrediction:
    fixture: Fixture
    home_win: float
    draw: float
    away_win: float
    expected_home_goals: float
    expected_away_goals: float
    expected_home_corners: float
    expected_away_corners: float
    expected_home_cards: float
    expected_away_cards: float
    method_probs: dict[MethodOfWin, float] = field(default_factory=dict)
    scoreline_probs: dict[tuple[int, int], float] = field(default_factory=dict)
    explanation: list[str] = field(default_factory=list)

    @property
    def total_goals(self) -> float:
        return self.expected_home_goals + self.expected_away_goals


@dataclass(frozen=True, slots=True)
class SimulationSummary:
    simulations: int
    champion_probs: dict[str, float]
    finalist_probs: dict[str, float]
    group_winner_probs: dict[str, dict[str, float]]
    knockout_probs: dict[str, float]
    upset_probs: dict[str, float]


@dataclass(frozen=True, slots=True)
class BracketMatch:
    stage: MatchStage
    match_id: str
    home_team: str
    away_team: str
    projected_winner: str
    advance_prob: float
    source: str  # "locked" when taken from a locked result, else "expected"


@dataclass(frozen=True, slots=True)
class BracketProjection:
    rounds: list[tuple[MatchStage, list[BracketMatch]]]
    champion: str
    third_place: str | None = None
