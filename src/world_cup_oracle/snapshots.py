"""Point-in-time snapshots of the model's predictions (audit trail).

Each snapshot records what the model predicted at a moment in time — champion
and finalist probabilities, the projected bracket, and win/draw/loss + expected
goals for every currently-playable fixture — so predictions can be reviewed and
scored after the fact. Snapshots are immutable, timestamped JSON files; the git
history of ``data/snapshots/`` is the audit trail.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from world_cup_oracle.domain import Fixture, MatchResult, MatchStage, Team
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
    backfilled: bool = False,
) -> dict:
    """Assemble a deterministic prediction snapshot dict (seeded Monte Carlo)."""
    moment = (generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    summary = run_monte_carlo(
        teams, fixtures, predictor, simulations=simulations, seed=seed, locked_results=locked_results
    )
    bracket = project_bracket(teams, fixtures, predictor, locked_results)

    return {
        "generated_at": moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "backfilled": backfilled,
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


def write_snapshot(snapshot: dict, snapshot_dir: Path, *, update_latest: bool = True) -> Path:
    """Write a timestamped snapshot (and by default refresh ``latest.json``).

    Backfilled snapshots pass ``update_latest=False`` so reconstructing history
    never masks the genuinely newest prediction.
    """
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    stamp = snapshot["generated_at"].replace(":", "").replace("-", "")
    path = snapshot_dir / f"{SNAPSHOT_PREFIX}{stamp}.json"
    payload = json.dumps(snapshot, indent=2, sort_keys=True) + "\n"
    path.write_text(payload, encoding="utf-8")
    if update_latest:
        (snapshot_dir / LATEST_NAME).write_text(payload, encoding="utf-8")
    return path


def snapshot_dates(snapshot_dir: Path) -> set[str]:
    """UTC dates (YYYY-MM-DD) that already have a snapshot."""
    return {
        snap["generated_at"][:10]
        for snap in load_snapshots(snapshot_dir)
        if snap.get("generated_at")
    }


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


def results_as_of(
    fixtures: list[Fixture],
    results: dict[str, MatchResult],
    cutoff: datetime,
    *,
    inclusive: bool = True,
) -> dict[str, MatchResult]:
    """Locked results whose fixture kicked off before ``cutoff``.

    This is what makes honest reconstruction possible: every fixture carries its
    official kickoff timestamp, so the set of results known at any moment can be
    rebuilt exactly. ``inclusive`` keeps kickoffs at the cutoff (end-of-day
    snapshots); a strict cutoff (``inclusive=False``) is used to score a match
    on only what was known *before* it kicked off, excluding itself and
    simultaneous games.
    """
    kickoff_by_id = {fixture.match_id: fixture.kickoff for fixture in fixtures}
    cutoff = cutoff.astimezone(timezone.utc)
    kept: dict[str, MatchResult] = {}
    for match_id, result in results.items():
        kickoff = _parse_kickoff(kickoff_by_id.get(match_id))
        if kickoff is None:
            continue
        if (kickoff <= cutoff) if inclusive else (kickoff < cutoff):
            kept[match_id] = result
    return kept


def fixtures_as_known(fixtures: list[Fixture], locked_ids: set[str]) -> list[Fixture]:
    """Rewind knockout fixtures to what was known given ``locked_ids``.

    The official calendar bakes real team codes (and host-advantage flags) into
    knockout fixtures once pairings are decided. For a backfill date before that
    decision, keeping them would leak the future — so a knockout fixture keeps
    its teams only if every bracket source it references had been decided by
    then; otherwise teams are blanked (and the venue treated as neutral) and the
    bracket resolver re-derives pairings from the results locked at the time.
    """
    group_ids = {fixture.match_id for fixture in fixtures if fixture.stage == MatchStage.GROUP}
    group_complete = group_ids <= locked_ids
    rewound: list[Fixture] = []
    for fixture in fixtures:
        if fixture.stage == MatchStage.GROUP or _sources_decided(fixture, locked_ids, group_complete):
            rewound.append(fixture)
        else:
            rewound.append(replace(fixture, home_team="", away_team="", neutral_site=True))
    return rewound


def _sources_decided(fixture: Fixture, locked_ids: set[str], group_complete: bool) -> bool:
    for source in (fixture.home_source, fixture.away_source):
        if not source:
            continue
        if source.startswith("W:") or source.startswith("RU:"):
            if source.split(":", 1)[1] not in locked_ids:
                return False
        elif not group_complete:  # seed labels such as 1A / 3ABCDF
            return False
    return True


def _parse_kickoff(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


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
