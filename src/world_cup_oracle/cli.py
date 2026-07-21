"""Command line helpers for data refreshes, training, and simulations."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from world_cup_oracle.backtest import run_backtest
from world_cup_oracle.context import build_predictor, load_live_context, load_raw_inputs
from world_cup_oracle.data import build_demo_fixtures, build_demo_teams
from world_cup_oracle.data.io import (
    GENERATED_PLAYER_ADJUSTMENT_PREFIX,
    GENERATED_RATINGS_PREFIX,
    cache_url,
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
from world_cup_oracle.models import MatchPredictor
from world_cup_oracle.simulation import project_bracket, run_monte_carlo
from world_cup_oracle.snapshots import (
    build_prediction_snapshot,
    fixtures_as_known,
    results_as_of,
    snapshot_dates,
    write_snapshot,
)


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

    snapshot = subparsers.add_parser(
        "snapshot-predictions",
        help="Write a timestamped snapshot of current predictions (audit trail).",
    )
    snapshot.add_argument("--simulations", type=int, default=5000)
    snapshot.add_argument("--seed", type=int, default=26)
    snapshot.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "data" / "snapshots")
    snapshot.add_argument(
        "--daily",
        action="store_true",
        help="Skip (successfully) when a snapshot already exists for today's UTC date.",
    )

    backfill = subparsers.add_parser(
        "backfill-snapshots",
        help="Reconstruct one end-of-day snapshot per missing day (marked backfilled).",
    )
    backfill.add_argument("--start", type=date.fromisoformat, default=WORLD_CUP_2026_START)
    backfill.add_argument(
        "--end",
        type=date.fromisoformat,
        default=None,
        help="Last day to reconstruct (default: yesterday, UTC).",
    )
    backfill.add_argument("--simulations", type=int, default=5000)
    backfill.add_argument("--seed", type=int, default=26)
    backfill.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "data" / "snapshots")

    backtest = subparsers.add_parser(
        "backtest",
        help="Score genuine pre-kickoff predictions against played results.",
    )
    backtest.add_argument("--output", type=Path, default=PROJECT_ROOT / "data" / "backtest.json")
    backtest.add_argument("--dry-run", action="store_true", help="Print metrics without writing the report.")

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
            upsert_generated_team_adjustments(args.output, rows, note_prefix=GENERATED_PLAYER_ADJUSTMENT_PREFIX)
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
            upsert_generated_team_adjustments(args.output, rows, note_prefix=GENERATED_RATINGS_PREFIX)
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
        teams, fixtures, predictor, locked, _ = load_live_context(PROJECT_ROOT)
        bracket = project_bracket(teams, fixtures, predictor, locked)
        names = {team.code: team.name for team in teams}
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
    if args.command == "snapshot-predictions":
        if args.daily and datetime.now(timezone.utc).strftime("%Y-%m-%d") in snapshot_dates(args.output_dir):
            print("daily snapshot already recorded for today; skipping")
            return 0
        teams, fixtures, predictor, locked, source = load_live_context(PROJECT_ROOT)
        snapshot = build_prediction_snapshot(
            teams,
            fixtures,
            predictor,
            locked,
            simulations=args.simulations,
            seed=args.seed,
            data_source=source,
        )
        path = write_snapshot(snapshot, args.output_dir)
        leader, prob = next(iter(snapshot["champion_probs"].items()), ("n/a", 0.0))
        print(f"snapshot={path}")
        print(f"generated_at={snapshot['generated_at']} source={source}")
        print(f"champion_leader={leader} ({prob:.1%}); remaining_matches={len(snapshot['remaining_matches'])}")
        return 0
    if args.command == "backfill-snapshots":
        teams, fixtures, adjustments, locked, params, source = load_raw_inputs(PROJECT_ROOT)
        existing = snapshot_dates(args.output_dir)
        end = args.end or (datetime.now(timezone.utc).date() - timedelta(days=1))
        day = args.start
        written = 0
        while day <= end:
            if day.isoformat() in existing:
                day += timedelta(days=1)
                continue
            # End-of-day reconstruction: only results whose fixture kicked off
            # by 23:59 UTC that day, and only bracket knowledge derivable from
            # them — knockout pairings not yet decided are re-blanked so the
            # resolver rederives what was actually knowable at the time.
            cutoff = datetime(day.year, day.month, day.day, 23, 59, 0, tzinfo=timezone.utc)
            locked_then = results_as_of(fixtures, locked, cutoff)
            fixtures_then = fixtures_as_known(fixtures, set(locked_then))
            predictor = build_predictor(teams, fixtures_then, adjustments, locked_then, params)
            snapshot = build_prediction_snapshot(
                teams,
                fixtures_then,
                predictor,
                locked_then,
                simulations=args.simulations,
                seed=args.seed,
                data_source=source,
                generated_at=cutoff,
                backfilled=True,
            )
            write_snapshot(snapshot, args.output_dir, update_latest=False)
            leader, prob = next(iter(snapshot["champion_probs"].items()), ("n/a", 0.0))
            print(f"{day.isoformat()}: locked={len(locked_then)} leader={leader} ({prob:.1%})")
            written += 1
            day += timedelta(days=1)
        print(f"backfilled={written}")
        return 0
    if args.command == "backtest":
        report = run_backtest(PROJECT_ROOT)
        overall = report["overall"]
        skill = report["skill_vs_uniform"]
        if not args.dry_run:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"matches={overall['matches']} accuracy={overall['accuracy']:.1%}")
        print(f"rps={overall['rps']:.4f} brier={overall['brier_score']:.4f} log_loss={overall['log_loss']:.4f}")
        print(f"uniform_rps={report['baseline_uniform']['rps']:.4f} rps_skill={skill['rps']:.1%}")
        for stage, metrics in report["by_stage"].items():
            print(f"  {stage}: n={metrics['matches']} acc={metrics['accuracy']:.0%} rps={metrics['rps']:.3f}")
        if not args.dry_run:
            print(f"report={args.output}")
        return 0
    if args.command == "release-check":
        report = release_check(PROJECT_ROOT / "data" / "processed")
        print(report.render())
        return 0 if report.ok else 1
    parser.print_help()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
