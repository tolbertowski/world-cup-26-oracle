.PHONY: run test simulate bracket init-data validate-templates sync-fifa release-check

run:
	streamlit run app.py

test:
	python3 -m pytest -q

simulate:
	PYTHONPATH=src python3 -m world_cup_oracle.cli simulate-demo --simulations 1000 --seed 26

bracket:
	PYTHONPATH=src python3 -m world_cup_oracle.cli project-bracket

init-data:
	PYTHONPATH=src python3 -m world_cup_oracle.cli init-data

validate-templates:
	PYTHONPATH=src python3 -m world_cup_oracle.cli validate-snapshot --teams data/raw/teams_template.csv --fixtures data/raw/fixtures_template.csv

sync-fifa:
	PYTHONPATH=src python3 -m world_cup_oracle.cli sync-fifa --apply

release-check:
	PYTHONPATH=src python3 -m world_cup_oracle.cli release-check
