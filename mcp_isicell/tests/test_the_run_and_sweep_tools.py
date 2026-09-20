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
    assert out["totalRuns"] == 5 and out["totalPoints"] == 5
    assert all(1 <= p["input.Cell.Common.x"] <= 2 for p in out["points"])


def test_a_sweep_does_not_run_until_it_is_confirmed(built):
    out = server.isicell_sweep_run(
        base_params={"input": {}},
        parameters=[{"name": "input.Scenario.dt", "kind": "float", "low": 60, "high": 600}],
        steps=[0],
        n_samples=3,
    )
    assert out["ran"] is False and out["totalRuns"] == 3
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
    assert out["failed"] == 0


def test_each_sweep_run_is_identified_by_the_point_it_came_from(built):
    out = server.isicell_sweep_run(
        base_params={"input": {}},
        parameters=[{"name": "input.Scenario.dt", "kind": "int", "low": 1, "high": 9}],
        steps=[0],
        n_samples=4,
        seed=3,
        confirm=True,
    )
    assert [r["ID_PARAMETER"] for r in out["runs"]] == [0, 1, 2, 3]


def test_a_preview_shows_the_distinct_points_a_sweep_would_use(built):
    out = server.isicell_sweep_preview(
        parameters=[{"name": "input.Scenario.dt", "kind": "int", "low": 1, "high": 9}],
        n_samples=4,
        seed=3,
    )
    values = [p["input.Scenario.dt"] for p in out["points"]]
    assert len(set(values)) == 4  # LHS gives four distinct strata


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


# -- sweeps with conditions, replicates and recording ---------------------


def test_a_sweep_crosses_conditions_and_replicates_with_the_points(built):
    out = server.isicell_sweep_run(
        base_params={"input": {"Cell": {"Common": {}}, "Scenario": {}}},
        parameters=[{"name": "input.Scenario.dt", "kind": "int", "low": 1, "high": 9}],
        n_samples=2,
        conditions=[{"input.Scenario.MOI": 0}, {"input.Scenario.MOI": 5}],
        replicates=2,
        steps=[0],
        seed=1,
        confirm=True,
    )
    assert out["totalRuns"] == 8 and len(out["runs"]) == 8
    assert all(r["ok"] for r in out["runs"])


def test_every_run_of_a_seeded_sweep_reports_its_own_seed(built):
    out = server.isicell_sweep_run(
        base_params={"input": {}},
        replicates=3,
        steps=[0],
        seed=42,
        confirm=True,
    )
    seeds = [r["seed"] for r in out["runs"]]
    assert len(set(seeds)) == 3 and all(isinstance(s, int) for s in seeds)


def test_a_seeded_sweep_reruns_identically(built):
    kwargs = dict(
        base_params={"input": {}},
        parameters=[{"name": "input.Scenario.dt", "kind": "int", "low": 1, "high": 100}],
        n_samples=3,
        steps=[0],
        seed=99,
        confirm=True,
    )
    a = server.isicell_sweep_run(**kwargs)
    b = server.isicell_sweep_run(**kwargs)
    assert [r["seed"] for r in a["runs"]] == [r["seed"] for r in b["runs"]]


def test_only_the_axes_that_vary_appear_in_a_runs_identity(built):
    """A one-point unreplicated sweep carries no ID columns at all."""
    out = server.isicell_sweep_run(
        base_params={"input": {}}, steps=[0], confirm=True
    )
    run = out["runs"][0]
    assert "ID_PARAMETER" not in run and "ID_CONDITION" not in run
    assert "ID_REPLICAT" not in run


def test_a_recorded_sweep_is_readable_by_the_results_tools(built, tmp_path):
    """The loop closes: sweep writes, the readers query what it wrote."""
    db = str(tmp_path / "sweep.db")
    out = server.isicell_sweep_run(
        base_params={"input": {"Cell": {"Common": {}}, "Scenario": {}}},
        parameters=[{"name": "input.Scenario.dt", "kind": "int", "low": 1, "high": 9}],
        n_samples=3,
        steps=[0, 10],
        seed=2,
        database=db,
        confirm=True,
    )
    assert out["database"] == db and out["table"] == "countState"

    summary = server.isicell_results_summarize(db)
    assert set(summary["tables"]) == {"parameters", "countState"}
    assert summary["tables"]["countState"]["rows"] == 6  # 3 runs x 2 steps

    rows = server.isicell_results_get(
        db, "countState", filters=[{"column": "ID_PARAMETER", "value": 0}]
    )
    assert [r["step"] for r in rows] == [0, 10]

    # And a swept point reads back in the shape a run takes.
    params = server.isicell_results_parameters(db, 0)
    assert "dt" in params["input"]["Scenario"]


def test_a_recorded_sweep_stores_the_conditions_it_crossed(built, tmp_path):
    db = str(tmp_path / "cond.db")
    server.isicell_sweep_run(
        base_params={"input": {}},
        conditions=[{"input.Scenario.MOI": 0}, {"input.Scenario.MOI": 5}],
        steps=[0],
        database=db,
        confirm=True,
    )
    assert len(server.isicell_results_get(db, "condition")) == 2


def test_recording_onto_an_existing_file_is_refused_by_default(built, tmp_path):
    db = tmp_path / "taken.db"
    db.write_text("precious")
    with pytest.raises(ToolError) as excinfo:
        server.isicell_sweep_run(
            base_params={"input": {}}, steps=[0], database=str(db), confirm=True
        )
    assert "overwrite=True" in str(excinfo.value)
    assert db.read_text() == "precious"


def test_an_unrecorded_sweep_returns_its_measurements_inline(built):
    out = server.isicell_sweep_run(
        base_params={"input": {}}, steps=[0], confirm=True
    )
    assert out["runs"][0]["data"] == {"0": {"Alive": 10}}


def test_a_design_too_large_to_mean_well_is_refused(built):
    with pytest.raises(ToolError) as excinfo:
        server.isicell_sweep_preview(
            parameters=[{"name": "input.Scenario.dt", "kind": "int", "low": 1, "high": 99}],
            n_samples=500,
            replicates=10,
        )
    assert "5000 runs" in str(excinfo.value)


# -- scoring --------------------------------------------------------------


def test_the_metrics_are_listed_with_what_each_measures():
    names = {m["name"] for m in server.isicell_list_metrics()}
    assert {"rmse", "mae"} <= names


def test_a_scored_sweep_fills_the_error_table_that_best_reads(built, tmp_path):
    """This is the loop closing: sweep, score, rank, re-run the winner."""
    db = str(tmp_path / "scored.db")
    # The stub Simu counts 10 + step, so a run measured at step 10 gives 20.
    out = server.isicell_sweep_run(
        base_params={"input": {"Cell": {"Common": {}}, "Scenario": {}}},
        parameters=[{"name": "input.Scenario.dt", "kind": "int", "low": 1, "high": 9}],
        n_samples=3,
        steps=[0, 10],
        seed=4,
        database=db,
        reference={"0": 10, "10": 20},
        column="Alive",
        metrics=["rmse", "mae"],
        confirm=True,
    )
    assert out["scored"] == 3 and out["metrics"] == ["rmse", "mae"]

    summary = server.isicell_results_summarize(db)
    assert "error" in summary["tables"]
    assert set(summary["errorColumns"]) >= {"rmse", "mae"}

    best = server.isicell_results_best(db, "rmse", n=3)
    assert len(best) == 3
    # The stub ignores parameters, so every run matches the reference exactly.
    assert all(b["score"] == pytest.approx(0.0) for b in best)


def test_a_scored_sweep_supports_a_pareto_front_over_two_metrics(built, tmp_path):
    db = str(tmp_path / "front.db")
    server.isicell_sweep_run(
        base_params={"input": {}},
        parameters=[{"name": "input.Scenario.dt", "kind": "int", "low": 1, "high": 9}],
        n_samples=3,
        steps=[0, 10],
        seed=4,
        database=db,
        reference={"0": 1, "10": 2},
        column="Alive",
        metrics=["rmse", "mae"],
        confirm=True,
    )
    front = server.isicell_results_pareto(db, ["rmse", "mae"])
    assert front  # every run ties, so none dominates and all survive


def test_each_run_reports_its_own_scores_inline_too(built, tmp_path):
    out = server.isicell_sweep_run(
        base_params={"input": {}},
        steps=[0],
        database=str(tmp_path / "s.db"),
        reference={"0": 5},
        column="Alive",
        confirm=True,
    )
    assert out["runs"][0]["scores"]["rmse"] == pytest.approx(5.0)


def test_scoring_without_a_database_is_refused_with_the_reason(built):
    with pytest.raises(ToolError) as excinfo:
        server.isicell_sweep_run(
            base_params={"input": {}}, steps=[0], reference={"0": 1}, column="Alive",
            confirm=True,
        )
    assert "error table" in str(excinfo.value)


def test_scoring_without_a_column_is_refused_with_the_reason(built, tmp_path):
    with pytest.raises(ToolError) as excinfo:
        server.isicell_sweep_run(
            base_params={"input": {}}, steps=[0], database=str(tmp_path / "s.db"),
            reference={"0": 1}, confirm=True,
        )
    assert "column" in str(excinfo.value)


def test_a_reference_naming_steps_the_run_never_measured_is_refused(built, tmp_path):
    with pytest.raises(ToolError) as excinfo:
        server.isicell_sweep_run(
            base_params={"input": {}}, steps=[0], database=str(tmp_path / "s.db"),
            reference={"999": 1}, column="Alive", confirm=True,
        )
    assert "999" in str(excinfo.value)


def test_an_unscored_sweep_leaves_no_error_table(built, tmp_path):
    db = str(tmp_path / "plain.db")
    server.isicell_sweep_run(base_params={"input": {}}, steps=[0], database=db, confirm=True)
    assert "error" not in server.isicell_results_summarize(db)["tables"]


# -- background sweeps ----------------------------------------------------


def _await_job(job: str, timeout: float = 60.0) -> dict:
    import time as _time

    deadline = _time.time() + timeout
    while _time.time() < deadline:
        entry = server.isicell_job_status(job)
        if entry["status"] != "running":
            return entry
        _time.sleep(0.05)
    raise AssertionError(f"background job {job} never finished")


@pytest.fixture
def job_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ISICELL_JOBS_DIR", str(tmp_path / "jobs"))
    yield tmp_path / "jobs"


def test_a_background_sweep_returns_at_once_and_finishes_later(built, tmp_path, job_dir):
    """The whole point: the call returns before the simulations do."""
    db = str(tmp_path / "bg.db")
    out = server.isicell_sweep_run(
        base_params={"input": {"Cell": {"Common": {}}, "Scenario": {}}},
        parameters=[{"name": "input.Scenario.dt", "kind": "int", "low": 1, "high": 9}],
        n_samples=3,
        steps=[0, 10],
        seed=1,
        database=db,
        background=True,
        confirm=True,
    )
    assert out["background"] is True and out["job"]

    entry = _await_job(out["job"])
    assert entry["status"] == "finished", server.isicell_job_log(out["job"])["lines"]

    # And the database the child wrote is readable through the usual tools.
    summary = server.isicell_results_summarize(db)
    assert summary["tables"]["countState"]["rows"] == 6


def test_a_background_sweep_is_previewed_before_it_is_confirmed(built, tmp_path, job_dir):
    out = server.isicell_sweep_run(
        base_params={"input": {}},
        steps=[0],
        database=str(tmp_path / "x.db"),
        background=True,
    )
    assert out["ran"] is False and "background" in out["note"]
    assert server.isicell_job_list() == []


def test_a_background_sweep_without_a_database_is_refused(built, job_dir):
    """Nothing is waiting to receive measurements inline, so they would be lost."""
    with pytest.raises(ToolError) as excinfo:
        server.isicell_sweep_run(
            base_params={"input": {}}, steps=[0], background=True, confirm=True
        )
    assert "database" in str(excinfo.value)


def test_a_finished_background_job_appears_in_the_listing(built, tmp_path, job_dir):
    out = server.isicell_sweep_run(
        base_params={"input": {}},
        steps=[0],
        database=str(tmp_path / "l.db"),
        background=True,
        confirm=True,
    )
    _await_job(out["job"])
    assert [j["token"] for j in server.isicell_job_list()] == [out["job"]]


def test_a_background_job_log_carries_what_the_sweep_reported(built, tmp_path, job_dir):
    out = server.isicell_sweep_run(
        base_params={"input": {}},
        steps=[0],
        database=str(tmp_path / "log.db"),
        background=True,
        confirm=True,
    )
    _await_job(out["job"])
    assert any("totalRuns" in line for line in server.isicell_job_log(out["job"])["lines"])


def test_a_background_sweep_that_fails_reads_as_failed(built, tmp_path, job_dir):
    """An unwritable database path makes the child exit non-zero."""
    out = server.isicell_sweep_run(
        base_params={"input": {}},
        steps=[0],
        database="/proc/cannot/write/here.db",
        background=True,
        confirm=True,
    )
    entry = _await_job(out["job"])
    assert entry["status"] == "failed" and entry["exit_code"] != 0


# -- refinement -----------------------------------------------------------


def test_refining_a_scored_sweep_narrows_around_its_winner(built, tmp_path):
    db = str(tmp_path / "ref.db")
    space = [{"name": "input.Scenario.growth", "kind": "float", "low": 0.0, "high": 10.0}]
    server.isicell_sweep_run(
        base_params={"input": {"Cell": {"Common": {}}, "Scenario": {}}},
        parameters=space,
        n_samples=4,
        steps=[0, 10],
        seed=1,
        database=db,
        reference={"0": 10, "10": 20},
        column="Alive",
        metrics=["rmse"],
        confirm=True,
    )
    out = server.isicell_sweep_refine(db, "rmse", space, shrink=0.5)
    narrowed = out["parameters"][0]
    assert narrowed["high"] - narrowed["low"] == pytest.approx(5.0)
    assert 0.0 <= narrowed["low"] < narrowed["high"] <= 10.0
    assert out["centred_on"]["ID_PARAMETER"] is not None


def test_refining_runs_nothing_and_says_so(built, tmp_path):
    db = str(tmp_path / "ref2.db")
    space = [{"name": "input.Scenario.growth", "kind": "float", "low": 0.0, "high": 10.0}]
    server.isicell_sweep_run(
        base_params={"input": {}}, parameters=space, n_samples=2, steps=[0],
        seed=1, database=db, reference={"0": 10}, column="Alive", confirm=True,
    )
    out = server.isicell_sweep_refine(db, "rmse", space)
    assert "Nothing has run" in out["note"]
    # The database is unchanged: no second sweep was recorded.
    assert server.isicell_results_summarize(db)["tables"]["parameters"]["rows"] == 2


def test_a_refined_space_can_be_swept_straight_away(built, tmp_path):
    """The output is a parameters list, so it feeds back in without editing."""
    db = str(tmp_path / "ref3.db")
    space = [{"name": "input.Scenario.growth", "kind": "float", "low": 0.0, "high": 10.0}]
    server.isicell_sweep_run(
        base_params={"input": {}}, parameters=space, n_samples=3, steps=[0],
        seed=1, database=db, reference={"0": 10}, column="Alive", confirm=True,
    )
    refined = server.isicell_sweep_refine(db, "rmse", space)
    second = server.isicell_sweep_run(
        base_params={"input": {}},
        parameters=refined["parameters"],
        n_samples=3,
        steps=[0],
        seed=2,
        database=str(tmp_path / "ref3b.db"),
        confirm=True,
    )
    assert second["ran"] is True and second["failed"] == 0


def test_refining_an_unscored_database_says_what_is_missing(built, tmp_path):
    db = str(tmp_path / "plain.db")
    space = [{"name": "input.Scenario.growth", "kind": "float", "low": 0.0, "high": 10.0}]
    server.isicell_sweep_run(
        base_params={"input": {}}, parameters=space, n_samples=2, steps=[0],
        seed=1, database=db, confirm=True,
    )
    with pytest.raises(ToolError) as excinfo:
        server.isicell_sweep_refine(db, "rmse", space)
    assert "error" in str(excinfo.value).lower()


def test_a_run_whose_script_dies_before_printing_raises_with_guidance(built, monkeypatch):
    """No measurements at all means a traceback, which the caller must not have to parse."""
    (built / "simulations" / "sim_ok" / "isicell" / "__init__.py").write_text(
        "raise ModuleNotFoundError(\"No module named 'pandas'\")"
    )
    with pytest.raises(ToolError) as caught:
        server.isicell_run_simulate(
            params={"input": {"Cell": {"Common": {}}, "Scenario": {}}}, steps=[0]
        )
    message = str(caught.value)
    assert "'pandas'" in message
    assert "ISICELL_PYTHON" in message


def test_a_run_interpreter_that_does_not_exist_says_so(built, monkeypatch):
    """A bare FileNotFoundError reaches the client as "Error executing tool"."""
    monkeypatch.setenv("ISICELL_PYTHON", str(built / "no" / "such" / "python"))
    with pytest.raises(ToolError) as caught:
        server.isicell_run_simulate(
            params={"input": {"Cell": {"Common": {}}, "Scenario": {}}}, steps=[0]
        )
    assert "does not exist" in str(caught.value)
    assert "ISICELL_PYTHON" in str(caught.value)
