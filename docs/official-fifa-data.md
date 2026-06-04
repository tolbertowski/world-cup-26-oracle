# Official FIFA Data

The app now supports FIFA's public calendar endpoint for the 2026 tournament:

```text
https://api.fifa.com/api/v3/calendar/matches?language=en&count=500&idSeason=285023
```

The `idSeason=285023` value is the current FIFA World Cup 2026 season visible in
official match-centre URLs.

## Sync

Run:

```bash
make sync-fifa
```

This executes:

```bash
world-cup-oracle sync-fifa --apply
```

The sync:

- downloads the official FIFA calendar JSON into ignored cache files;
- writes reviewed source CSVs into `data/raw/teams.csv` and `data/raw/fixtures.csv`;
- writes app-ready files into `data/processed/teams.csv` and `data/processed/fixtures.csv`;
- merges completed FIFA results into `data/manual/match_updates.csv` only when FIFA marks the match complete.

Upcoming matches are not auto-locked. Completed results are only accepted when
the FIFA payload has scores and `MatchStatus=0`.

## Release Gate

Run before any push or deployment:

```bash
make release-check
```

This fails if processed data is missing, because the app would fall back to demo
data. It also requires the strict 2026 shape: 48 teams, 12 groups of 4, and 72
group fixtures.

## During The Tournament

Use this rhythm:

```bash
make sync-fifa
make test
make release-check
```

If a just-finished match has not appeared as complete in FIFA's feed yet, do not
force-lock it automatically. Wait for the next sync or add a reviewed manual
entry in `data/manual/match_updates.csv`.
