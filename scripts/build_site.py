"""Assemble the static GitHub Pages site (stlite build).

Copies the Streamlit entrypoint, the package source, and the data files the app
reads into ``_site/`` alongside ``web/index.html``, and injects a file manifest
so stlite can mount everything into the in-browser filesystem with the same
relative layout the app uses locally.

Stdlib only; run from anywhere: ``python scripts/build_site.py [output_dir]``.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = PROJECT_ROOT / "web" / "index.html"
MANIFEST_PLACEHOLDER = "/*__STLITE_FILES__*/ {}"

# Everything the browser app needs, with the repo-relative layout preserved so
# ROOT/data path resolution inside the app keeps working unchanged.
INCLUDE = [
    "app.py",
    "src/world_cup_oracle",
    "data/processed/teams.csv",
    "data/processed/fixtures.csv",
    "data/processed/model_params.json",
    "data/manual/match_updates.csv",
    "data/manual/team_adjustments.csv",
    "data/manual/player_callups.csv",
]


def collect_files() -> list[Path]:
    files: list[Path] = []
    for entry in INCLUDE:
        path = PROJECT_ROOT / entry
        if path.is_dir():
            files.extend(sorted(p for p in path.rglob("*.py") if "__pycache__" not in p.parts))
        elif path.exists():
            files.append(path)
        else:
            raise SystemExit(f"build_site: required input missing: {entry}")
    return files


def build(output_dir: Path) -> None:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    manifest: dict[str, dict[str, str]] = {}
    for source in collect_files():
        relative = source.relative_to(PROJECT_ROOT)
        target = output_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        manifest[str(relative)] = {"url": f"./{relative}"}

    template = TEMPLATE.read_text(encoding="utf-8")
    if MANIFEST_PLACEHOLDER not in template:
        raise SystemExit("build_site: manifest placeholder not found in web/index.html")
    html = template.replace(MANIFEST_PLACEHOLDER, json.dumps(manifest, indent=8, sort_keys=True))
    (output_dir / "index.html").write_text(html, encoding="utf-8")
    # GitHub Pages runs Jekyll by default, which drops paths like _site assets;
    # .nojekyll disables that so every mounted file is served verbatim.
    (output_dir / ".nojekyll").write_text("", encoding="utf-8")
    print(f"built {output_dir} with {len(manifest)} mounted files")


if __name__ == "__main__":
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT_ROOT / "_site")
