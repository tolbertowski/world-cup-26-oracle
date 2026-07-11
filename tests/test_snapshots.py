from datetime import datetime, timezone
from pathlib import Path

from world_cup_oracle.data import build_demo_fixtures, build_demo_teams
from world_cup_oracle.domain import Fixture, MatchResult, MatchStage, MethodOfWin
from world_cup_oracle.models import MatchPredictor
from world_cup_oracle.snapshots import (
    SNAPSHOT_PREFIX,
    build_prediction_snapshot,
    fixtures_as_known,
    load_snapshots,
    results_as_of,
    snapshot_dates,
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


def test_results_as_of_filters_by_kickoff() -> None:
    fixtures = [
        Fixture("EARLY", MatchStage.GROUP, "BRA", "MEX", kickoff="2026-06-12T19:00:00Z"),
        Fixture("LATE", MatchStage.GROUP, "KOR", "CZE", kickoff="2026-06-20T19:00:00Z"),
    ]
    results = {
        "EARLY": MatchResult("EARLY", 1, 0),
        "LATE": MatchResult("LATE", 2, 2),
        "NO_FIXTURE": MatchResult("NO_FIXTURE", 3, 0),
    }
    cutoff = datetime(2026, 6, 15, 23, 59, tzinfo=timezone.utc)

    kept = results_as_of(fixtures, results, cutoff)

    assert set(kept) == {"EARLY"}  # later kickoff and unknown-date results excluded


def test_fixtures_as_known_blanks_undecided_knockouts() -> None:
    group = [
        Fixture(f"G{i}", MatchStage.GROUP, "AAA", "BBB", group="A", kickoff="2026-06-12T19:00:00Z")
        for i in range(3)  # G2 stays unlocked, so the group stage is incomplete
    ]
    decided = Fixture(
        "K1", MatchStage.ROUND_OF_32, "BRA", "MEX",
        home_source="W:G0", away_source="W:G1", neutral_site=False,
    )
    undecided = Fixture(
        "K2", MatchStage.ROUND_OF_16, "BRA", "ENG",
        home_source="W:K1", away_source="W:K9", neutral_site=False,
    )
    seeded = Fixture("K3", MatchStage.ROUND_OF_32, "USA", "AUS", home_source="1A", away_source="2B")

    # Group not complete: only fully-referenced fixtures keep their teams.
    rewound = {f.match_id: f for f in fixtures_as_known([*group, decided, undecided, seeded], {"G0", "G1"})}
    assert rewound["K1"].home_team == "BRA" and rewound["K1"].neutral_site is False
    assert rewound["K2"].home_team == "" and rewound["K2"].neutral_site is True
    assert rewound["K2"].home_source == "W:K1"  # sources survive for the resolver
    assert rewound["K3"].home_team == ""  # seed sources need the full group stage
    assert rewound["G0"].home_team == "AAA"  # group fixtures untouched


def test_backfilled_snapshot_flag_and_latest_protection(tmp_path: Path) -> None:
    teams, fixtures, predictor = _context()
    live = build_prediction_snapshot(
        teams, fixtures, predictor, {}, simulations=20, seed=1,
        generated_at=datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc),
    )
    write_snapshot(live, tmp_path)
    old = build_prediction_snapshot(
        teams, fixtures, predictor, {}, simulations=20, seed=1,
        generated_at=datetime(2026, 6, 15, 23, 59, tzinfo=timezone.utc), backfilled=True,
    )
    write_snapshot(old, tmp_path, update_latest=False)

    assert old["backfilled"] is True and live["backfilled"] is False
    import json

    latest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    assert latest["generated_at"] == "2026-07-08T12:00:00Z"  # backfill did not clobber
    assert snapshot_dates(tmp_path) == {"2026-07-08", "2026-06-15"}


def test_cli_backfill_reconstructs_missing_days_and_skips_existing(capsys, tmp_path: Path) -> None:
    from world_cup_oracle.cli import main

    args = [
        "backfill-snapshots",
        "--start", "2026-06-11",
        "--end", "2026-06-12",
        "--simulations", "10",
        "--output-dir", str(tmp_path),
    ]
    assert main(args) == 0
    assert "backfilled=2" in capsys.readouterr().out
    files = sorted(tmp_path.glob(f"{SNAPSHOT_PREFIX}*.json"))
    assert len(files) == 2
    assert not (tmp_path / "latest.json").exists()  # backfill never writes latest

    # Second run is a no-op: those days already have snapshots.
    assert main(args) == 0
    assert "backfilled=0" in capsys.readouterr().out


def test_cli_snapshot_daily_skips_same_day(capsys, tmp_path: Path) -> None:
    from world_cup_oracle.cli import main

    base = ["snapshot-predictions", "--simulations", "10", "--output-dir", str(tmp_path), "--daily"]
    assert main(base) == 0
    assert len(list(tmp_path.glob(f"{SNAPSHOT_PREFIX}*.json"))) == 1
    assert main(base) == 0
    assert "skipping" in capsys.readouterr().out
    assert len(list(tmp_path.glob(f"{SNAPSHOT_PREFIX}*.json"))) == 1


def test_cli_snapshot_predictions_writes_file(capsys, tmp_path: Path) -> None:
    from world_cup_oracle.cli import main

    assert main(["snapshot-predictions", "--simulations", "20", "--output-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "snapshot=" in out and "champion_leader=" in out
    assert list(tmp_path.glob(f"{SNAPSHOT_PREFIX}*.json"))
    assert (tmp_path / "latest.json").exists()
