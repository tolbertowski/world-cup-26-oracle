"""Command line helpers for data refreshes, training, and simulations."""

from __future__ import annotations

import argparse
from pathlib import Path

from world_cup_oracle.data import build_demo_fixtures, build_demo_teams
from world_cup_oracle.data.io import cache_url, write_manual_templates
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
    subparsers.add_parser("init-data", help="Create manual CSV templates.")

    refresh = subparsers.add_parser("cache-url", help="Cache a public free-data URL.")
    refresh.add_argument("url")
    refresh.add_argument("--name", help="Optional local filename.")

    simulate = subparsers.add_parser("simulate-demo", help="Run demo Monte Carlo predictions.")
    simulate.add_argument("--simulations", type=int, default=500)
    simulate.add_argument("--seed", type=int, default=26)
    return parser


def main(argv: list[str] | None = None) -> int:
    from world_cup_oracle import __version__

    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        print(__version__)
        return 0
    if args.command == "init-data":
        paths = write_manual_templates(PROJECT_ROOT / "data" / "manual")
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
    parser.print_help()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
