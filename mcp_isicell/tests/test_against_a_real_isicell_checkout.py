"""The real pipeline, against a real ISiCell checkout.

Marked `integration` and deselected by default: these need ISICELL_ROOT to point
at a checkout, and the build ones additionally need the system libraries the
Dockerfile installs (libboost-all-dev, libcgal-dev) plus cmake, make and a C++17
compiler. Those libraries do not have to be installed as root: unpacking the
.deb files into a prefix and pointing CMAKE_PREFIX_PATH, BOOST_ROOT and CPATH at
it satisfies every find_package in core/CMakeLists.txt.

Running a simulation additionally needs swig and the Python packages isicellpy
imports (pandas, tqdm, networkx, plotly, seaborn, matplotlib, scipy, optuna,
SQLAlchemy). The module is a compiled .so, so the interpreter that imports it
must be the one it was built against.

    ISICELL_ROOT=/path/to/ISiCell uv run pytest -m integration

The codegen test needs none of that beyond Python: mainGenerator.py copies the
tree and runs Isigen before it ever invokes cmake, so what the server produces
can be checked even where the C++ cannot be built.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from mcp_isicell.domain.models import BuilderData

pytestmark = pytest.mark.integration

# A cell that ages one unit per step and dies past five, seeded with ten cells.
# Small enough to build quickly, and its trajectory is fully determined: exactly
# ten Alive until age passes 5, then zero.
BEHAVIOUR_MODEL = {
    "cellTypes": ["Common"],
    "cell": {
        "attributes": [{"type": "double", "value": "age", "source": "user"}],
        "States": [{"name": "Alive", "code": "age += 1.0;"}],
        "initializationTransitions": [
            {"from": "CellInstantiation", "destinations": [{"to": "Alive", "condition": ""}]}
        ],
        "Transitions": [
            {"from": "Alive", "destinations": [{"to": "DeadCell", "condition": "age > 5"}]}
        ],
    },
    "scenario": {"codeInit": "for(int i=0;i<10;i++) createCell();"},
    "paramInputs": {"Cell": [{"type": "double", "value": "age"}], "Scenario": []},
}

MODEL = {
    "cellTypes": ["Common"],
    "cell": {
        "attributes": [{"type": "double", "value": "age", "source": "user"}],
        "States": [{"name": "Alive", "code": "age += 1.0;"}],
        "initializationTransitions": [
            {"from": "CellInstantiation", "destinations": [{"to": "Alive", "condition": ""}]}
        ],
        "Transitions": [
            {"from": "Alive", "destinations": [{"to": "DeadCell", "condition": "age > 50"}]}
        ],
    },
    "paramInputs": {"Cell": [{"type": "double", "value": "age"}], "Scenario": []},
}


@pytest.fixture
def checkout() -> Path:
    raw = os.environ.get("ISICELL_ROOT")
    if not raw:
        pytest.skip("set ISICELL_ROOT to an ISiCell checkout to run integration tests")
    root = Path(raw).expanduser()
    if not (root / "scripts" / "mainGenerator.py").is_file():
        pytest.skip(f"{root} has no scripts/mainGenerator.py")
    return root


def run_generator(
    root: Path, token: str, mode: str, model: dict | None = None
) -> subprocess.CompletedProcess:
    """Drive the real mainGenerator.py exactly as the backend does."""
    return subprocess.run(
        ["python3", str(root / "scripts" / "mainGenerator.py"), token, mode],
        input=json.dumps(BuilderData.model_validate(model or MODEL).to_generator_json()),
        capture_output=True,
        text=True,
        cwd=str(root),
        timeout=900,
    )


@pytest.fixture
def generated(checkout: Path):
    """Generate into the real checkout, then clean up after."""
    token = "mcp_integration_test"
    target = checkout / "simulations" / token
    if target.exists():
        shutil.rmtree(target)
    result = run_generator(checkout, token, "sql")
    yield result, target
    if target.exists():
        shutil.rmtree(target)


def test_the_model_authored_here_becomes_the_expected_generated_cpp(generated):
    """Every claim the validator makes about codegen, confirmed by running it."""
    _, target = generated
    model_hpp = (target / "src" / "core" / "Model.hpp").read_text()
    primo = (target / "src" / "cells" / "PrimoCell.hpp").read_text()

    # A declared state becomes an enum member.
    assert "enum State { Alive}" in model_hpp
    # Its code lands in the behave() switch.
    assert "case State::Alive" in primo and "age += 1.0;" in primo
    # DeadCell compiles to die(), not to a State value -- the magic-name rule.
    assert "if(age > 50) this->die();" in primo
    assert "State::DeadCell" not in primo
    # A user attribute is emitted, with the Cell.Common fallback.
    assert "double age;" in primo
    assert 'cellConfig["Common"]["age"]' in model_hpp


def test_the_generator_records_the_checksum_of_what_it_was_given(generated):
    _, target = generated
    assert (target / "checksum.txt").read_text().strip()


def test_a_generated_tree_carries_the_pieces_a_build_needs(generated):
    _, target = generated
    for expected in ("CMakeLists.txt", "src", "external/MecaCell", "config/runsParams"):
        assert (target / expected).exists(), expected


def test_no_isigen_directives_survive_into_the_generated_source(generated):
    """keepIsigen=False, so a leftover block would be a template that never fired."""
    _, target = generated
    for path in (target / "src").rglob("*.hpp"):
        assert "isigen:insert@" not in path.read_text(), path


@pytest.mark.skipif(
    shutil.which("cmake") is None, reason="cmake is needed to build the generated project"
)
def test_the_generated_project_compiles(generated):
    """The whole pipeline. Needs Boost and CGAL, which the Dockerfile installs."""
    result, target = generated
    if result.returncode != 0:
        tail = (result.stdout or "")[-2000:]
        if "Could NOT find" in tail:
            pytest.skip(f"a system dependency is missing:\n{tail[-400:]}")
        pytest.fail(f"generation failed:\n{tail}")
    assert (target / "build" / "console").is_file()


@pytest.fixture
def simulation(checkout: Path):
    """Build the python-mode tree: the SWIG module a run actually imports."""
    if shutil.which("cmake") is None or shutil.which("swig") is None:
        pytest.skip("cmake and swig are needed to build the python module")
    token = "mcp_integration_sim"
    target = checkout / "simulations" / token
    if target.exists():
        shutil.rmtree(target)
    result = run_generator(checkout, token, "python", BEHAVIOUR_MODEL)
    if result.returncode != 0:
        tail = (result.stdout or "")[-2000:]
        if "Could NOT find" in tail:
            pytest.skip(f"a system dependency is missing:\n{tail[-400:]}")
        pytest.fail(f"python-mode generation failed:\n{tail}")
    yield target
    if target.exists():
        shutil.rmtree(target)


def test_the_python_mode_build_produces_the_importable_module(simulation):
    """mainGenerator.py:174-175 copies the built .so and its wrapper into isicell/."""
    assert (simulation / "isicell" / "_isiCellPy.so").is_file()
    assert (simulation / "isicell" / "isiCellPy.py").is_file()


def test_a_generated_run_script_drives_a_real_simulation(simulation):
    """The whole pipeline: a model authored here, simulated, measured as data.

    The trajectory is determined by the model -- ten cells that age one unit per
    step and die past five -- so this asserts semantics, not just that it ran.
    """
    from mcp_isicell.backend.simscript import RunSpec, render

    script = simulation / "generated_run.py"
    script.write_text(
        render(
            RunSpec(
                params={"input": {"Cell": {"Common": {"age": 0.0}}, "Scenario": {}}},
                steps=[0, 4, 8],
                catcher="countState",
                seed=42,
            )
        )
    )
    proc = subprocess.run(
        [os.environ.get("ISICELL_PYTHON", "python3"), str(script)],
        capture_output=True,
        text=True,
        cwd=str(simulation),
        timeout=900,
    )
    if "ModuleNotFoundError" in (proc.stderr or ""):
        pytest.skip(f"isicellpy's own dependencies are missing:\n{proc.stderr[-300:]}")
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["ok"], payload
    counts = payload["data"]
    # Seeded by the scenario, still alive before the transition fires.
    assert counts["0"]["Alive"] == 10
    assert counts["4"]["Alive"] == 10
    # age > 5 compiles to die(), so the population is gone by step 8.
    assert counts["8"]["Alive"] == 0


# A cell function with an undeclared identifier in it. The function is what
# carries metaDataDebug markers (PrimoCell.hpp:182), so this is the case the
# diagnostic mapping exists for.
BROKEN_FUNCTION_MODEL = {
    "cellTypes": ["Common"],
    "cell": {
        "attributes": [{"type": "double", "value": "age", "source": "user"}],
        "States": [{"name": "Alive", "code": "age += 1.0;"}],
        "initializationTransitions": [
            {"from": "CellInstantiation", "destinations": [{"to": "Alive", "condition": ""}]}
        ],
        "Transitions": [
            {"from": "Alive", "destinations": [{"to": "DeadCell", "condition": "age > 5"}]}
        ],
        "functions": {"f1": {"code": "void boom() {\n  age += notAThing;\n}"}},
    },
    "paramInputs": {"Cell": [{"type": "double", "value": "age"}], "Scenario": []},
}


@pytest.fixture
def backend(checkout: Path, monkeypatch):
    """The real LocalBackend, not the stub the offline suite uses."""
    from mcp_isicell.backend.local import backend_from_env

    monkeypatch.setenv("ISICELL_ROOT", str(checkout))
    made = backend_from_env()
    assert made is not None
    return made


def test_a_model_with_a_cell_function_compiles(backend, checkout):
    """The templates index functions as strings; an object kills Isigen outright."""
    if shutil.which("cmake") is None:
        pytest.skip("cmake is needed to build the generated project")
    model = json.loads(json.dumps(BROKEN_FUNCTION_MODEL))
    model["cell"]["functions"]["f1"]["code"] = "void grow() {\n  age += 1.0;\n}"
    model["cell"]["States"][0]["code"] = "grow();"
    target = checkout / "simulations" / "mcp_integration_fn_ok"
    try:
        result = backend.compile(
            BuilderData.model_validate(model),
            token="mcp_integration_fn_ok",
            build_type="sql",
            confirm=True,
        )
        if not result.ok and any("Could NOT find" in line for line in result.stdout_tail):
            pytest.skip("a system dependency is missing")
        assert result.ok, result.stdout_tail[-10:] + result.stderr_tail[-10:]
    finally:
        if target.exists():
            shutil.rmtree(target)


def test_a_compiler_error_is_reported_against_the_function_the_user_wrote(backend, checkout):
    """The point of metaInfo: a g++ line in generated source becomes `boom`/`f1`."""
    if shutil.which("cmake") is None:
        pytest.skip("cmake is needed to build the generated project")
    target = checkout / "simulations" / "mcp_integration_diag"
    try:
        result = backend.compile(
            BuilderData.model_validate(BROKEN_FUNCTION_MODEL),
            token="mcp_integration_diag",
            build_type="sql",
            confirm=True,
        )
        if any("Could NOT find" in line for line in result.stdout_tail):
            pytest.skip("a system dependency is missing")
        assert not result.ok
        # Isigen emitted the map at all -- empty means the payload shape is wrong.
        assert result.meta_info, "no metaInfo: functions are probably not bare strings"
        owned = [d for d in result.diagnostics if d.get("model_element")]
        assert owned, result.diagnostics
        assert owned[0]["model_element"] == "boom"
        assert owned[0]["idFunc"] == "f1"
    finally:
        if target.exists():
            shutil.rmtree(target)


@pytest.fixture
def tools(checkout: Path, monkeypatch):
    """The MCP tool surface, pointed at the real checkout.

    Everything above is backend-level. This exercises what an LLM actually
    calls: the session, the validation gate on compiling, the token rebinding
    and the run tool.
    """
    from mcp_isicell import server as sv

    monkeypatch.setenv("ISICELL_ROOT", str(checkout))
    sv.isicell_model_new(name="integration")
    return sv


def _author_the_ageing_model(sv) -> None:
    """BEHAVIOUR_MODEL, built one tool call at a time."""
    sv.isicell_model_add_state("Alive", "age += 1.0;")
    sv.isicell_model_add_transition("CellInstantiation", "Alive", "")
    sv.isicell_model_add_transition("Alive", "DeadCell", "age > 5")
    sv.isicell_model_add_attribute("cell", "double", "age", is_param=True)
    sv.isicell_model_set_scenario_code(init="for(int i=0;i<10;i++) createCell();")


def test_a_model_authored_through_the_tools_validates_clean(tools):
    """If this drifts, every later tool-level test is testing a broken model."""
    _author_the_ageing_model(tools)
    report = tools.isicell_model_validate()
    assert report["ok"], report["findings"]


def test_the_tool_loop_compiles_and_simulates_against_real_isicell(tools, checkout):
    """Author, compile, run -- the whole loop the server's instructions describe."""
    if shutil.which("cmake") is None or shutil.which("swig") is None:
        pytest.skip("cmake and swig are needed to build the python module")
    _author_the_ageing_model(tools)
    token = "mcp_integration_tools"
    target = checkout / "simulations" / token
    try:
        built = tools.isicell_build_compile(type="python", token=token, confirm=True)
        if not built["ok"] and any("Could NOT find" in x for x in built["stdout_tail"]):
            pytest.skip("a system dependency is missing")
        assert built["ok"], built["stdout_tail"][-10:]
        # A successful compile rebinds the session's token, so the run needs none.
        assert tools.isicell_model_describe()["model"]["token"] == token

        from mcp.server.mcpserver.exceptions import ToolError

        try:
            result = tools.isicell_run_simulate(
                params={"input": {"Cell": {"Common": {"age": 0.0}}, "Scenario": {}}},
                steps=[0, 4, 8],
                catcher="countState",
                seed=42,
            )
        except ToolError as exc:
            # The run needs the interpreter the .so was built against; say which.
            pytest.skip(f"the run interpreter is not usable: {exc}")
        assert result["ok"], result["stderr_tail"]
        assert result["data"]["0"]["Alive"] == 10
        assert result["data"]["4"]["Alive"] == 10
        assert result["data"]["8"]["Alive"] == 0
    finally:
        if target.exists():
            shutil.rmtree(target)


def test_compiling_a_broken_model_is_refused_before_any_build_runs(tools):
    """The validation gate: a model with errors must not cost a three-minute build."""
    tools.isicell_model_add_state("Alive", "age += 1.0;")
    # No transition out of CellInstantiation: E_NO_INIT_TRANSITION.
    with pytest.raises(Exception) as caught:
        tools.isicell_build_compile(type="python", token="mcp_integration_never", confirm=True)
    assert "E_NO_INIT_TRANSITION" in str(caught.value)


def test_a_scored_sweep_ranks_real_simulations(tools, checkout, tmp_path):
    """M5-M7 against real ISiCell: sample, simulate, score, rank, read back.

    Starting `age` decides whether a cell survives to step 4, so the reference
    {0: 10, 4: 10, 8: 0} is matched only by the low sample -- the ranking is a
    fact about the model, not just a number the writer round-tripped.
    """
    if shutil.which("cmake") is None or shutil.which("swig") is None:
        pytest.skip("cmake and swig are needed to build the python module")
    from mcp.server.mcpserver.exceptions import ToolError

    _author_the_ageing_model(tools)
    token = "mcp_integration_sweep"
    target = checkout / "simulations" / token
    database = str(tmp_path / "sweep.db")
    try:
        built = tools.isicell_build_compile(type="python", token=token, confirm=True)
        if not built["ok"] and any("Could NOT find" in x for x in built["stdout_tail"]):
            pytest.skip("a system dependency is missing")
        assert built["ok"], built["stdout_tail"][-10:]
        try:
            swept = tools.isicell_sweep_run(
                base_params={"input": {"Cell": {"Common": {"age": 0.0}}, "Scenario": {}}},
                steps=[0, 4, 8],
                parameters=[{"name": "input.Cell.Common.age", "low": 0.0, "high": 3.0}],
                n_samples=3,
                catcher="countState",
                seed=1,
                database=database,
                overwrite=True,
                reference={"0": 10, "4": 10, "8": 0},
                column="Alive",
                metrics=["rmse"],
                confirm=True,
            )
        except ToolError as exc:
            pytest.skip(f"the run interpreter is not usable: {exc}")
        assert swept["failed"] == 0, swept
        assert all(r["ok"] for r in swept["runs"]), swept["runs"]

        ranked = tools.isicell_results_best(database, error_name="rmse", n=3)
        assert len(ranked) == 3
        # Ranking is by ascending error, and the best one reproduces the reference.
        assert ranked[0]["score"] == pytest.approx(0.0)
        assert ranked[0]["score"] <= ranked[-1]["score"]

        # The winning parameters come back in the shape a run takes.
        winner = tools.isicell_results_parameters(database, ranked[0]["ID_PARAMETER"])
        assert "age" in winner["input"]["Cell"]["Common"]
    finally:
        if target.exists():
            shutil.rmtree(target)
