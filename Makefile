.PHONY: run test simulate init-data

run:
	streamlit run app.py

test:
	python3 -m pytest -q

simulate:
	PYTHONPATH=src python3 -m world_cup_oracle.cli simulate-demo --simulations 1000 --seed 26

init-data:
	PYTHONPATH=src python3 -m world_cup_oracle.cli init-data
