"""The writer produces the layout the readers already understand.

That round trip is the point: a sweep records a database, and every existing
result tool works on it without knowing who wrote it.
"""

import pytest

from mcp_isicell.results.reader import ResultsDatabase
from mcp_isicell.results.writer import ResultsWriter, WriteError, measurement_rows


def test_a_written_database_reads_back_through_the_reader(tmp_path):
    path = tmp_path / "sweep.db"
    with ResultsWriter(path) as w:
        w.write_parameters([{"input.Cell.Common.x": 1.0}, {"input.Cell.Common.x": 2.0}])
        w.write_measurements(
            "states",
            [
                {"step": 0, "ID_PARAMETER": 0, "Alive": 10},
                {"step": 10, "ID_PARAMETER": 0, "Alive": 20},
                {"step": 0, "ID_PARAMETER": 1, "Alive": 10},
            ],
        )
    with ResultsDatabase(path) as db:
        summary = db.summarize()
        assert set(summary["tables"]) == {"parameters", "states"}
        assert db.count("states") == 3
        rows = db.get("states", filters=[{"column": "ID_PARAMETER", "value": 0}])
        assert [r["Alive"] for r in rows] == [10, 20]


def test_a_swept_point_reads_back_nested_and_ready_to_run(tmp_path):
    """The path written is the path read, through the $ flattening and out again."""
    path = tmp_path / "s.db"
    with ResultsWriter(path) as w:
        w.write_parameters([{"input.Cell.Common.cycleDuration": 18.0}])
    with ResultsDatabase(path) as db:
        assert db.parameters_for(0) == {
            "input": {"Cell": {"Common": {"cycleDuration": 18.0}}}
        }


def test_conditions_are_written_as_their_own_table(tmp_path):
    path = tmp_path / "s.db"
    with ResultsWriter(path) as w:
        w.write_conditions([{"input.Scenario.MOI": 0}, {"input.Scenario.MOI": 5}])
    with ResultsDatabase(path) as db:
        rows = db.get("condition")
    assert len(rows) == 2


def test_rows_that_disagree_about_columns_are_all_kept(tmp_path):
    """A model can gain a state between runs; the later rows must not be dropped."""
    path = tmp_path / "s.db"
    with ResultsWriter(path) as w:
        w.write_measurements(
            "states",
            [
                {"step": 0, "Alive": 10},
                {"step": 10, "Alive": 8, "Necrotic": 2},
            ],
        )
    with ResultsDatabase(path) as db:
        rows = db.get("states")
    assert len(rows) == 2
    assert rows[0]["Necrotic"] is None
    assert rows[1]["Necrotic"] == 2


def test_an_existing_file_is_not_clobbered_by_accident(tmp_path):
    path = tmp_path / "s.db"
    path.write_text("not a database")
    with pytest.raises(WriteError) as excinfo:
        ResultsWriter(path)
    assert "overwrite=True" in str(excinfo.value)
    assert path.read_text() == "not a database"


def test_an_existing_file_is_replaced_when_that_is_asked_for(tmp_path):
    path = tmp_path / "s.db"
    path.write_text("not a database")
    with ResultsWriter(path, overwrite=True) as w:
        w.write_measurements("states", [{"step": 0, "Alive": 1}])
    with ResultsDatabase(path) as db:
        assert db.count("states") == 1


def test_a_catchers_dict_becomes_one_column_per_key():
    rows = measurement_rows({"0": {"Alive": 10, "Dead": 1}}, {"ID_PARAMETER": 3})
    assert rows == [{"step": 0, "ID_PARAMETER": 3, "Alive": 10, "Dead": 1}]


def test_a_non_dict_measurement_is_stored_under_value():
    rows = measurement_rows({"0": 42}, {})
    assert rows == [{"step": 0, "value": 42}]


def test_the_seed_of_a_run_is_recorded_with_its_measurements():
    """Without it a surprising replicate cannot be reproduced."""
    rows = measurement_rows({"0": {"Alive": 1}}, {"ID_REPLICAT": 2}, seed=12345)
    assert rows[0]["seed"] == 12345


def test_no_seed_column_appears_when_the_sweep_was_unseeded():
    rows = measurement_rows({"0": {"Alive": 1}}, {})
    assert "seed" not in rows[0]


def test_a_structured_measurement_is_stored_as_json(tmp_path):
    """An attribute catcher returns a list per step; it must survive the write."""
    path = tmp_path / "s.db"
    rows = measurement_rows({"0": {"oxygen": [1.0, 2.0]}}, {})
    with ResultsWriter(path) as w:
        w.write_measurements("states", rows)
    with ResultsDatabase(path) as db:
        assert db.get("states")[0]["oxygen"] == "[1.0, 2.0]"


def test_steps_are_stored_as_integers_not_the_string_keys_they_arrive_as():
    rows = measurement_rows({"10": {"Alive": 1}}, {})
    assert rows[0]["step"] == 10 and isinstance(rows[0]["step"], int)
