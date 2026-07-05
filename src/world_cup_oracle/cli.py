"""Command line helpers for data refreshes, training, and simulations."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from world_cup_oracle.data import build_demo_fixtures, build_demo_teams, load_processed_or_demo
from world_cup_oracle.data.io import (
    GENERATED_RATINGS_PREFIX,
    apply_team_adjustments,
    cache_url,
    read_match_updates,
    read_model_params,
    read_team_adjustments,
    upsert_generated_team_adjustments,
    write_manual_templates,
    write_model_params,
)
from world_cup_oracle.data.pipeline import (
    import_tournament_snapshot,
    read_fixtures_csv,
    read_teams_csv,
    release_check,
    update_seed_ratings,
    validate_tournament_data,
    write_source_templates,
)
from world_cup_oracle.data.historical import (
    DEFAULT_HALF_LIFE_DAYS,
    RESULTS_URL,
    WORLD_CUP_2026_START,
    fit_average_goals,
    fit_team_ratings,
    read_results,
)
from world_cup_oracle.data.fifa_official import (
    FIFA_WORLD_CUP_2026_SEASON_ID,
    sync_fifa_calendar,
)
from world_cup_oracle.data.player_callups import (
    DEFAULT_MAX_RATING_DELTA,
    build_player_callup_adjustments,
    read_player_callups,
)
from world_cup_oracle.models import DEFAULT_AVERAGE_TOTAL_GOALS, MatchPredictor, apply_results_to_ratings
from world_cup_oracle.simulation import project_bracket, run_monte_carlo


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODEL_PARAMS_PATH = PROJECT_ROOT / "data" / "processed" / "model_params.json"


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

    player_callups = subparsers.add_parser(
        "apply-player-callups",
        help="Generate team adjustment deltas from reviewed player call-up CSVs.",
    )
    player_callups.add_argument("--callups", type=Path, default=PROJECT_ROOT / "data" / "manual" / "player_callups.csv")
    player_callups.add_argument("--teams", type=Path, default=PROJECT_ROOT / "data" / "processed" / "teams.csv")
    player_callups.add_argument("--output", type=Path, default=PROJECT_ROOT / "data" / "manual" / "team_adjustments.csv")
    player_callups.add_argument("--baseline-score", type=float, help="Optional neutral squad score on a 0-100 scale.")
    player_callups.add_argument("--max-rating-delta", type=float, default=DEFAULT_MAX_RATING_DELTA)
    player_callups.add_argument("--dry-run", action="store_true", help="Print generated deltas without writing them.")

    fit_ratings = subparsers.add_parser(
        "fit-ratings",
        help="Fit team Elo ratings and attack/defense from historical international results.",
    )
    fit_ratings.add_argument("--results", type=Path, default=PROJECT_ROOT / "data" / "cache" / "international_results.csv")
    fit_ratings.add_argument("--teams", type=Path, default=PROJECT_ROOT / "data" / "processed" / "teams.csv")
    fit_ratings.add_argument("--output", type=Path, default=PROJECT_ROOT / "data" / "manual" / "team_adjustments.csv")
    fit_ratings.add_argument("--half-life-days", type=float, default=DEFAULT_HALF_LIFE_DAYS)
    fit_ratings.add_argument(
        "--cutoff",
        type=date.fromisoformat,
        default=WORLD_CUP_2026_START,
        help="Drop matches on/after this date so live-tournament games are never double-counted.",
    )
    fit_ratings.add_argument(
        "--no-seed-rating",
        action="store_true",
        help="Skip writing fitted Elo into teams.csv seed_rating.",
    )
    fit_ratings.add_argument("--dry-run", action="store_true", help="Print fitted ratings without writing them.")

    sync_fifa = subparsers.add_parser("sync-fifa", help="Sync official FIFA World Cup 2026 calendar data.")
    sync_fifa.add_argument("--season-id", default=FIFA_WORLD_CUP_2026_SEASON_ID)
    sync_fifa.add_argument("--language", default="en")
    sync_fifa.add_argument("--source-json", type=Path, help="Use a cached FIFA calendar JSON file instead of fetching.")
    sync_fifa.add_argument("--apply", action="store_true", help="Write raw/processed files and official completed results.")
    sync_fifa.add_argument("--no-results", action="store_true", help="Do not merge completed FIFA results into manual updates.")
    sync_fifa.add_argument("--no-strict", action="store_true", help="Allow partial snapshots.")

    subparsers.add_parser(
        "project-bracket",
        help="Print the deterministic most-likely knockout bracket from processed data.",
    )

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
    if args.command == "apply-player-callups":
        callups = read_player_callups(args.callups)
        if not callups:
            print(f"No player callups found in {args.callups}.")
            return 0 if args.dry_run else 1
        teams = read_teams_csv(args.teams) if args.teams.exists() else []
        try:
            adjustments = build_player_callup_adjustments(
                callups,
                team_codes={team.code for team in teams} if teams else None,
                baseline_score=args.baseline_score,
                max_rating_delta=args.max_rating_delta,
            )
        except ValueError as exc:
            print(exc)
            return 1
        rows = [adjustment.as_adjustment_row() for adjustment in adjustments]
        if not args.dry_run:
            upsert_generated_team_adjustments(args.output, rows)
        print("team_code,rating_delta,attack_delta,defense_delta,tempo_delta,squad_score,players")
        for adjustment in adjustments:
            print(
                f"{adjustment.team_code},"
                f"{adjustment.rating_delta:.1f},"
                f"{adjustment.attack_delta:.3f},"
                f"{adjustment.defense_delta:.3f},"
                f"{adjustment.tempo_delta:.3f},"
                f"{adjustment.squad_score:.1f},"
                f"{adjustment.player_count}"
            )
        if not args.dry_run:
            print(f"updated={args.output}")
        return 0
    if args.command == "fit-ratings":
        if not args.results.exists():
            print(f"No results snapshot at {args.results}.")
            print(f'Fetch it first: world-cup-oracle cache-url "{RESULTS_URL}" --name international_results.csv')
            return 1
        if not args.teams.exists():
            print(f"No teams file at {args.teams}; run init-data or sync-fifa first.")
            return 1
        records = [record for record in read_results(args.results) if record.match_date < args.cutoff]
        teams = read_teams_csv(args.teams)
        fitted, unmatched = fit_team_ratings(records, teams, half_life_days=args.half_life_days)
        if not fitted:
            print("No teams could be matched to the results dataset.")
            return 1
        rows = [item.as_adjustment_row(note_prefix=GENERATED_RATINGS_PREFIX) for item in fitted]
        average_goals = fit_average_goals(records, half_life_days=args.half_life_days)
        if not args.dry_run:
            upsert_generated_team_adjustments(args.output, rows)
            if not args.no_seed_rating:
                update_seed_ratings(args.teams, {item.team_code: item.elo for item in fitted})
            params_path = write_model_params(
                MODEL_PARAMS_PATH,
                {
                    "average_total_goals": round(average_goals, 3),
                    "fitted_at": date.today().isoformat(),
                    "source": "international_results",
                },
            )
        print(f"average_total_goals={average_goals:.3f}")
        print("team_code,elo,attack,defense,attack_delta,defense_delta,matches")
        for item in sorted(fitted, key=lambda value: value.elo, reverse=True):
            print(
                f"{item.team_code},"
                f"{item.elo:.0f},"
                f"{item.attack:.2f},"
                f"{item.defense:.2f},"
                f"{item.attack_delta:.3f},"
                f"{item.defense_delta:.3f},"
                f"{item.matches}"
            )
        if unmatched:
            print(f"unmatched={','.join(unmatched)}")
        if not args.dry_run:
            print(f"updated={args.output}")
            print(f"model_params={params_path}")
            if not args.no_seed_rating:
                print(f"seed_rating_updated={args.teams}")
        return 0
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
    if args.command == "project-bracket":
        tournament = load_processed_or_demo(PROJECT_ROOT / "data" / "processed")
        adjustments = read_team_adjustments(PROJECT_ROOT / "data" / "manual" / "team_adjustments.csv")
        locked = read_match_updates(PROJECT_ROOT / "data" / "manual" / "match_updates.csv")
        params = read_model_params(MODEL_PARAMS_PATH)
        ratings = apply_team_adjustments(MatchPredictor.from_teams(tournament.teams).ratings, adjustments)
        ratings = apply_results_to_ratings(ratings, tournament.fixtures, locked)
        predictor = MatchPredictor(
            ratings,
            average_total_goals=params.get("average_total_goals", DEFAULT_AVERAGE_TOTAL_GOALS),
        )
        bracket = project_bracket(tournament.teams, tournament.fixtures, predictor, locked)
        names = {team.code: team.name for team in tournament.teams}
        for stage, matches in bracket.rounds:
            print(stage.value)
            for match in matches:
                winner = names.get(match.projected_winner, match.projected_winner)
                tag = "locked" if match.source == "locked" else f"{match.advance_prob:.0%}"
                home = names.get(match.home_team, match.home_team)
                away = names.get(match.away_team, match.away_team)
                print(f"  {home} vs {away} -> {winner} ({tag})")
        print(f"champion={names.get(bracket.champion, bracket.champion)}")
        return 0
    if args.command == "release-check":
        report = release_check(PROJECT_ROOT / "data" / "processed")
        print(report.render())
        return 0 if report.ok else 1
    parser.print_help()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
