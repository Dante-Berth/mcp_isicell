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
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import __version__
from .backend.jobs import JobStore
from .backend.local import BUILD_TYPES, LocalBackend, RunSpec
from .backend.simscript import describe_catchers
from .config import IsicellPaths, isicell_python, jobs_directory, session_path
from .domain import mutations as mut
from .domain.models import BuilderData
from .domain.modules import Catalogue
from .domain.validation import validate
from .errors import IsicellError, NotConfigured, ValidationRefused
from .explore.design import plan
from .explore.metrics import describe_metrics, score
from .explore.refine import refine
from .explore.sampling import Parameter
from .results.optuna_archive import OptunaArchive
from .results.reader import ResultsDatabase
from .results.writer import ResultsWriter, measurement_rows
from .session import ModelSession

mcp = MCPServer(
    "isicell",
    version=__version__,
    instructions=(
        "Drive ISiCell, a platform for agent-based cell biology models. A model is a "
        "state machine per cell: states carry C++ behaviour, transitions carry C++ "
        "conditions.\n\n"
        "Start with isicell_status to see whether an ISiCell checkout is configured. "
        "Authoring and validation need nothing installed; compiling and running need "
        "ISICELL_ROOT.\n\n"
        "The usual loop: isicell_model_new, add states and transitions, "
        "isicell_model_validate until clean, isicell_build_compile, isicell_run_simulate. "
        "Every authoring call returns fresh findings, so fix what it reports rather than "
        "compiling and reading C++ errors.\n\n"
        "CellInstantiation and DeadCell are not states: they compile to init() and die(). "
        "Every model needs a transition out of the first and usually one into the second."
    ),
)
SESSION = ModelSession.restored(session_path())


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


def _jobs() -> JobStore:
    return JobStore(jobs_directory())


def _backend() -> LocalBackend:
    paths = _paths()
    if paths is None:
        raise NotConfigured(
            "ISICELL_ROOT is not set. Authoring and validation work without it; "
            "compiling and running need an ISiCell checkout."
        )
    return LocalBackend(paths, python=isicell_python())


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
    SESSION.set_token(None)
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
    SESSION.set_token(None)
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
        SESSION.set_token(result.token)
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


# A simulation is a separate process (backend.simulate spawns scriptLauncher),
# so the pool is threads around a blocking wait rather than processes: the GIL
# is released for the whole subprocess.run, and no model has to be picklable.
# Both branches below are lazy and yield in design order, so which runs finish
# first never reaches the results database.
MAX_WORKERS_NOTE = "workers is capped by the run count and by the CPU count."


def _effective_workers(requested: int, total_runs: int) -> tuple[int, str | None]:
    """How many runs to have in flight, and why that is not what was asked for."""
    if requested < 1:
        raise IsicellError("workers must be at least 1.")
    cpus = os.cpu_count() or 1
    effective = max(1, min(requested, total_runs, cpus))
    if effective == requested:
        return effective, None
    return effective, (
        f"workers reduced from {requested} to {effective}: "
        f"{total_runs} runs, {cpus} CPUs. {MAX_WORKERS_NOTE}"
    )


def _design(
    base_params: dict[str, Any],
    parameters: list[dict[str, Any]] | None,
    n_samples: int,
    conditions: list[dict[str, Any]] | None,
    replicates: int,
    seed: int | None,
):
    space = [Parameter.from_dict(p) for p in (parameters or [])]
    return plan(
        base_params,
        space,
        n_samples=n_samples if space else 1,
        conditions=conditions,
        replicates=replicates,
        seed=seed,
    )


def _describe_runs(design, limit: int) -> list[dict[str, Any]]:
    """The first `limit` runs, as identity rather than full parameter dumps."""
    out = []
    for run in design.runs[: max(0, limit)]:
        out.append(
            {
                **run.index(
                    with_parameter=design.with_parameter,
                    with_condition=design.with_condition,
                    with_replicate=design.with_replicate,
                ),
                "point": run.point,
                "condition": run.condition,
                "seed": run.seed,
            }
        )
    return out


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
def isicell_list_metrics() -> list[dict[str, Any]]:
    """The metrics a sweep can score against reference data, and what each measures.

    Scoring needs a metric name and a reference series, both data. An error
    function is deliberately not accepted, for the same reason a catcher is a
    name rather than an expression.
    """
    return describe_metrics()


# A run happens in a separate interpreter, so its failures are not exceptions
# here -- they are a traceback on stderr. These turn the two common ones into a
# sentence naming the cause, because "ModuleNotFoundError: pandas" buried in
# twelve lines of runpy frames does not say *which* interpreter is wrong.
MISSING_MODULE = re.compile(r"ModuleNotFoundError: No module named '([\w.]+)'")


def _diagnose_run_failure(stderr: str) -> str:
    """Why a generated run script died, said in terms the caller can act on."""
    interpreter = isicell_python()
    missing = MISSING_MODULE.search(stderr)
    if missing:
        name = missing.group(1)
        if name in ("isicell", "isiCellPy"):
            return (
                "the build has no importable isicell module. Compile with "
                "type='python' or 'all'."
            )
        return (
            f"the simulation could not start: {interpreter!r} has no module {name!r}. "
            "Generated run scripts import isicellpy, which needs pandas, tqdm, "
            "networkx, plotly, seaborn, matplotlib, scipy, optuna and SQLAlchemy. "
            f"Install them for {interpreter!r}, or set ISICELL_PYTHON to an "
            "interpreter that has them -- it must also be the one the build's "
            "_isiCellPy.so was compiled against."
        )
    if "undefined symbol" in stderr or "invalid ELF header" in stderr:
        return (
            f"the build's _isiCellPy.so does not load under {interpreter!r}. It is "
            "compiled against one specific interpreter; set ISICELL_PYTHON to that "
            "one, or recompile this build."
        )
    tail = [line for line in stderr.splitlines() if line.strip()][-3:]
    if not tail:
        return "the simulation failed to run and printed nothing."
    return "the simulation failed to run: " + " | ".join(tail)


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
    if not result["ok"] and not measurements:
        # The script died before printing anything, so there is no structured
        # error to report -- only a traceback the caller would have to parse.
        raise IsicellError(_diagnose_run_failure(result.get("stderr") or ""))
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
    base_params: dict[str, Any] | None = None,
    parameters: list[dict[str, Any]] | None = None,
    n_samples: int = 10,
    conditions: list[dict[str, Any]] | None = None,
    replicates: int = 1,
    seed: int | None = None,
    show: int = 10,
) -> dict[str, Any]:
    """Show every run a sweep would perform. Costs nothing.

    A sweep crosses three axes: sampled `parameters`, fixed `conditions`, and
    `replicates` of each combination.

    A parameter is {"name": "input.Cell.Common.x", "kind": "float"|"int"|
    "categorical", "low":, "high":, "choices":, "log":, "step":}; `name` is a
    dotted path into the run params. Latin hypercube sampling covers every axis
    evenly at any number of dimensions, which plain random sampling stops doing
    as axes are added.

    A condition is a dict of dotted-path overrides applied after the sampled
    point, so it wins when both name the same path -- a condition is the fixed
    experimental setup, the sweep explores within it.

    With a `seed` the whole design is reproducible, run for run.
    """
    design = _design(base_params or {}, parameters, n_samples, conditions, replicates, seed)
    return {
        **design.summary(),
        "bounds": design.bounds,
        "points": list(design.points),
        "conditions": list(design.conditions),
        "runs": _describe_runs(design, limit=show),
        "runsShown": min(show, len(design.runs)),
    }


@mcp.tool()
@_tool
def isicell_sweep_run(
    base_params: dict[str, Any],
    steps: list[int],
    parameters: list[dict[str, Any]] | None = None,
    n_samples: int = 10,
    conditions: list[dict[str, Any]] | None = None,
    replicates: int = 1,
    catcher: str = "countState",
    token: str | None = None,
    seed: int | None = None,
    database: str | None = None,
    overwrite: bool = False,
    reference: dict[str, Any] | None = None,
    column: str | None = None,
    metrics: list[str] | None = None,
    workers: int = 1,
    background: bool = False,
    confirm: bool = False,
) -> dict[str, Any]:
    """Run every combination of parameters, conditions and replicates.

    Preview with isicell_sweep_preview first. Needs confirm=True, because this
    is the tool that spends real compute -- one simulation per run.

    With `database`, the sweep records parameters, conditions and measurements
    into a results file the isicell_results_* tools can then query. Without it
    the measurements come back inline, which gets unwieldy past a few runs.

    With `reference` -- observed values per step, e.g. {"0": 10, "10": 22} -- each
    run is scored against it on `column` using the named `metrics`, and the
    scores go into an `error` table. That is what isicell_results_best and
    isicell_results_pareto read, so scoring is what turns a sweep into a ranked
    answer.

    `workers` runs that many simulations at once. Each one is a full process,
    so the useful ceiling is the CPU count, and a memory-hungry model may want
    fewer; the default of 1 keeps a sweep costing what it has always cost.
    Ordering is unaffected -- rows are written in design order whatever order
    the runs finish in.
    """
    use = token or SESSION.token
    if not use:
        raise IsicellError("No build token. Compile first, or pass one explicitly.")
    design = _design(base_params, parameters, n_samples, conditions, replicates, seed)

    # One preview branch for both modes. It costs nothing, so it runs before any
    # requirement the caller has not had to decide on yet.
    planned, _ = _effective_workers(workers, len(design.runs))
    if not confirm:
        where = " in the background" if background else ""
        parallel = (
            f" They run {planned} at a time."
            if planned > 1
            else f" They run one at a time; workers= up to {os.cpu_count() or 1} runs them in parallel."
        )
        return {
            "ran": False,
            **design.summary(),
            "runs": _describe_runs(design, limit=10),
            "workers": planned,
            "note": f"Preview only. Pass confirm=True to run these "
            f"{len(design.runs)} simulations{where}.{parallel}",
        }

    if background:
        if not database:
            raise IsicellError(
                "a background sweep needs a database: nothing is waiting to receive "
                "its measurements inline."
            )
        return _start_background_sweep(
            token=use,
            base_params=base_params,
            steps=steps,
            parameters=parameters,
            n_samples=n_samples,
            conditions=conditions,
            replicates=replicates,
            catcher=catcher,
            seed=seed,
            database=database,
            overwrite=overwrite,
            reference=reference,
            column=column,
            metrics=metrics,
            workers=workers,
        )

    if reference is not None and not database:
        raise IsicellError(
            "scoring writes an error table, so it needs a database to write into."
        )
    metric_names = list(metrics or ["rmse"])
    score_column = column
    if reference is not None and score_column is None:
        raise IsicellError(
            "scoring needs `column`: which measured column the reference describes."
        )

    writer = None
    if database:
        writer = ResultsWriter(database, overwrite=overwrite)
        writer.write_parameters(design.points)
        if design.with_condition:
            writer.write_conditions(design.conditions)

    backend = _backend()
    used, reduced = _effective_workers(workers, len(design.runs))
    outcomes: list[dict[str, Any]] = []
    recorded: list[dict[str, Any]] = []
    scores: list[dict[str, Any]] = []
    failures = 0

    def simulate_one(run) -> dict[str, Any]:
        return backend.simulate(
            use, RunSpec(params=run.params, steps=steps, catcher=catcher, seed=run.seed)
        )

    # Both branches are lazy, so an outcome is bookkept and released as it
    # arrives rather than every stdout being held until the last run lands.
    pool = ThreadPoolExecutor(max_workers=used) if used > 1 else None
    try:
        simulated = map(simulate_one, design.runs) if pool is None else pool.map(
            simulate_one, design.runs
        )
        for run, outcome in zip(design.runs, simulated):
            index = run.index(
                with_parameter=design.with_parameter,
                with_condition=design.with_condition,
                with_replicate=design.with_replicate,
            )
            measured = outcome.get("measurements") or {}
            ok = outcome["ok"] and measured.get("ok", False)
            failures += 0 if ok else 1
            entry = {
                **index,
                "seed": run.seed,
                "ok": ok,
                "error": measured.get("error"),
            }
            if writer is not None and ok:
                recorded.extend(measurement_rows(measured.get("data"), index, seed=run.seed))
            else:
                entry["data"] = measured.get("data")

            if reference is not None and ok:
                scored = score(
                    measured.get("data") or {}, reference, score_column, metric_names
                )
                entry["scores"] = scored
                scores.append({**index, **scored})
            outcomes.append(entry)
        if writer is not None:
            writer.write_measurements(catcher.replace(":", "_"), recorded)
            if scores:
                writer.write_errors(scores)
    finally:
        if pool is not None:
            # Queued runs are dropped on the way out; ones already started are
            # separate processes and are waited for, not orphaned.
            pool.shutdown(wait=True, cancel_futures=True)
        if writer is not None:
            writer.close()

    return {
        "ran": True,
        "token": use,
        **design.summary(),
        "workers": used,
        "workersNote": reduced,
        "failed": failures,
        "database": database,
        "table": catcher.replace(":", "_") if database else None,
        "scored": len(scores),
        "metrics": metric_names if reference is not None else None,
        "runs": outcomes,
    }


def _start_background_sweep(**kwargs: Any) -> dict[str, Any]:
    """Run the same sweep in a detached child of this server.

    The child re-enters this module and calls the very tool that would have run
    inline, so background and foreground sweeps cannot drift apart.
    """
    import shlex
    import sys

    payload = json.dumps({**kwargs, "confirm": True, "background": False})
    script = (
        "import json,sys;"
        "from mcp_isicell import server;"
        "a=json.loads(sys.argv[1]);"
        "r=server.isicell_sweep_run(**a);"
        "print(json.dumps({'totalRuns':r['totalRuns'],'failed':r['failed'],"
        "'scored':r.get('scored'),'database':r.get('database')}))"
    )
    token = f"sweep_{int(time.time())}_{os.getpid()}"
    store = _jobs()
    entry = store.start(
        token,
        [sys.executable, "-c", script, payload],
        cwd=Path.cwd(),
        label=f"sweep of {kwargs.get('n_samples')} points into {kwargs.get('database')}",
    )
    return {
        "ran": True,
        "background": True,
        "job": entry["token"],
        "log": entry["log"],
        "command": shlex.join(entry["command"][:2]) + " ...",
        "note": "Started. Poll with isicell_job_status, read isicell_job_log, "
        "and query the database once it finishes.",
    }


@mcp.tool()
@_tool
def isicell_sweep_refine(
    database: str,
    error_name: str,
    parameters: list[dict[str, Any]],
    shrink: float = 0.5,
) -> dict[str, Any]:
    """Narrow a parameter space around the best point a scored sweep found.

    Reads the winner from `database`, then returns a `parameters` list centred
    on it and `shrink` times as wide, ready to pass straight to
    isicell_sweep_run. It runs nothing: spending another sweep stays your
    decision.

    Pass the same `parameters` the original sweep used. Categorical axes are
    left alone -- there is no neighbourhood to narrow into -- and every box is
    kept inside the original bounds.
    """
    space = [Parameter.from_dict(p) for p in parameters]
    with ResultsDatabase(database) as db:
        best = db.best(error_name, n=1)
        if not best:
            raise IsicellError(
                f"no scored runs in {database}: sweep with a reference and metrics first."
            )
        winner_id = int(best[0]["ID_PARAMETER"])
        winning_params = db.parameters_for(winner_id)

    # Parameters are stored nested; the axes are named by dotted path.
    flat = _flatten_for_lookup(winning_params)
    narrowed, notes = refine(space, flat, shrink=shrink)
    return {
        "parameters": [_parameter_to_dict(p) for p in narrowed],
        "centred_on": {"ID_PARAMETER": winner_id, "score": best[0].get("score")},
        "winning_values": {n["name"]: n.get("centre") for n in notes if n.get("narrowed")},
        "notes": notes,
        "shrink": shrink,
        "note": "Nothing has run. Pass `parameters` to isicell_sweep_run to explore this box.",
    }


def _flatten_for_lookup(nested: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Nested parameters back to the dotted names the axes carry."""
    out: dict[str, Any] = {}
    for key, value in nested.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            out.update(_flatten_for_lookup(value, path))
        else:
            out[path] = value
    return out


def _parameter_to_dict(p: Parameter) -> dict[str, Any]:
    out: dict[str, Any] = {"name": p.name, "kind": p.kind}
    if p.kind == "categorical":
        out["choices"] = list(p.choices)
    else:
        out["low"] = p.low
        out["high"] = p.high
        if p.log:
            out["log"] = True
        if p.step:
            out["step"] = p.step
    return out


# --------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------


@mcp.tool()
@_tool
def isicell_job_list() -> list[dict[str, Any]]:
    """Background jobs, newest first, with their status reconciled."""
    return _jobs().list()


@mcp.tool()
@_tool
def isicell_job_status(job: str) -> dict[str, Any]:
    """One job: whether it is running, finished, failed or interrupted.

    A job that exited non-zero reads as failed and carries its exit code.
    """
    return _jobs().status(job)


@mcp.tool()
@_tool
def isicell_job_log(job: str, tail: int = 100) -> dict[str, Any]:
    """The tail of a job's output. stdout and stderr share one log."""
    return _jobs().log(job, tail=tail)


@mcp.tool()
@_tool
def isicell_job_stop(job: str, confirm: bool = False) -> dict[str, Any]:
    """Stop a running job. Needs confirm=True; it kills a live simulation."""
    return _jobs().stop(job, confirm=confirm)


@mcp.tool()
@_tool
def isicell_job_forget(job: str, confirm: bool = False) -> dict[str, Any]:
    """Drop a finished job from the registry and delete its log."""
    return _jobs().forget(job, confirm=confirm)


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


# --------------------------------------------------------------------------
# Optuna archives
# --------------------------------------------------------------------------
#
# Read as data, never replayed. ISiCell's own OptunaDB execs archived source
# the moment it is constructed (OptunaWrapper.py:588-593, :1124), so these read
# the SQLite schema directly instead. See results/optuna_archive.py.


@mcp.tool()
@_tool
def isicell_optuna_studies(database: str) -> list[dict[str, Any]]:
    """Studies in an Optuna archive: trial counts by state, and directions.

    Reads the archive as data. ISiCell's OptunaDB helper executes source stored
    in the file as soon as it opens one, so it is not used here; nothing in
    this tool or its siblings replays an archived function.
    """
    with OptunaArchive(database) as archive:
        return archive.studies()


@mcp.tool()
@_tool
def isicell_optuna_trials(
    database: str,
    study: str,
    state: str | None = "COMPLETE",
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Trials of one study, each with its objective values and parameters.

    `state` filters on Optuna's own trial state (COMPLETE, FAIL, PRUNED,
    RUNNING); pass None for every trial.
    """
    with OptunaArchive(database) as archive:
        return archive.trials(study, state=state, limit=limit)


@mcp.tool()
@_tool
def isicell_optuna_best(
    database: str, study: str, objective: int = 0, n: int = 5
) -> list[dict[str, Any]]:
    """The trials a study scored best, ranked by its own recorded direction.

    A maximised objective is ranked as maximised; the direction used is in the
    reply rather than assumed.
    """
    with OptunaArchive(database) as archive:
        return archive.best(study, objective=objective, n=n)


@mcp.tool()
@_tool
def isicell_optuna_parameters(database: str, trial_id: int) -> dict[str, Any]:
    """One trial's parameters, decoded from what Optuna stored.

    Categorical axes come back as their value rather than the stored category
    index, decoded from the distribution Optuna records beside the parameter.

    They are flat, named as Optuna recorded them, and are *not* a run parameter
    set: mapping an axis onto a nested run parameter was the archived suggest
    function's job, and this does not execute it. Build the run params from
    these values yourself, or re-run the sweep through isicell_sweep_run.
    """
    with OptunaArchive(database) as archive:
        return archive.params(trial_id)


def main() -> None:
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
