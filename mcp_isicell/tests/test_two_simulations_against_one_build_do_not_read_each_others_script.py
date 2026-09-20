"""Concurrent runs against one build are isolated.

Runs overlap in normal use: two background sweeps get distinct job tokens and
start side by side, and isicell_run_simulate can be called while a sweep is in
flight. They share a build directory, so the generated script cannot have a
fixed name -- the second writer would win and the first launcher would exec the
other run's parameters, reporting them under its own index.

The fake launcher here sleeps before exec'ing the script, which widens the
window every overlapping run really has into one a test can rely on.
"""

from __future__ import annotations

import textwrap
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from mcp_isicell.backend.local import LocalBackend
from mcp_isicell.backend.simscript import RunSpec
from mcp_isicell.config import IsicellPaths

# Exec the script only after every sibling has had time to write its own.
SLOW_LAUNCHER = textwrap.dedent(
    """
    import runpy, sys, time
    sys.path.insert(0, sys.argv[1])
    time.sleep(0.4)
    runpy.run_path(sys.argv[2], run_name="__main__")
    """
)

# cells.get() returns the parameter this run was constructed with, so a
# measurement is a fingerprint of the script that produced it.
ECHOING_ISICELL = textwrap.dedent(
    """
    class _Cells:
        def __init__(self, simu): self._s = simu
        def get(self, name): return [float(self._s.params["input"]["mark"])]

    class Simu:
        def __init__(self, params, seed=None, benchmark=None):
            self.params = params
            self.cells = _Cells(self)
        def getCurrentStep(self): return 0
        def update(self, n=1): pass
    """
)

RUNS = 4


@pytest.fixture
def build(tmp_path: Path) -> Path:
    root = tmp_path / "isicell"
    (root / "scripts").mkdir(parents=True)
    # require() looks for this; nothing here compiles.
    (root / "scripts" / "mainGenerator.py").write_text("")
    (root / "scripts" / "scriptLauncher.py").write_text(SLOW_LAUNCHER)
    module = root / "simulations" / "sim_shared" / "isicell"
    module.mkdir(parents=True)
    (module / "__init__.py").write_text(ECHOING_ISICELL)
    return root


def test_each_concurrent_run_measures_its_own_parameters(build: Path):
    """The defect this prevents is silent: every run would report the last one's."""
    backend = LocalBackend(IsicellPaths(build))

    def run(mark: int) -> dict:
        spec = RunSpec(
            params={"input": {"mark": mark}}, steps=[0], catcher="attribute:mark"
        )
        return backend.simulate("sim_shared", spec)

    with ThreadPoolExecutor(max_workers=RUNS) as pool:
        results = list(pool.map(run, range(RUNS)))

    measured = []
    for mark, result in enumerate(results):
        assert result["ok"], result["stderr"]
        measured.append(result["measurements"]["data"]["0"]["mark"])
    assert measured == [[float(m)] for m in range(RUNS)]


def test_concurrent_runs_leave_no_script_behind(build: Path):
    """Each run removes its own script and no one else's."""
    backend = LocalBackend(IsicellPaths(build))

    def run(mark: int) -> dict:
        return backend.simulate(
            "sim_shared",
            RunSpec(params={"input": {"mark": mark}}, steps=[0], catcher="attribute:mark"),
        )

    with ThreadPoolExecutor(max_workers=RUNS) as pool:
        list(pool.map(run, range(RUNS)))

    assert list((build / "simulations" / "sim_shared").glob("_mcp_run*.py")) == []
