"""A run fails in another interpreter, so its failure arrives as a traceback.

The caller is an LLM. `ModuleNotFoundError: No module named 'pandas'` buried in
twelve lines of runpy frames does not say which interpreter is wrong or what to
do, so the tool layer turns the common causes into a sentence.
"""

import pytest

from mcp_isicell.server import _diagnose_run_failure

TRACEBACK = """Traceback (most recent call last):
  File "/x/scripts/scriptLauncher.py", line 11, in <module>
    runpy.run_path(sys.argv[2], run_name='__main__')
  File "<frozen runpy>", line 287, in run_path
  File "/x/simulations/t/isicell/isiCellPy.py", line 12, in <module>
    import pandas as pd
ModuleNotFoundError: No module named 'pandas'
"""


def test_a_missing_dependency_names_the_interpreter_and_the_module(monkeypatch):
    monkeypatch.setenv("ISICELL_PYTHON", "/opt/venv/bin/python")
    message = _diagnose_run_failure(TRACEBACK)
    assert "'pandas'" in message
    assert "/opt/venv/bin/python" in message
    assert "ISICELL_PYTHON" in message


def test_a_missing_isicell_module_points_at_the_build_type():
    """Not a dependency problem: the build simply has no Python module."""
    message = _diagnose_run_failure("ModuleNotFoundError: No module named 'isicell'")
    assert "type='python'" in message
    assert "ISICELL_PYTHON" not in message


@pytest.mark.parametrize(
    "stderr",
    ["ImportError: /x/_isiCellPy.so: undefined symbol: _ZN6Python",
     "ImportError: /x/_isiCellPy.so: invalid ELF header"],
)
def test_an_abi_mismatch_is_reported_as_the_wrong_interpreter(stderr):
    """The .so is built against one interpreter; loading it under another fails."""
    message = _diagnose_run_failure(stderr)
    assert "compiled against one specific interpreter" in message


def test_an_unrecognised_failure_still_carries_the_last_lines():
    """Never swallow the evidence for a cause we did not anticipate."""
    message = _diagnose_run_failure("something\nunexpected\nRuntimeError: boom")
    assert "RuntimeError: boom" in message


def test_silence_is_reported_as_silence():
    assert "printed nothing" in _diagnose_run_failure("")
