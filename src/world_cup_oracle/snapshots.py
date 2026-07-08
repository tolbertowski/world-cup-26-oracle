"""Point-in-time snapshots of the model's predictions (audit trail).

Each snapshot records what the model predicted at a moment in time — champion
and finalist probabilities, the projected bracket, and win/draw/loss + expected
goals for every currently-playable fixture — so predictions can be reviewed and
scored after the fact. Snapshots are immutable, timestamped JSON files; the git
history of ``data/snapshots/`` is the audit trail.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from world_cup_oracle.domain import Fixture, MatchResult, Team
from world_cup_oracle.models import MatchPredictor
from world_cup_oracle.simulation import project_bracket, run_monte_carlo

SNAPSHOT_PREFIX = "prediction_"
LATEST_NAME = "latest.json"


def build_prediction_snapshot(
    teams: list[Team],
    fixtures: list[Fixture],
    predictor: MatchPredictor,
    locked_results: dict[str, MatchResult],
    *,
    simulations: int = 5000,
    seed: int = 26,
    data_source: str = "processed",
    generated_at: datetime | None = None,
) -> dict:
    """Assemble a deterministic prediction snapshot dict (seeded Monte Carlo)."""
    moment = (generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    summary = run_monte_carlo(
        teams, fixtures, predictor, simulations=simulations, seed=seed, locked_results=locked_results
    )
    bracket = project_bracket(teams, fixtures, predictor, locked_results)

    return {
        "generated_at": moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data_source": data_source,
        "simulations": simulations,
        "seed": seed,
        "champion_probs": _round_probs(summary.champion_probs),
        "finalist_probs": _round_probs(summary.finalist_probs),
        "bracket": {
            "champion": bracket.champion,
            "third_place": bracket.third_place,
            "rounds": [
                {
                    "stage": stage.value,
                    "matches": [
                        {
                            "match_id": match.match_id,
                            "home": match.home_team,
                            "away": match.away_team,
                            "projected_winner": match.projected_winner,
                            "advance_prob": round(match.advance_prob, 4),
                            "source": match.source,
                        }
                        for match in matches
                    ],
                }
                for stage, matches in bracket.rounds
            ],
        },
        "remaining_matches": _remaining_match_predictions(fixtures, predictor, locked_results),
    }


def write_snapshot(snapshot: dict, snapshot_dir: Path) -> Path:
    """Write a timestamped snapshot and refresh ``latest.json``; return its path."""
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    stamp = snapshot["generated_at"].replace(":", "").replace("-", "")
    path = snapshot_dir / f"{SNAPSHOT_PREFIX}{stamp}.json"
    payload = json.dumps(snapshot, indent=2, sort_keys=True) + "\n"
    path.write_text(payload, encoding="utf-8")
    (snapshot_dir / LATEST_NAME).write_text(payload, encoding="utf-8")
    return path


def load_snapshots(snapshot_dir: Path) -> list[dict]:
    """All snapshots in chronological order (excludes the latest.json alias)."""
    if not snapshot_dir.exists():
        return []
    snapshots: list[dict] = []
    for path in sorted(snapshot_dir.glob(f"{SNAPSHOT_PREFIX}*.json")):
        try:
            snapshots.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    snapshots.sort(key=lambda snap: snap.get("generated_at", ""))
    return snapshots


def _remaining_match_predictions(
    fixtures: list[Fixture],
    predictor: MatchPredictor,
    locked_results: dict[str, MatchResult],
) -> list[dict]:
    rows: list[dict] = []
    for fixture in fixtures:
        # Only fixtures whose teams are both known and which have not been
        # played yet — future rounds with TBD teams are covered by the bracket.
        if fixture.match_id in locked_results or not fixture.home_team or not fixture.away_team:
            continue
        if fixture.home_team not in predictor.ratings or fixture.away_team not in predictor.ratings:
            continue
        prediction = predictor.predict(fixture)
        rows.append(
            {
                "match_id": fixture.match_id,
                "stage": fixture.stage.value,
                "home": fixture.home_team,
                "away": fixture.away_team,
                "home_win": round(prediction.home_win, 4),
                "draw": round(prediction.draw, 4),
                "away_win": round(prediction.away_win, 4),
                "expected_home_goals": round(prediction.expected_home_goals, 2),
                "expected_away_goals": round(prediction.expected_away_goals, 2),
            }
        )
    return rows


def _round_probs(probs: dict[str, float]) -> dict[str, float]:
    return {code: round(prob, 4) for code, prob in probs.items()}
