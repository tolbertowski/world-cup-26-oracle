"""Command line helpers for data refreshes, training, and simulations."""

from __future__ import annotations

import argparse
from pathlib import Path

from world_cup_oracle.data import build_demo_fixtures, build_demo_teams
from world_cup_oracle.data.io import cache_url, write_manual_templates
from world_cup_oracle.data.pipeline import (
    import_tournament_snapshot,
    read_fixtures_csv,
    read_teams_csv,
    release_check,
    validate_tournament_data,
    write_source_templates,
)
from world_cup_oracle.data.fifa_official import (
    FIFA_WORLD_CUP_2026_SEASON_ID,
    sync_fifa_calendar,
)
from world_cup_oracle.models import MatchPredictor
from world_cup_oracle.simulation import run_monte_carlo


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="world-cup-oracle")
    parser.add_argument(
        "--version",
        action="store_true",
        help="Show package version and exit.",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("init-data", help="Create manual and source CSV templates.")

    refresh = subparsers.add_parser("cache-url", help="Cache a public free-data URL.")
    refresh.add_argument("url")
    refresh.add_argument("--name", help="Optional local filename.")

    simulate = subparsers.add_parser("simulate-demo", help="Run demo Monte Carlo predictions.")
    simulate.add_argument("--simulations", type=int, default=500)
    simulate.add_argument("--seed", type=int, default=26)

    validate = subparsers.add_parser("validate-snapshot", help="Validate raw tournament CSVs.")
    validate.add_argument("--teams", type=Path, required=True)
    validate.add_argument("--fixtures", type=Path, required=True)
    validate.add_argument("--strict", action="store_true", help="Require a full 48-team, 72-group-fixture snapshot.")

    import_snapshot = subparsers.add_parser("import-snapshot", help="Validate and export raw CSVs into data/processed.")
    import_snapshot.add_argument("--teams", type=Path, required=True)
    import_snapshot.add_argument("--fixtures", type=Path, required=True)
    import_snapshot.add_argument("--strict", action="store_true", help="Require a full 48-team, 72-group-fixture snapshot.")

    sync_fifa = subparsers.add_parser("sync-fifa", help="Sync official FIFA World Cup 2026 calendar data.")
    sync_fifa.add_argument("--season-id", default=FIFA_WORLD_CUP_2026_SEASON_ID)
    sync_fifa.add_argument("--language", default="en")
    sync_fifa.add_argument("--source-json", type=Path, help="Use a cached FIFA calendar JSON file instead of fetching.")
    sync_fifa.add_argument("--apply", action="store_true", help="Write raw/processed files and official completed results.")
    sync_fifa.add_argument("--no-results", action="store_true", help="Do not merge completed FIFA results into manual updates.")
    sync_fifa.add_argument("--no-strict", action="store_true", help="Allow partial snapshots.")

    subparsers.add_parser("release-check", help="Fail if the app would still use demo data.")
    return parser


def main(argv: list[str] | None = None) -> int:
    from world_cup_oracle import __version__

    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        print(__version__)
        return 0
    if args.command == "init-data":
        paths = [
            *write_manual_templates(PROJECT_ROOT / "data" / "manual"),
            *write_source_templates(PROJECT_ROOT / "data" / "raw"),
        ]
        for path in paths:
            print(path)
        return 0
    if args.command == "cache-url":
        path = cache_url(args.url, PROJECT_ROOT / "data" / "cache", args.name)
        print(path)
        return 0
    if args.command == "simulate-demo":
        teams = build_demo_teams()
        summary = run_monte_carlo(
            teams,
            build_demo_fixtures(),
            MatchPredictor.from_teams(teams),
            simulations=args.simulations,
            seed=args.seed,
        )
        for team, probability in list(summary.champion_probs.items())[:10]:
            print(f"{team},{probability:.4f}")
        return 0
    if args.command == "validate-snapshot":
        report = validate_tournament_data(
            read_teams_csv(args.teams),
            read_fixtures_csv(args.fixtures),
            strict=args.strict,
        )
        print(report.render())
        return 0 if report.ok else 1
    if args.command == "import-snapshot":
        report = import_tournament_snapshot(
            args.teams,
            args.fixtures,
            PROJECT_ROOT / "data" / "processed",
            strict=args.strict,
        )
        print(report.render())
        return 0 if report.ok else 1
    if args.command == "sync-fifa":
        result = sync_fifa_calendar(
            raw_dir=PROJECT_ROOT / "data" / "raw",
            cache_dir=PROJECT_ROOT / "data" / "cache" / "fifa",
            processed_dir=PROJECT_ROOT / "data" / "processed",
            manual_dir=PROJECT_ROOT / "data" / "manual",
            source_json=args.source_json,
            apply=args.apply,
            update_results=not args.no_results,
            strict=not args.no_strict,
            season_id=args.season_id,
            language=args.language,
        )
        print(result.report.render())
        print(f"teams={len(result.teams)} fixtures={len(result.fixtures)} completed_results={len(result.completed_results)}")
        if result.cache_path:
            print(f"cache={result.cache_path}")
        if result.raw_paths:
            print(f"raw={result.raw_paths[0]},{result.raw_paths[1]}")
        if result.processed_paths:
            print(f"processed={result.processed_paths[0]},{result.processed_paths[1]}")
        if result.updates_path:
            print(f"updates={result.updates_path}")
        return 0 if result.ok else 1
    if args.command == "release-check":
        report = release_check(PROJECT_ROOT / "data" / "processed")
        print(report.render())
        return 0 if report.ok else 1
    parser.print_help()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
