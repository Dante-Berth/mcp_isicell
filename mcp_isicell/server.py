"""The MCP surface: ISiCell as an API an LLM can call.

Authoring tools work with no ISiCell checkout at all -- they mutate a
server-held model and validate it in pure Python. Only compiling and running
need ISICELL_ROOT.

Every authoring tool returns fresh findings alongside the model summary, so the
caller sees a mistake at the moment it makes it rather than three minutes into a
build.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .backend.local import BUILD_TYPES, LocalBackend, RunSpec
from .backend.simscript import describe_catchers
from .config import IsicellPaths
from .domain import mutations as mut
from .domain.models import BuilderData
from .domain.modules import Catalogue
from .domain.validation import validate
from .errors import IsicellError, NotConfigured, ValidationRefused
from .explore.sampling import Parameter, assign, bounds, sample
from .results.reader import ResultsDatabase
from .session import ModelSession

mcp = MCPServer("isicell")
SESSION = ModelSession()


def _tool(fn):
    """Surface deliberate IsicellErrors as tool errors, message intact."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except IsicellError as exc:
            raise ToolError(str(exc)) from exc
        except (ValueError, KeyError) as exc:
            raise ToolError(str(exc)) from exc

    return wrapper


# Bundled so module names and dependencies resolve with no checkout present.
BUNDLED_MODULES = Path(__file__).parent / "tests" / "fixtures" / "officialModules.json"


def _paths() -> IsicellPaths | None:
    return IsicellPaths.from_env()


def _backend() -> LocalBackend:
    paths = _paths()
    if paths is None:
        raise NotConfigured(
            "ISICELL_ROOT is not set. Authoring and validation work without it; "
            "compiling and running need an ISiCell checkout."
        )
    return LocalBackend(paths)


def _catalogue() -> Catalogue:
    paths = _paths()
    if paths and paths.official_modules_json.is_file():
        return Catalogue.from_path(paths.official_modules_json)
    return Catalogue.from_path(BUNDLED_MODULES)


def _installed() -> set[str] | None:
    paths = _paths()
    return paths.installed_modules() if paths else None


def _report(model: BuilderData | None = None) -> dict[str, Any]:
    """The standard reply: what the model is now, and what is wrong with it."""
    target = SESSION.model if model is None else model
    report = validate(target, _catalogue(), _installed())
    return {
        "model": SESSION.summary(),
        "ok": report.ok,
        "findings": [
            {
                "severity": f.severity,
                "code": f.code,
                "path": f.path,
                "message": f.message,
                "suggestion": f.suggestion,
            }
            for f in report.findings
        ],
    }


def _apply(new_model: BuilderData) -> dict[str, Any]:
    SESSION.replace(new_model)
    return _report()


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


@mcp.tool()
@_tool
def isicell_status() -> dict[str, Any]:
    """Whether an ISiCell checkout is configured, and what it can do."""
    paths = _paths()
    if paths is None:
        return {
            "configured": False,
            "note": "Set ISICELL_ROOT to enable compiling and running. "
            "Authoring and validation work without it.",
            "modules_known": _catalogue().names(),
        }
    installed = paths.installed_modules()
    known = _catalogue().names()
    return {
        "configured": paths.exists(),
        "root": str(paths.root),
        "modules_known": known,
        "modules_installed": sorted(installed),
        "modules_missing_on_disk": sorted(
            {m.DirName for m in _catalogue().modules} - installed
        ),
        "note": "Modules missing on disk are uninitialised git submodules; run "
        "`git submodule update --init --recursive` in the checkout.",
    }


@mcp.tool()
@_tool
def isicell_list_modules() -> list[dict[str, Any]]:
    """The official cell modules, with their types and dependencies."""
    installed = _installed()
    out = []
    for m in _catalogue().modules:
        out.append(
            {
                "name": m.Name,
                "dirName": m.DirName,
                "description": m.Description,
                "type": m.Type,
                "dependencies": list(m.Dependencies),
                "space": m.space(),
                "installed": None if installed is None else m.DirName in installed,
            }
        )
    return out


@mcp.tool()
@_tool
def isicell_list_builds() -> list[dict[str, Any]]:
    """Existing simulation builds and whether each one compiled."""
    return _backend().list_builds()


# --------------------------------------------------------------------------
# Authoring -- all pure, no checkout needed
# --------------------------------------------------------------------------


@mcp.tool()
@_tool
def isicell_model_new(name: str = "untitled") -> dict[str, Any]:
    """Start a new, empty model. Discards whatever was being authored."""
    SESSION.reset(name)
    return _report()


@mcp.tool()
@_tool
def isicell_model_describe() -> dict[str, Any]:
    """The current model's structure and its validation findings."""
    return _report()


@mcp.tool()
@_tool
def isicell_model_export() -> dict[str, Any]:
    """The full builderData document, exactly as the generator receives it."""
    return SESSION.model.to_generator_json()


@mcp.tool()
@_tool
def isicell_model_import(model: dict[str, Any], name: str = "imported") -> dict[str, Any]:
    """Load a complete builderData document as the current model."""
    SESSION.name = name
    SESSION.token = None
    return _apply(BuilderData.model_validate(model))


@mcp.tool()
@_tool
def isicell_model_save(path: str) -> dict[str, Any]:
    """Write the current model to a JSON file."""
    target = Path(path).expanduser()
    target.write_text(json.dumps(SESSION.model.to_generator_json(), indent=2))
    return {"written": str(target)}


@mcp.tool()
@_tool
def isicell_model_load(path: str) -> dict[str, Any]:
    """Load a model from a JSON file. Accepts a demo file with a finalJson key."""
    payload = json.loads(Path(path).expanduser().read_text())
    if "finalJson" in payload:  # demo files wrap the model
        payload = payload["finalJson"]
    SESSION.name = Path(path).stem
    SESSION.token = None
    return _apply(BuilderData.model_validate(payload))


@mcp.tool()
@_tool
def isicell_model_validate() -> dict[str, Any]:
    """Check the model for anything that would break codegen or the build."""
    return _report()


@mcp.tool()
@_tool
def isicell_model_add_state(name: str, code: str = "") -> dict[str, Any]:
    """Declare a cell state. `code` is C++ run every step while in that state.

    Do not use CellInstantiation or DeadCell as names: they compile to init()
    and die() rather than to enum values.
    """
    return _apply(mut.add_state(SESSION.model, name, code))


@mcp.tool()
@_tool
def isicell_model_set_state_code(name: str, code: str) -> dict[str, Any]:
    """Replace a state's behaviour code."""
    return _apply(mut.set_state_code(SESSION.model, name, code))


@mcp.tool()
@_tool
def isicell_model_remove_state(name: str) -> dict[str, Any]:
    """Remove a state, and every transition that referenced it."""
    return _apply(mut.remove_state(SESSION.model, name))


@mcp.tool()
@_tool
def isicell_model_add_transition(source: str, target: str, condition: str = "") -> dict[str, Any]:
    """Add source -> target, guarded by a C++ boolean expression.

    Use CellInstantiation as the source for a newborn cell's first transition,
    and DeadCell as the target to kill the cell. An empty condition is the
    fallback branch; declaring two from one source makes the second dead code.
    """
    return _apply(mut.add_transition(SESSION.model, source, target, condition))


@mcp.tool()
@_tool
def isicell_model_add_attribute(
    scope: str,
    type: str,
    name: str,
    is_param: bool = False,
    is_recorded: bool = False,
) -> dict[str, Any]:
    """Declare an attribute on the cell or the scenario.

    scope is 'cell' or 'scenario'. is_param makes it settable at run time;
    is_recorded makes it a database column (cell scope only).
    """
    return _apply(
        mut.add_attribute(
            SESSION.model, scope, type, name, is_param=is_param, is_recorded=is_recorded
        )
    )


@mcp.tool()
@_tool
def isicell_model_add_function(scope: str, key: str, code: str) -> dict[str, Any]:
    """Add a C++ function to the cell or the scenario.

    Write a full definition -- `void divide() { ... }`. Codegen parses the name
    out of the code and fails opaquely if it cannot find one.
    """
    return _apply(mut.add_function(SESSION.model, scope, key, code))


@mcp.tool()
@_tool
def isicell_model_select_module(name: str) -> dict[str, Any]:
    """Select an official cell module by its Name (not its DirName)."""
    return _apply(mut.select_module(SESSION.model, name))


@mcp.tool()
@_tool
def isicell_model_deselect_module(name: str) -> dict[str, Any]:
    """Remove a module from the selection."""
    return _apply(mut.deselect_module(SESSION.model, name))


@mcp.tool()
@_tool
def isicell_model_set_scenario_code(init: str | None = None, loop: str | None = None) -> dict[str, Any]:
    """Set the scenario's C++: `init` runs once at start, `loop` every step."""
    return _apply(mut.set_scenario_code(SESSION.model, init=init, loop=loop))


@mcp.tool()
@_tool
def isicell_model_set_cell_types(types: list[str]) -> dict[str, Any]:
    """Declare the cell types. Empty means a single implicit 'Common' type."""
    return _apply(mut.set_cell_types(SESSION.model, types))


@mcp.tool()
@_tool
def isicell_model_add_enum(name: str, values: list[str]) -> dict[str, Any]:
    """Declare a C++ enum usable as an attribute type."""
    return _apply(mut.add_enum(SESSION.model, name, values))


# --------------------------------------------------------------------------
# Build and run
# --------------------------------------------------------------------------


@mcp.tool()
@_tool
def isicell_build_compile(
    type: str = "python",
    token: str | None = None,
    force: bool = False,
    confirm: bool = False,
) -> dict[str, Any]:
    """Generate C++ from the current model and compile it. Takes minutes.

    type: 'python' for the importable module, 'sql' for the console binary,
    'all' for both, 'debug' to run under GDB.

    Refuses a model with error-level findings unless force=True. Reusing an
    existing token deletes that build, so it needs confirm=True.
    """
    if type not in BUILD_TYPES:
        raise ValueError(f"type must be one of {BUILD_TYPES}")
    report = validate(SESSION.model, _catalogue(), _installed())
    if not report.ok and not force:
        raise ValidationRefused(
            "Refusing to compile: the model has errors that would fail the build. "
            "Fix them, or pass force=True if you believe the validator is wrong. "
            + "; ".join(f"{f.code} at {f.path}" for f in report.errors)
        )
    result = _backend().compile(SESSION.model, type, token=token, confirm=confirm)
    if result.ok:
        SESSION.token = result.token
    return {
        "token": result.token,
        "ok": result.ok,
        "returncode": result.returncode,
        "phase": result.phase,
        "percent": result.percent,
        "diagnostics": result.diagnostics,
        "stdout_tail": result.stdout_tail,
        "stderr_tail": result.stderr_tail,
        "build_dir": result.build_dir,
    }


@mcp.tool()
@_tool
def isicell_build_checksum(token: str) -> dict[str, Any]:
    """The md5 of the model a build was made from, to decide on a rebuild."""
    return {"token": token, "checksum": _backend().checksum(token)}


@mcp.tool()
@_tool
def isicell_build_remove(token: str, confirm: bool = False) -> dict[str, Any]:
    """Delete a build directory. Irreversible, so it needs confirm=True."""
    return _backend().remove_build(token, confirm=confirm)


@mcp.tool()
@_tool
def isicell_run_script(script_path: str, token: str | None = None) -> dict[str, Any]:
    """Run a Python script against a compiled model.

    The build is placed on sys.path, so the script can `from isicell import *`
    and construct Simu(params). Use the token from a previous compile, or omit
    it to use the current session's build.
    """
    use = token or SESSION.token
    if not use:
        raise IsicellError("No build token. Compile first, or pass one explicitly.")
    return _backend().run_script(use, Path(script_path).expanduser())


# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------


@mcp.tool()
@_tool
def isicell_list_catchers() -> list[dict[str, Any]]:
    """The measurements a run can record, and what each returns.

    A run names one of these. Arbitrary Python is deliberately not accepted, so
    a measurement outside this set means writing a script and using
    isicell_run_script.
    """
    return describe_catchers()


@mcp.tool()
@_tool
def isicell_run_simulate(
    params: dict[str, Any],
    steps: list[int],
    catcher: str = "countState",
    token: str | None = None,
    seed: int | None = None,
) -> dict[str, Any]:
    """Run a compiled model and record a measurement at each step.

    `params` is runParams, not a model: {"input": {"Cell": {...}, "Scenario":
    {...}}}. Cell.Common must be present -- the engine falls back to it for any
    value a cell type does not name.

    `steps` are cumulative step indices to measure at. `catcher` names a
    measurement from isicell_list_catchers. Needs a build compiled with
    type='python' or 'all'.
    """
    use = token or SESSION.token
    if not use:
        raise IsicellError("No build token. Compile first, or pass one explicitly.")
    result = _backend().simulate(use, RunSpec(params=params, steps=steps, catcher=catcher, seed=seed))
    measurements = result.get("measurements")
    if measurements and not measurements.get("ok", True):
        raise IsicellError(f"the simulation failed: {measurements.get('error')}")
    return {
        "token": use,
        "ok": result["ok"],
        "data": (measurements or {}).get("data"),
        "stderr_tail": (result.get("stderr") or "").splitlines()[-20:],
    }


# --------------------------------------------------------------------------
# Explore
# --------------------------------------------------------------------------


@mcp.tool()
@_tool
def isicell_sweep_preview(
    parameters: list[dict[str, Any]], n_samples: int = 10, seed: int | None = None
) -> dict[str, Any]:
    """Show the points a Latin hypercube sweep would run. Costs nothing.

    Each parameter is {"name": "input.Cell.Common.x", "kind": "float"|"int"|
    "categorical", "low":, "high":, "choices":, "log":, "step":}. `name` is a
    dotted path into the run params.

    Latin hypercube sampling covers every axis evenly at any number of
    dimensions, which plain random sampling stops doing as axes are added.
    """
    space = [Parameter.from_dict(p) for p in parameters]
    points = sample(space, n_samples, seed=seed)
    return {"bounds": bounds(space), "n_samples": len(points), "points": points}


@mcp.tool()
@_tool
def isicell_sweep_run(
    base_params: dict[str, Any],
    parameters: list[dict[str, Any]],
    steps: list[int],
    n_samples: int = 10,
    catcher: str = "countState",
    token: str | None = None,
    seed: int | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Run one simulation per sampled point. Takes n_samples times a run.

    Preview with isicell_sweep_preview first. Needs confirm=True, because this
    is the tool that spends real compute.
    """
    use = token or SESSION.token
    if not use:
        raise IsicellError("No build token. Compile first, or pass one explicitly.")
    space = [Parameter.from_dict(p) for p in parameters]
    points = sample(space, n_samples, seed=seed)
    if not confirm:
        return {
            "ran": False,
            "n_samples": len(points),
            "points": points,
            "note": "Preview only. Pass confirm=True to run these; it costs "
            f"{len(points)} simulations.",
        }
    backend = _backend()
    runs: list[dict[str, Any]] = []
    for index, point in enumerate(points):
        merged = assign(base_params, point)
        outcome = backend.simulate(
            use, RunSpec(params=merged, steps=steps, catcher=catcher, seed=seed)
        )
        measured = outcome.get("measurements") or {}
        runs.append(
            {
                "index": index,
                "point": point,
                "ok": outcome["ok"] and measured.get("ok", False),
                "data": measured.get("data"),
                "error": measured.get("error"),
            }
        )
    return {"ran": True, "token": use, "n_samples": len(points), "runs": runs}


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------


@mcp.tool()
@_tool
def isicell_results_summarize(database: str) -> dict[str, Any]:
    """What a results database holds: tables, row counts, error and parameter names."""
    with ResultsDatabase(database) as db:
        return db.summarize()


@mcp.tool()
@_tool
def isicell_results_get(
    database: str,
    table: str,
    filters: list[dict[str, Any]] | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Rows of one table.

    `filters` are structured, not SQL: [{"column": "ID_PARAMETER", "op": "eq",
    "value": 3}]. Operators are eq, ne, lt, le, gt, ge. Parameter rows come back
    nested, in the shape a run takes.
    """
    with ResultsDatabase(database) as db:
        return db.get(table, filters=filters, limit=limit)


@mcp.tool()
@_tool
def isicell_results_best(
    database: str, error_name: str, n: int = 5, for_each_condition: bool = False
) -> list[dict[str, Any]]:
    """The parameter sets scoring lowest on one error column.

    Averages the error over conditions and replicates first, which is what makes
    two parameter sets comparable. Set for_each_condition to rank within each
    condition instead.
    """
    with ResultsDatabase(database) as db:
        return db.best(error_name, n=n, for_each_condition=for_each_condition)


@mcp.tool()
@_tool
def isicell_results_pareto(database: str, error_names: list[str]) -> list[dict[str, Any]]:
    """Parameter sets no other set beats on every error at once.

    With one error this is just the minimum; with several it is the trade-off
    front, where improving one error costs another.
    """
    with ResultsDatabase(database) as db:
        return db.pareto(error_names)


@mcp.tool()
@_tool
def isicell_results_parameters(database: str, id_parameter: int) -> dict[str, Any]:
    """The nested parameter set behind an ID, ready to pass to isicell_run_simulate."""
    with ResultsDatabase(database) as db:
        return db.parameters_for(id_parameter)


def main() -> None:
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
