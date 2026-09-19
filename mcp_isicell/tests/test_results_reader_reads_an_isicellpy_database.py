"""Reading a results database written the way isicellpy writes one.

The fixture is built with plain sqlite3 following DatabaseManager's conventions:
parameters flattened with `$`, lists as JSON, error rows per parameter and
condition.
"""

import json
import sqlite3

import pytest

from mcp_isicell.results.reader import ResultsDatabase, ResultsError
from mcp_isicell.results.schema import flatten, unflatten


@pytest.fixture
def results_db(tmp_path):
    path = tmp_path / "example.db"
    conn = sqlite3.connect(path)
    conn.execute(
        'CREATE TABLE parameters ("ID_PARAMETER" INTEGER, '
        '"Cell$Common$cycleDuration" REAL, "Scenario$dt" REAL, "Scenario$infection" TEXT)'
    )
    conn.executemany(
        "INSERT INTO parameters VALUES (?,?,?,?)",
        [
            (0, 12.0, 360.0, json.dumps([[0, 5]])),
            (1, 18.0, 360.0, json.dumps([[0, 5]])),
            (2, 24.0, 360.0, json.dumps([[0, 5]])),
        ],
    )
    conn.execute('CREATE TABLE condition ("ID_CONDITION" INTEGER, "MOI" REAL)')
    conn.executemany("INSERT INTO condition VALUES (?,?)", [(0, 0.1), (1, 0.2)])
    conn.execute(
        'CREATE TABLE error ("ID_PARAMETER" INTEGER, "ID_CONDITION" INTEGER, '
        '"rmse" REAL, "mae" REAL)'
    )
    conn.executemany(
        "INSERT INTO error VALUES (?,?,?,?)",
        [
            # param 0: best rmse, worst mae
            (0, 0, 0.10, 0.90), (0, 1, 0.12, 0.88),
            # param 1: middling on both
            (1, 0, 0.50, 0.50), (1, 1, 0.52, 0.48),
            # param 2: worst rmse, best mae
            (2, 0, 0.90, 0.10), (2, 1, 0.88, 0.12),
        ],
    )
    conn.execute('CREATE TABLE states ("ID_PARAMETER" INTEGER, "step" INTEGER, "Alive" INTEGER)')
    conn.executemany(
        "INSERT INTO states VALUES (?,?,?)",
        [(0, 0, 10), (0, 10, 20), (1, 0, 10), (1, 10, 15)],
    )
    conn.commit()
    conn.close()
    return path


def test_summarize_names_the_tables_and_the_error_columns(results_db):
    with ResultsDatabase(results_db) as db:
        summary = db.summarize()
    assert set(summary["tables"]) == {"parameters", "condition", "error", "states"}
    assert summary["tables"]["error"]["rows"] == 6
    assert set(summary["errorColumns"]) == {"rmse", "mae"}


def test_parameter_names_are_reported_with_dots_not_dollars(results_db):
    """`$` is a storage detail; a user writes and reads dotted paths."""
    with ResultsDatabase(results_db) as db:
        names = db.summarize()["parameterNames"]
    assert "Cell.Common.cycleDuration" in names
    assert not any("$" in n for n in names)


def test_parameters_come_back_nested_and_ready_to_run(results_db):
    with ResultsDatabase(results_db) as db:
        params = db.parameters_for(1)
    assert params["Cell"]["Common"]["cycleDuration"] == 18.0
    assert params["Scenario"]["infection"] == [[0, 5]]
    assert "ID_PARAMETER" not in params


def test_the_best_parameter_set_is_the_one_with_the_lowest_average_error(results_db):
    with ResultsDatabase(results_db) as db:
        best = db.best("rmse", n=3)
    assert [r["ID_PARAMETER"] for r in best] == [0, 1, 2]
    assert best[0]["score"] == pytest.approx(0.11)


def test_best_per_condition_ranks_within_each_condition(results_db):
    with ResultsDatabase(results_db) as db:
        best = db.best("rmse", n=1, for_each_condition=True)
    assert {r["ID_CONDITION"] for r in best} == {0, 1}
    assert all(r["ID_PARAMETER"] == 0 for r in best)


def test_a_trade_off_keeps_every_point_that_wins_on_some_axis(results_db):
    """0, 1 and 2 trade rmse against mae, so none dominates another."""
    with ResultsDatabase(results_db) as db:
        front = db.pareto(["rmse", "mae"])
    assert {r["ID_PARAMETER"] for r in front} == {0, 1, 2}


def test_a_point_worse_on_every_axis_is_dropped_from_the_front(tmp_path):
    """The real test of domination: param 3 loses to param 0 on both errors."""
    path = tmp_path / "dominated.db"
    conn = sqlite3.connect(path)
    conn.execute(
        'CREATE TABLE error ("ID_PARAMETER" INTEGER, "rmse" REAL, "mae" REAL)'
    )
    conn.executemany(
        "INSERT INTO error VALUES (?,?,?)",
        [(0, 0.10, 0.10), (3, 0.90, 0.90)],
    )
    conn.commit()
    conn.close()
    with ResultsDatabase(path) as db:
        front = db.pareto(["rmse", "mae"])
    assert [r["ID_PARAMETER"] for r in front] == [0]


def test_a_single_objective_pareto_is_just_the_minimum(results_db):
    with ResultsDatabase(results_db) as db:
        front = db.pareto(["rmse"])
    assert [r["ID_PARAMETER"] for r in front] == [0]


def test_rows_can_be_filtered_without_the_caller_writing_sql(results_db):
    with ResultsDatabase(results_db) as db:
        rows = db.get("states", filters=[{"column": "ID_PARAMETER", "op": "eq", "value": 1}])
    assert rows and all(r["ID_PARAMETER"] == 1 for r in rows)


def test_a_filter_value_is_bound_not_interpolated(results_db):
    """A value that looks like SQL is data, so it matches nothing and changes nothing."""
    with ResultsDatabase(results_db) as db:
        rows = db.get("states", filters=[{"column": "ID_PARAMETER", "value": "0 OR 1=1"}])
        assert rows == []
        assert db.count("states") == 4


def test_an_unknown_column_is_refused_and_says_what_exists(results_db):
    with ResultsDatabase(results_db) as db:
        with pytest.raises(ResultsError) as excinfo:
            db.get("states", filters=[{"column": "nope", "value": 1}])
    assert "nope" in str(excinfo.value) and "step" in str(excinfo.value)


def test_an_unknown_operator_is_refused(results_db):
    with ResultsDatabase(results_db) as db:
        with pytest.raises(ResultsError):
            db.get("states", filters=[{"column": "step", "op": "DROP", "value": 1}])


def test_an_unknown_table_is_refused_and_lists_the_real_ones(results_db):
    with ResultsDatabase(results_db) as db:
        with pytest.raises(ResultsError) as excinfo:
            db.get("absent")
    assert "states" in str(excinfo.value)


def test_a_missing_database_is_refused_rather_than_created(tmp_path):
    with pytest.raises(ResultsError):
        ResultsDatabase(tmp_path / "nothing.db")
    assert not (tmp_path / "nothing.db").exists()


def test_the_database_is_opened_read_only(results_db):
    with ResultsDatabase(results_db) as db:
        with pytest.raises(sqlite3.OperationalError):
            db.conn.execute("DELETE FROM states")


def test_a_row_limit_is_always_applied(results_db):
    with ResultsDatabase(results_db) as db:
        assert len(db.get("states", limit=2)) == 2


def test_flatten_and_unflatten_are_inverse():
    nested = {"Cell": {"Common": {"x": 1.0}}, "Scenario": {"dt": 360, "protocol": [[0, 5]]}}
    assert unflatten(flatten(nested)) == nested


def test_flatten_stores_lists_as_json_the_way_isicellpy_does():
    assert flatten({"Scenario": {"p": [[0, 5]]}}) == {"Scenario$p": "[[0, 5]]"}
