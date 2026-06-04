from world_cup_oracle.app import _probability_rows


def test_probability_rows_handles_empty_probabilities() -> None:
    rows = _probability_rows({}, {})

    assert list(rows.columns) == ["Label", "Code", "Probability"]
    assert rows.empty
