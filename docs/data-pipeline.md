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
