"""Sweeps with workers > 1.

Two things have to hold at once: the runs really do overlap, and the rows they
produce are still in design order. The second is what makes the first safe --
ID_CONDITION 2 must mean the third condition whether or not it finished third.

Concurrency is proved with a barrier rather than a stopwatch: every simulation
records its arrival and waits for its siblings, so a sequential sweep fails the
assertion instead of merely being slow.
"""

from __future__ import annotations

import json
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

# `arrived` is how many runs were in flight when this one started; `mark` is
# which condition it came from. Each run then sleeps for longer the lower its
# mark, so completion order is the reverse of design order.
BARRIER_ISICELL = textwrap.dedent(
    """
    import os, time
    from pathlib import Path

    BARRIER = Path(os.environ["SWEEP_BARRIER"])
    EXPECT = int(os.environ["SWEEP_EXPECT"])

    class _Cells:
        def __init__(self, simu): self._s = simu
        def get(self, name): return [float(getattr(self._s, name))]

    class Simu:
        def __init__(self, params, seed=None, benchmark=None):
            self.mark = int(params["input"]["mark"])
            (BARRIER / str(self.mark)).write_text("")
            deadline = time.time() + 3
            while len(list(BARRIER.iterdir())) < EXPECT and time.time() < deadline:
                time.sleep(0.01)
            self.arrived = len(list(BARRIER.iterdir()))
            time.sleep(0.05 * (EXPECT - self.mark))
            self.cells = _Cells(self)
        def getCurrentStep(self): return 0
        def update(self, n=1): pass
    """
)

RUNS = 4
CONDITIONS = [{"input.mark": i} for i in range(RUNS)]


@pytest.fixture
def built(tmp_path, monkeypatch):
    root = tmp_path / "isicell"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "mainGenerator.py").write_text("")
    (root / "scripts" / "scriptLauncher.py").write_text(LAUNCHER)
    module = root / "simulations" / "sim_ok" / "isicell"
    module.mkdir(parents=True)
    (module / "__init__.py").write_text(BARRIER_ISICELL)

    barrier = tmp_path / "barrier"
    barrier.mkdir()
    monkeypatch.setenv("SWEEP_BARRIER", str(barrier))
    monkeypatch.setenv("SWEEP_EXPECT", str(RUNS))
    monkeypatch.setenv("ISICELL_ROOT", str(root))
    server.SESSION.reset("t")
    server.SESSION.token = "sim_ok"
    yield root
    server.SESSION.reset("t")


def sweep(**kwargs):
    return server.isicell_sweep_run(
        base_params={"input": {"mark": -1}},
        steps=[0],
        conditions=CONDITIONS,
        confirm=True,
        **kwargs,
    )


def test_every_run_of_a_parallel_sweep_is_in_flight_together(built):
    """With workers=1 each run would see only itself and this would read 1."""
    out = sweep(catcher="attribute:arrived", workers=RUNS)
    assert out["failed"] == 0
    assert [r["data"]["0"]["arrived"] for r in out["runs"]] == [[float(RUNS)]] * RUNS


def test_results_stay_in_design_order_when_runs_finish_out_of_order(built):
    """Run 0 finishes last by construction; it must still be reported first."""
    out = sweep(catcher="attribute:mark", workers=RUNS)
    assert [r["ID_CONDITION"] for r in out["runs"]] == list(range(RUNS))
    assert [r["data"]["0"]["mark"] for r in out["runs"]] == [[float(i)] for i in range(RUNS)]


def test_recorded_rows_carry_the_condition_the_value_came_from(built, tmp_path):
    """The same invariant where it matters: on disk, not just in the reply."""
    database = str(tmp_path / "parallel.db")
    out = sweep(catcher="attribute:mark", workers=RUNS, database=database)
    assert out["failed"] == 0

    conn = sqlite3.connect(database)
    rows = conn.execute(
        'SELECT "ID_CONDITION", "mark" FROM attribute_mark ORDER BY "ID_CONDITION"'
    ).fetchall()
    conn.close()
    # An attribute catcher measures every live cell, so the column holds a JSON
    # list -- the storage convention the readers unflatten on the way out.
    assert [(c, json.loads(v)) for c, v in rows] == [(i, [float(i)]) for i in range(RUNS)]


def test_a_sweep_reports_how_many_workers_it_actually_used(built):
    out = sweep(catcher="attribute:mark", workers=RUNS)
    assert out["workers"] == RUNS and out["workersNote"] is None


def test_asking_for_more_workers_than_runs_says_what_it_did_instead(built):
    out = sweep(catcher="attribute:mark", workers=RUNS + 64)
    assert out["workers"] <= RUNS
    assert "reduced from" in out["workersNote"]


def test_a_workers_count_below_one_is_refused(built):
    with pytest.raises(ToolError) as excinfo:
        sweep(catcher="attribute:mark", workers=0)
    assert "at least 1" in str(excinfo.value)


def _await_job(job: str, timeout: float = 60.0) -> dict:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        entry = server.isicell_job_status(job)
        if entry["status"] != "running":
            return entry
        time.sleep(0.05)
    raise AssertionError(f"background job {job} never finished")


def test_a_background_sweep_runs_its_workers_in_parallel_too(built, tmp_path, monkeypatch):
    """The child re-enters this tool, so a dropped `workers` is silently sequential.

    Proved by outcome rather than by reading the payload: the barrier only fills
    if the detached child really has all four runs in flight.
    """
    monkeypatch.setenv("ISICELL_JOBS_DIR", str(tmp_path / "jobs"))
    database = str(tmp_path / "bg_parallel.db")
    out = sweep(
        catcher="attribute:arrived",
        workers=RUNS,
        database=database,
        background=True,
    )
    assert out["background"] is True

    entry = _await_job(out["job"])
    assert entry["status"] == "finished", server.isicell_job_log(out["job"])["lines"]

    conn = sqlite3.connect(database)
    rows = conn.execute(
        'SELECT "ID_CONDITION", "arrived" FROM attribute_arrived ORDER BY "ID_CONDITION"'
    ).fetchall()
    conn.close()
    assert [(c, json.loads(v)) for c, v in rows] == [(i, [float(RUNS)]) for i in range(RUNS)]
