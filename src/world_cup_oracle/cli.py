"""Command line helpers for data refreshes, training, and simulations."""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="world-cup-oracle")
    parser.add_argument(
        "--version",
        action="store_true",
        help="Show package version and exit.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    from world_cup_oracle import __version__

    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        print(__version__)
        return 0
    parser.print_help()
    return 0
