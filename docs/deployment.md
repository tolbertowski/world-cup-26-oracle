# Deployment Notes

## Streamlit Community Cloud

Run `make sync-fifa`, `make test`, and `make release-check` before deployment.
Do not deploy while the release check fails; that means the app would fall back
to demo data.

1. Push this repository to GitHub.
2. Create a new Streamlit app that points to `app.py`.
3. Use Python 3.11 or newer.
4. Install from `pyproject.toml`.
5. Keep private credentials out of the repo. The MVP does not require any.

## Local Portfolio Demo

Run:

```bash
streamlit run app.py
```

For screenshots, use the Dashboard, Match Predictor, Tournament Simulator, and
Model Check views. The bundled data is illustrative and should be labelled as a
demo until official 2026 fixture snapshots are imported.

## Data Refresh Policy

- Prefer open datasets and official fixture snapshots.
- Cache public pages or CSVs before import.
- Do not call paid APIs from the app.
- Use `data/manual/match_updates.csv` to lock real tournament results.
