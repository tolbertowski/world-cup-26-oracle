# Portfolio Checklist

- Run `make test`.
- Run `make sync-fifa`.
- Run `make fit-ratings` to refresh ratings from historical results.
- Run `make player-callups` after squad inputs change.
- Run `make release-check`.
- Run `make simulate`.
- Launch `make run`.
- Capture screenshots of Dashboard, Match Predictor, Tournament Simulator, and Model Check.
- Do not push or deploy while `make release-check` fails.
- Verify the Pages build locally: `python3 scripts/build_site.py && python3 scripts/serve_site.py`.
- After merging, confirm the Deploy to GitHub Pages action is green and the live URL renders.
- Keep the disclaimer visible: this is not betting advice.
