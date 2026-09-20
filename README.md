# mcp_isicell

An MCP server that turns [ISiCell](https://gitlab.com/isicell-irit/isicell) into an API
an LLM can call: author an agent-based cell model, validate it, compile it, and run it —
without the web UI.

ISiCell is a platform for co-designing cell biology models. A model is a state machine
per cell: each state carries C++ behaviour, each transition a C++ condition. The platform
splices that into C++ templates, builds with CMake, and runs the simulation.

The web UI is not privileged. The whole generate-compile pipeline is
`scripts/mainGenerator.py <token> <type>` reading the model JSON on **stdin**, so
anything that can emit valid `builderData` can drive the platform. That is what this
server does.

## What it gives you

**A validator that runs with no ISiCell installed.** Most of what goes wrong in an
ISiCell model is structural and decidable from the model alone: a transition to a state
that does not exist, a function body codegen cannot parse a name out of, two modules that
disagree about whether the world is 2D or 3D. Compiling takes minutes and reports errors
against generated C++ that nobody wrote. This answers instantly, against the model.

**Model authoring as tool calls.** `isicell_model_add_state`,
`isicell_model_add_transition`, and the rest mutate a server-held model and return fresh
findings on every call, so a mistake surfaces where it is made.

**Build and run**, with compiler errors mapped back onto model elements.

**Sweeps and results.** Latin hypercube sampling across points, conditions and
replicates, previewable for free before spending any compute, recorded into a results
database and read back through ranked scores, Pareto fronts, and parameter sets handed
back in the shape a run takes. Give a sweep reference data and it scores each run with
named metrics, so a calibration ends in a ranked answer rather than a pile of numbers.
A long sweep runs as a background job, so the call returns immediately, and refinement
proposes where to look next without spending anything.

**A session that survives.** The model you are authoring is autosaved, so a restart or a
reconnect does not cost you the work.

## Install

```sh
uv sync --group dev
```

Point it at an ISiCell checkout to enable compiling and running:

```sh
export ISICELL_ROOT=/path/to/ISiCell
export ISICELL_STATE_DIR=/path/to/state  # optional; session and job state
```

Without `ISICELL_ROOT`, authoring and validation still work — that half needs nothing
installed but this package.

Register it with an MCP client:

```json
{
  "mcpServers": {
    "isicell": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/mcp_isicell", "python", "-m", "mcp_isicell"],
      "env": { "ISICELL_ROOT": "/path/to/ISiCell" }
    }
  }
}
```

## A worked example

```
isicell_model_new("tumour")
isicell_model_add_state("Proliferating", "this->grow();")
isicell_model_add_state("Quiescent", "// wait")
isicell_model_add_transition("CellInstantiation", "Proliferating")
isicell_model_add_transition("Proliferating", "Quiescent", "countNeighborType(Common) > 6")
isicell_model_add_transition("Quiescent", "Proliferating", "countNeighborType(Common) <= 6")
isicell_model_add_transition("Proliferating", "DeadCell", "getRadius() > 10")
isicell_model_validate()        -> ok
isicell_build_compile("python") -> sim_2026919_2148_417
isicell_run_simulate(params={...}, steps=[0,100,200], catcher="countState")
                                -> {"0": {"Proliferating": 10}, ...}
```

Then sweep a parameter and read the results back:

```
isicell_sweep_preview(parameters=[
    {"name": "input.Cell.Common.cycleDuration", "kind": "float", "low": 10, "high": 30}
], n_samples=20, conditions=[{"input.Scenario.MOI": 0}, {"input.Scenario.MOI": 5}],
   replicates=3)                -> 120 runs laid out, no compute spent
isicell_sweep_run(..., database="sweep.db",
   reference={"0": 10, "10": 30}, column="Alive", metrics=["rmse"],
   confirm=True)                -> every run scored against observed data
isicell_results_best("sweep.db", "rmse", n=5)
isicell_results_parameters("sweep.db", 3)  -> the winner, ready to re-run
```

`CellInstantiation` and `DeadCell` are not states. They compile to `this->init()` and
`this->die()`, so a model needs one transition out of the first and, usually, one into
the second.

## Trust boundary

Compiling and running ISiCell models **executes arbitrary C++ and Python as the user
running this server**. That is what ISiCell is: the model *is* code. The platform also
`exec()`s template snippets during codegen and user-supplied Python during parameter
sweeps.

The `confirm` flags on destructive and expensive tools guard against accidents — a token
reuse silently deleting a previous build, a sweep spending twenty simulations — and are
**not a security control**. An LLM can set them itself. Real gating is the MCP host's
permission prompts. Run this against instances and models you trust.

Two boundaries are held deliberately rather than by a flag. A run names a measurement
from a fixed set instead of accepting a Python expression, and every value crosses into
the generated script as JSON. Result readers take structured filters instead of SQL, over
a read-only connection. Neither is a claim that the server is a sandbox — compiling
already runs your C++ — but neither adds a new way in.

The server never echoes the `main` block of a run-params file, which carries the database
password.

## The upstream zip

`isicell-main.zip` is a GitLab snapshot, kept locally and gitignored. It is not corrupt,
but a zip export cannot carry git submodules, so all seven
`core/modules/cellModules/*` directories in it are empty. Selecting an official module
then fails at `mainGenerator.py:86` with `FileNotFoundError`, and `/api/defaultJson`
throws. `E_MODULE_MISSING_ON_DISK` catches this before a build is spent on it; the fix is
`git submodule update --init --recursive` on a real checkout.

## Development

```sh
uv run pytest          # 273 tests, no ISiCell needed
uv run ruff check .
```

The compile path is tested against a stub generator and the run path against a fake build
with a stub `Simu`, so the suite needs no C++ toolchain and no SWIG.
Tests that need a real checkout are marked `integration` and deselected by default.

`mcp_isicell/domain/` is pure: no I/O, no subprocesses. Everything that touches the world
lives in `backend/`, `results/` and `config.py`. See `skill/SKILL.md` for the modelling guide and
`ROADMAP.md` for what is built and what is not.

## Licence

LGPL-3.0-or-later, matching ISiCell.
