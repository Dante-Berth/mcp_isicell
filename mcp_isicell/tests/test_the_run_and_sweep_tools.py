"""The M4/M5 surface, driven as an LLM drives it.

The simulate path runs against a fake build: a scriptLauncher.py that simply
execs the generated script, and a stub `isicell` package providing Simu. So the
real generated script really runs, with no C++ and no SWIG.
"""

import sqlite3
import textwrap

import pytest

from mcp_isicell import server
from mcp.server.mcpserver.exceptions import ToolError

LAUNCHER = textwrap.dedent(
    """
    import runpy, sys
    sys.path.insert(0, sys.argv[1])
    runpy.run_path(sys.argv[2], run_name="__main__")
    """
)

# A Simu that counts a population doubling every 10 steps, so a run has a
# result worth asserting on.
FAKE_ISICELL = textwrap.dedent(
    """
    class _Cells:
        def __init__(self, simu): self._s = simu
        def countState(self): return {"Alive": self._s._n}
        def countType(self): return {"Common": self._s._n}
        def get(self, name): return [float(self._s._n)]
        def __len__(self): return self._s._n

    class Simu:
        def __init__(self, params, seed=None, benchmark=None):
            self.params = params
            self._step = 0
            self._n = 10
            self.cells = _Cells(self)
        def getCurrentStep(self): return self._step
        def update(self, n=1):
            self._step += n
            self._n *= 2 ** (n // 10)
    """
)


@pytest.fixture
def built(tmp_path, monkeypatch):
    """A checkout with one build that has a Python module."""
    root = tmp_path / "isicell"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "mainGenerator.py").write_text("")
    (root / "scripts" / "scriptLauncher.py").write_text(LAUNCHER)
    build = root / "simulations" / "sim_ok"
    (build / "isicell").mkdir(parents=True)
    (build / "isicell" / "__init__.py").write_text(FAKE_ISICELL)
    monkeypatch.setenv("ISICELL_ROOT", str(root))
    server.SESSION.reset("t")
    server.SESSION.token = "sim_ok"
    yield root
    server.SESSION.reset("t")


def test_the_catchers_are_listed_with_what_they_return():
    names = {c["name"] for c in server.isicell_list_catchers()}
    assert "countState" in names and "attribute:<name>" in names


def test_a_run_returns_a_measurement_per_requested_step(built):
    out = server.isicell_run_simulate(
        params={"input": {"Cell": {"Common": {}}, "Scenario": {"dt": 360}}},
        steps=[0, 10, 20],
        catcher="countState",
    )
    assert out["ok"]
    assert out["data"] == {"0": {"Alive": 10}, "10": {"Alive": 20}, "20": {"Alive": 40}}


def test_a_run_uses_the_session_build_when_no_token_is_given(built):
    out = server.isicell_run_simulate(params={}, steps=[0])
    assert out["token"] == "sim_ok"


def test_a_run_without_any_build_says_so_rather_than_failing_obscurely(built):
    server.SESSION.token = None
    with pytest.raises(ToolError) as excinfo:
        server.isicell_run_simulate(params={}, steps=[0])
    assert "Compile first" in str(excinfo.value)


def test_a_build_with_no_python_module_is_refused_with_the_fix(built):
    (built / "simulations" / "sim_nopy").mkdir(parents=True)
    with pytest.raises(ToolError) as excinfo:
        server.isicell_run_simulate(params={}, steps=[0], token="sim_nopy")
    assert "type='python'" in str(excinfo.value)


def test_an_unknown_catcher_is_refused_before_the_simulation_starts(built):
    with pytest.raises(ToolError) as excinfo:
        server.isicell_run_simulate(params={}, steps=[0], catcher="rm -rf /")
    assert "countState" in str(excinfo.value)


def test_the_generated_script_does_not_survive_the_run(built):
    """A run leaves nothing behind in the build directory."""
    server.isicell_run_simulate(params={}, steps=[0])
    assert not (built / "simulations" / "sim_ok" / "_mcp_run.py").exists()


def test_an_attribute_catcher_reads_that_attribute(built):
    out = server.isicell_run_simulate(params={}, steps=[0], catcher="attribute:oxygen")
    assert out["data"]["0"] == {"oxygen": [10.0]}


# -- sweeps ---------------------------------------------------------------


def test_a_sweep_preview_costs_nothing_and_shows_the_points():
    out = server.isicell_sweep_preview(
        parameters=[{"name": "input.Cell.Common.x", "kind": "float", "low": 1, "high": 2}],
        n_samples=5,
        seed=1,
    )
    assert out["n_samples"] == 5
    assert all(1 <= p["input.Cell.Common.x"] <= 2 for p in out["points"])


def test_a_sweep_does_not_run_until_it_is_confirmed(built):
    out = server.isicell_sweep_run(
        base_params={"input": {}},
        parameters=[{"name": "input.Scenario.dt", "kind": "float", "low": 60, "high": 600}],
        steps=[0],
        n_samples=3,
    )
    assert out["ran"] is False and out["n_samples"] == 3
    assert "confirm=True" in out["note"]


def test_a_confirmed_sweep_runs_one_simulation_per_point(built):
    out = server.isicell_sweep_run(
        base_params={"input": {"Cell": {"Common": {}}, "Scenario": {}}},
        parameters=[{"name": "input.Scenario.dt", "kind": "float", "low": 60, "high": 600}],
        steps=[0, 10],
        n_samples=3,
        seed=5,
        confirm=True,
    )
    assert out["ran"] is True and len(out["runs"]) == 3
    assert all(r["ok"] for r in out["runs"])
    assert all(r["data"]["10"] == {"Alive": 20} for r in out["runs"])


def test_each_sweep_run_records_the_point_it_used(built):
    out = server.isicell_sweep_run(
        base_params={"input": {}},
        parameters=[{"name": "input.Scenario.dt", "kind": "int", "low": 1, "high": 9}],
        steps=[0],
        n_samples=4,
        seed=3,
        confirm=True,
    )
    points = [r["point"]["input.Scenario.dt"] for r in out["runs"]]
    assert len(set(points)) == 4  # LHS gives four distinct strata


def test_a_malformed_parameter_space_is_refused():
    with pytest.raises(ToolError):
        server.isicell_sweep_preview(
            parameters=[{"name": "x", "kind": "float", "low": 10, "high": 1}], n_samples=3
        )


# -- results --------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "r.db"
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE parameters ("ID_PARAMETER" INTEGER, "Cell$Common$x" REAL)')
    conn.executemany("INSERT INTO parameters VALUES (?,?)", [(0, 1.0), (1, 2.0)])
    conn.execute('CREATE TABLE error ("ID_PARAMETER" INTEGER, "rmse" REAL)')
    conn.executemany("INSERT INTO error VALUES (?,?)", [(0, 0.9), (1, 0.1)])
    conn.commit()
    conn.close()
    return str(path)


def test_summarize_reports_the_error_columns_worth_optimising(db):
    assert server.isicell_results_summarize(db)["errorColumns"] == ["rmse"]


def test_best_returns_the_lowest_scoring_parameter(db):
    assert server.isicell_results_best(db, "rmse", n=1)[0]["ID_PARAMETER"] == 1


def test_parameters_come_back_ready_to_feed_into_a_run(db):
    assert server.isicell_results_parameters(db, 1) == {"Cell": {"Common": {"x": 2.0}}}


def test_a_filter_is_structured_rather_than_sql(db):
    rows = server.isicell_results_get(
        db, "error", filters=[{"column": "ID_PARAMETER", "op": "ge", "value": 1}]
    )
    assert [r["ID_PARAMETER"] for r in rows] == [1]


def test_an_unknown_error_column_is_refused_and_names_the_real_ones(db):
    with pytest.raises(ToolError) as excinfo:
        server.isicell_results_best(db, "nope")
    assert "rmse" in str(excinfo.value)


def test_a_missing_database_is_refused(tmp_path):
    with pytest.raises(ToolError):
        server.isicell_results_summarize(str(tmp_path / "absent.db"))
