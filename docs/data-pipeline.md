# Data Pipeline

The app is local-file first:

1. Cache or download free/public data into `data/cache/`.
2. Convert reviewed source snapshots into raw CSVs in `data/raw/`.
3. Validate the raw CSVs.
4. Import them into `data/processed/`.
5. Run the app. If processed files exist, the app uses them instead of demo data.

## Source CSVs

Create templates:

```bash
world-cup-oracle init-data
```

Team columns:

```text
team_code,team_name,group,confederation,fifa_rank,seed_rating
```

Fixture columns:

```text
match_id,stage,home_team,away_team,group,kickoff,venue,neutral_site
```

Use `stage=group` for group fixtures. Knockout stages use the values from
`MatchStage`, such as `round_of_32`, `quarter_final`, and `final`.

## Validate

Partial snapshots are allowed by default, which is useful while building the
pipeline:

```bash
world-cup-oracle validate-snapshot \
  --teams data/raw/teams.csv \
  --fixtures data/raw/fixtures.csv
```

Use strict mode when you expect a complete 2026 group-stage snapshot:

```bash
world-cup-oracle validate-snapshot \
  --teams data/raw/teams.csv \
  --fixtures data/raw/fixtures.csv \
  --strict
```

Strict mode currently requires:

- 48 teams;
- 72 group fixtures;
- exactly 4 teams per group.

## Import

After validation, export processed files:

```bash
world-cup-oracle import-snapshot \
  --teams data/raw/teams.csv \
  --fixtures data/raw/fixtures.csv
```

This writes:

- `data/processed/teams.csv`;
- `data/processed/fixtures.csv`.

These processed files are ignored by Git so local experiments do not pollute the
portfolio history.

## App Loading

The Streamlit app loads data in this order:

1. `data/processed/teams.csv` and `data/processed/fixtures.csv`;
2. bundled demo data if processed files are missing.

The sidebar shows the active data source.

## Player Call-Up Adjustments

Player call-ups are a reviewed manual layer, not a live dependency. Fill
`data/manual/player_callups.csv` with one row per called-up player:

```text
team_code,player_name,position,expected_role,player_rating,minutes_share,availability,club_strength,market_value_eur,notes
```

Recommended inputs:

- `player_rating`: source-agnostic 0-100 player score when available.
- `expected_role`: `starter`, `key`, `regular`, `rotation`, `squad`, `bench`, `fringe`, `reserve`, `injured`, or `out`.
- `minutes_share`: optional override from 0-1 or 0-100. Use this for likely starters and injury-managed players.
- `availability`: 0-1 or 0-100 injury/suspension availability.
- `club_strength` and `market_value_eur`: fallback signals when a direct player rating is missing.

Preview the generated team deltas:

```bash
world-cup-oracle apply-player-callups --dry-run
```

Apply them into `data/manual/team_adjustments.csv`:

```bash
world-cup-oracle apply-player-callups
```

The command replaces previous `player_callups:` generated rows and preserves
other manual rows. When the app reads team adjustments, duplicate team rows are
summed, so manual context and generated squad deltas can coexist.

## Historical Ratings Fit

Replace the placeholder seed ratings with ratings fit from real results. First
cache the goals-only `martj42/international_results` snapshot (it is gitignored,
not committed):

```bash
world-cup-oracle cache-url "https://raw.githubusercontent.com/martj42/international_results/master/results.csv" --name international_results.csv
```

Preview the fit without writing anything:

```bash
world-cup-oracle fit-ratings --dry-run
```

Apply it:

```bash
world-cup-oracle fit-ratings
```

This writes the fitted Elo into `seed_rating` in `data/processed/teams.csv` and
merges `attack_delta`/`defense_delta` rows into
`data/manual/team_adjustments.csv` under the `international_results:` prefix.
Like the call-up command, it replaces only its own generated rows and leaves
manual and `player_callups:` rows intact. The dataset is goals-only, so the fit
does not touch cards, corners, discipline, or tempo. Use `--no-seed-rating` to
write only the attack/defense deltas, or `--half-life-days` to tune recency.

**Live-tournament matches are excluded from the fit automatically.** World Cup
2026 results locked in `data/manual/match_updates.csv` already move ratings
in-tournament, so `fit-ratings` drops all matches on or after the tournament
start (`--cutoff`, default 2026-06-11) before fitting. This means re-fitting
against a freshly cached snapshot is always safe: played tournament games enter
the model exactly once, through the lock file, never through `seed_rating`.

## Final Stages Workflow

As the tournament progresses, keep the model current with one command:

```bash
world-cup-oracle sync-fifa --apply
```

This merges every completed official result — group and knockout — into
`data/manual/match_updates.csv`. Knockout rows carry `stage`, `home_team`, and
`away_team` columns; those let the simulator, the projected bracket, and the
rating updates match a played knockout game to its bracket slot by stage and
team pair (the simulator's own knockout ids are generated, so match ids alone
cannot identify them). Locking a knockout result by hand works the same way:
fill in `stage` (e.g. `round_of_16`) and the two team codes.

Locked results then do three things at load time: fix group standings, override
knockout slots so played matches are never re-simulated, and move each side's
Elo rating (margin-capped, shootouts as draws) so upsets propagate to every
remaining round. No re-fit is needed — and `fit-ratings` ignores
live-tournament matches entirely (see above).

## Official FIFA Shortcut

For the 2026 World Cup, prefer:

```bash
world-cup-oracle sync-fifa --apply
```

That command fetches FIFA's public calendar, validates the strict 2026 group
stage, writes processed fixtures, and updates completed official results.
