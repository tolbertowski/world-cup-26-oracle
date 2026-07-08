from datetime import datetime, timezone
from pathlib import Path

from world_cup_oracle.data import build_demo_fixtures, build_demo_teams
from world_cup_oracle.domain import MatchResult, MatchStage, MethodOfWin
from world_cup_oracle.models import MatchPredictor
from world_cup_oracle.snapshots import (
    SNAPSHOT_PREFIX,
    build_prediction_snapshot,
    load_snapshots,
    write_snapshot,
)


def _context():
    teams = build_demo_teams()
    fixtures = build_demo_fixtures()
    return teams, fixtures, MatchPredictor.from_teams(teams)


def test_snapshot_captures_odds_bracket_and_remaining_matches() -> None:
    teams, fixtures, predictor = _context()
    moment = datetime(2026, 7, 8, 8, 0, tzinfo=timezone.utc)

    snapshot = build_prediction_snapshot(
        teams, fixtures, predictor, {}, simulations=50, seed=1, generated_at=moment
    )

    assert snapshot["generated_at"] == "2026-07-08T08:00:00Z"
    assert snapshot["champion_probs"] and abs(sum(snapshot["champion_probs"].values()) - 1.0) < 1e-6
    assert snapshot["bracket"]["champion"]
    # Every unplayed group fixture with known teams is predicted.
    remaining = snapshot["remaining_matches"]
    assert len(remaining) == len(fixtures)
    row = remaining[0]
    assert abs(row["home_win"] + row["draw"] + row["away_win"] - 1.0) < 1e-6


def test_snapshot_reflects_locked_results() -> None:
    teams, fixtures, predictor = _context()
    first = fixtures[0]
    locked = {
        first.match_id: MatchResult(
            first.match_id, 1, 0, method=MethodOfWin.REGULATION, stage=MatchStage.GROUP,
            home_team=first.home_team, away_team=first.away_team,
        )
    }
    snapshot = build_prediction_snapshot(teams, fixtures, predictor, locked, simulations=20, seed=1)
    remaining_ids = {row["match_id"] for row in snapshot["remaining_matches"]}
    assert first.match_id not in remaining_ids  # played match is excluded


def test_write_and_load_snapshots_round_trip(tmp_path: Path) -> None:
    teams, fixtures, predictor = _context()
    for hour in (8, 14):
        moment = datetime(2026, 7, 8, hour, 0, tzinfo=timezone.utc)
        snap = build_prediction_snapshot(
            teams, fixtures, predictor, {}, simulations=20, seed=1, generated_at=moment
        )
        path = write_snapshot(snap, tmp_path)
        assert path.name.startswith(SNAPSHOT_PREFIX)

    assert (tmp_path / "latest.json").exists()
    loaded = load_snapshots(tmp_path)
    # Two immutable snapshots, chronologically ordered; latest.json not double-counted.
    assert [snap["generated_at"] for snap in loaded] == [
        "2026-07-08T08:00:00Z",
        "2026-07-08T14:00:00Z",
    ]


def test_cli_snapshot_predictions_writes_file(capsys, tmp_path: Path) -> None:
    from world_cup_oracle.cli import main

    assert main(["snapshot-predictions", "--simulations", "20", "--output-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "snapshot=" in out and "champion_leader=" in out
    assert list(tmp_path.glob(f"{SNAPSHOT_PREFIX}*.json"))
    assert (tmp_path / "latest.json").exists()
