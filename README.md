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

## Install

```sh
uv sync --group dev
```

Point it at an ISiCell checkout to enable compiling and running:

```sh
export ISICELL_ROOT=/path/to/ISiCell
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
```

`CellInstantiation` and `DeadCell` are not states. They compile to `this->init()` and
`this->die()`, so a model needs one transition out of the first and, usually, one into
the second.

## Trust boundary

Compiling and running ISiCell models **executes arbitrary C++ and Python as the user
running this server**. That is what ISiCell is: the model *is* code. The platform also
`exec()`s template snippets during codegen and user-supplied Python during parameter
sweeps.

The `confirm` flags on destructive tools guard against accidents — a token reuse silently
deleting a previous build — and are **not a security control**. An LLM can set them
itself. Real gating is the MCP host's permission prompts. Run this against instances and
models you trust.

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
uv run pytest          # 88 tests, no ISiCell needed
uv run ruff check .
```

The compile path is tested against a stub generator, so the suite needs no C++ toolchain.
Tests that need a real checkout are marked `integration` and deselected by default.

`mcp_isicell/domain/` is pure: no I/O, no subprocesses. Everything that touches the world
lives in `backend/` and `results/`. See `skill/SKILL.md` for the modelling guide and
`ROADMAP.md` for what is built and what is not.

## Licence

LGPL-3.0-or-later, matching ISiCell.
