---
name: isicell-modeling
description: Author, validate, compile and run ISiCell agent-based cell biology models through the mcp_isicell server. Covers the builderData schema, the state-machine idiom, the validator codes, and the generate-compile-run loop. Invoke when building or debugging an ISiCell model, or when a compile fails and the error must be traced back to the model.
---

# isicell-modeling

ISiCell is a platform for agent-based cell biology models. A model is a **state
machine per cell**: each state carries C++ behaviour code, and transitions carry
C++ boolean conditions. The platform splices that into C++ templates, compiles
with CMake, and runs the simulation.

The web UI is not privileged. The whole pipeline is
`scripts/mainGenerator.py <token> <type>` reading the model JSON **on stdin**.
Anything that can emit valid `builderData` can drive ISiCell. That is what this
server does.

## 1. The two JSON documents — do not conflate them

| | **builderData** (`finalJson`) | **runParams** |
|---|---|---|
| Is | the model *definition* | *values* for one run |
| Goes to | `mainGenerator.py` on stdin | `Simu(params)` / `console <f.json>` |
| Produces | C++ → `make` → `console` + `_isiCellPy.so` | one execution |

Compiling is expensive (minutes) and defines *structure*. Running is cheap and
supplies *numbers*. If a change only moves a number, you do not recompile — that
is what `paramInputs` is for. Check `isicell_build_checksum` before rebuilding.

Two token namespaces, and they are different things:

- `sim_*` — a built project directory under `simulations/`.
- `web*` — one execution of it, and the PostgreSQL table prefix.

## 2. The builderData schema

```
moduleSelected            [str]    official module Names (not DirNames)
customModule              {name: {file: {code, type}}}
enums                     {name: [values]}
cellTypes                 [str]    empty => enum Type { Common }
scenario
  attributes              [{type, value, source}]
  codeInit, codeLoop      C++ run once / every step
  functions               {idFunc: {code}}
cell
  attributes              [{type, value, source}]
  initializationCode      C++ run at cell birth
  initializationTransitions  [{from: "CellInstantiation", destinations: [...]}]
  States                  [{name, code}]
  Transitions             [{from, destinations: [{to, condition}]}]
  functions               {idFunc: {code}}
paramInputs               {Cell: [{type,value}], Scenario: [{type,value}]}
CellAttributesRecorded    [{type,value}]   -> becomes a Postgres column
CellFunctionsRecorded     [{type,value}]
```

`source: 'core'` attributes are skipped in codegen — they describe what the
engine already provides. Only `'user'` attributes are emitted.

An attribute reaches `paramInputs` or `CellAttributesRecorded` by being *tagged*
in the UI (`MODEL`, `VIEWER`). Through this server, set them explicitly with
`isicell_model_set_param_inputs`.

## 3. The two magic state names

`CellInstantiation` and `DeadCell` are not ordinary states. They compile to
method calls, not to `State::` values (`core/src/cells/PrimoCell.hpp:29-32`):

- `to: "CellInstantiation"` → `this->init()` — re-initialise the cell
- `to: "DeadCell"` → `this->die()` — remove it from the world

So every model needs at least one transition to `DeadCell`, or cells never die,
and at least one `initializationTransitions` entry, or a newborn cell never
enters a real state. The UI spells them `"Cell Instantiation"` and `"Dead Cell"`
and normalises the spaces away; emit them without spaces.

**Never declare a state named `CellInstantiation` or `DeadCell`.** It generates
a duplicate `case` and the build fails on generated source you did not write.

## 4. Transition conditions are ordered, and the order is rewritten

`buildConditionsCode` (`PrimoCell.hpp:15-39`) sorts a state's destinations so
that **unconditional ones go last**, then emits `if / else if / else`. Two
consequences:

- You do not need to order destinations yourself; put the fallback anywhere.
- **Two unconditional destinations from one state means the second is dead
  code.** The validator reports `W_UNORDERED_CONDITIONS`. It compiles, and it
  silently never fires — the worst failure mode there is.

A condition is a C++ boolean expression evaluated on the cell. Helpers available
to it: `isType(t)`, `isInContactWith(t)`, `countNeighborType(t)`, `isInContact()`,
`getId()`, `getMotherId()`, `getRadius()`, `getParametersFrom(type)`.

## 5. Step-by-step — the authoring loop

### Step 1 — Start a model

`isicell_model_new`, or `isicell_model_load` from a demo. The model is held in
the session; authoring tools mutate it and return fresh findings. Do not pass the
whole model on every call — `isicell_model_export` when you want it out.

### Step 2 — Declare structure before code

States, cell types and enums first; then attributes; then behaviour code. Writing
C++ against attributes that do not exist yet produces compile errors in generated
source, which are much harder to read than a validator finding.

### Step 3 — Select modules, and respect the dependency kinds

`isicell_model_select_module` takes the module **`Name`** (`"Diffusion2D"`,
`"Mass Spring Damper"`), not the `DirName`. Dependencies come in two kinds and
both are checked:

- a module `Type`, e.g. Grimes Oxygen depends on `"Spheroid Manager"`
- a dimensionality pseudo-capability, `"2D"` or `"3D"`

A 2D and a 3D physics module together is `E_SPACE_DIM_CONFLICT`. It is not a
warning; the generated world cannot be both.

### Step 4 — Validate before you spend a compile

`isicell_model_validate`. Fix every `error`. Read every `warning` — the two that
matter most are `W_UNREACHABLE_STATE` (you wrote behaviour that can never run)
and `W_UNORDERED_CONDITIONS` (above).

`isicell_build_compile` refuses a model with error-level findings unless
`force=True`. Do not reach for `force` to make a finding go away; it exists for
the case where you know the validator is wrong, and then say so.

### Step 5 — Compile

`isicell_build_compile(type=...)`, where type selects what gets built:

| type | SWIG/Python | SQL | Use for |
|---|---|---|---|
| `python` | yes | no | driving from Python — the usual choice |
| `sql` | no | yes | the `console` binary writing to Postgres |
| `all` | yes | yes | both |
| `debug` | no | yes | `-DDEBUG=ON`, runs under GDB |

**Reusing a token destroys that build.** `mainGenerator.py:66` does
`shutil.rmtree(simPath)` before regenerating. The tool requires `confirm=True`
when the token already exists. Omit `token` to get a fresh one.

### Step 6 — Read a compile failure back onto the model

The generator prints `metaInfo:` JSON to stderr mapping generated C++ lines to
model elements. The server surfaces it as `meta_info`, so a `g++` error at
`PrimoCell.hpp:212` is reported as *the function `divide` in the cell*. Report
the model location, never the generated line — the generated file is rewritten
on every build and the line number is worthless to the user.

### Step 7 — Run

`isicell_run_simulate(params, steps, catcher)` runs against the SWIG module, so
the build must be `python` or `all`. `params` is **runParams**, not builderData:

```
{"input": {"Cell": {"Common": {...}}, "Scenario": {"dt": 360, "maxStep": 1000}}}
```

`Cell.Common` must always be present — `Model.hpp:52` falls back to it for any
cell type that does not name a value, and its absence throws
`problem with cell paramter "<name>", for cell type "<type>"` at load.

`dt` is in **seconds**; the default 360 is six minutes of simulated time.
`steps` are cumulative step indices to measure at, and the run only ever
advances forwards.

`catcher` names a measurement — `isicell_list_catchers` gives the set, and
`attribute:<name>` reads one recorded attribute. **Arbitrary Python is not
accepted**, deliberately: compiling already runs your C++, and a catcher that
took an expression would add remote code execution as a feature rather than a
consequence. When a measurement is genuinely outside the set, write a script and
use `isicell_run_script`.

### Step 8 — Sweep

A sweep crosses three axes: sampled **parameters**, fixed **conditions**, and
**replicates** of each combination. `isicell_sweep_preview` lays out every run
and costs nothing; `isicell_sweep_run` needs `confirm=True` because it is one
simulation per run.

A parameter is declared as data, not as a callback:

```
{"name": "input.Cell.Common.cycleDuration", "kind": "float", "low": 10, "high": 30}
{"name": "input.Scenario.seedCount", "kind": "int", "low": 1, "high": 50, "log": true}
{"name": "input.Scenario.mode", "kind": "categorical", "choices": ["a", "b"]}
```

`name` is a dotted path into the run params. LHS covers every axis evenly
however many axes there are, which is the property plain random sampling loses
as dimensions grow — that is the reason to prefer it over a grid or a shotgun.

A **condition** is a dict of the same dotted paths: the fixed setup a sweep
explores within, for example `{"input.Scenario.MOI": 5}`. It is applied after
the sampled point, so a condition wins when both name the same path.

A **replicate** is the same parameters under a different seed. Pass a `seed` to
the sweep and every run's seed is derived from it by position, so the whole
design replays from one number and any single run can be re-run exactly. Without
a seed the engine draws its own and the sweep is not reproducible.

Index columns follow the axes that vary: `ID_PARAMETER` only when more than one
point, `ID_CONDITION` only when conditions were given, `ID_REPLICAT` only when
replicates exceed one. A run carrying all three when nothing varied would be
three false claims.

Runs multiply. Three points by two conditions by two replicates is twelve
simulations; a design over 2000 runs is refused.

Past a handful of runs, pass `background=True` and the sweep becomes a job: the
call returns at once and the simulations continue without it. A background sweep
needs a `database`, because nothing is waiting to receive measurements inline.
Track it with `isicell_job_status` and `isicell_job_log`, stop it with
`isicell_job_stop` (confirm required — it kills a live simulation), and query the
database once it reports `finished`. A job that exited non-zero reads as
`failed` and carries its exit code; that distinction is the reason to look.

### Step 9 — Record and read results

Pass `database` to `isicell_sweep_run` and it records what it found: a
`parameters` table of the sampled points, a `condition` table when conditions
were crossed, one measurement table named after the catcher, and an `error`
table when you also gave it reference data to score against. Without a
database the measurements come back inline, which gets unwieldy past a few runs.
An existing file is never replaced unless `overwrite=True`.

Read any results database — one this server wrote, or one a calibration script
wrote with isicellpy — with:

- `isicell_results_summarize` — tables, row counts, and the error columns worth
  optimising against. Start here; it tells you what the other calls can ask for.
- `isicell_results_best(db, error, n)` — averages the error over conditions and
  replicates, then ranks. The averaging is what makes two parameter sets
  comparable.
- `isicell_results_pareto(db, [errors])` — the sets no other set beats on every
  error at once. With one error this is just the minimum.
- `isicell_results_parameters(db, id)` — the nested parameter set behind an ID,
  in exactly the shape `isicell_run_simulate` takes, so re-running a winner is
  one call.
- `isicell_results_get(db, table, filters)` — rows, filtered structurally:
  `[{"column": "ID_PARAMETER", "op": "ge", "value": 3}]`. Not SQL.

`best` and `pareto` read an `error` table, which a sweep fills when you give it
reference data. Pass `reference` (observed values per step), `column` (which
measured column the reference describes), and `metrics` — a list of names from
`isicell_list_metrics`:

| Metric | Measures |
|---|---|
| `rmse` | root mean squared error over the matched steps |
| `mae` | mean absolute error over the matched steps |
| `mape` | mean absolute percentage error, skipping zero references |
| `max_abs` | the largest absolute difference at any matched step |
| `final_abs` | absolute difference at the last matched step only |

There is no error *function*: a metric is a name and a reference is data, for the
same reason a catcher is a name rather than an expression.

Alignment is strict. Only steps present and numeric on both sides are compared,
and a reference sharing no steps with the run is refused rather than scored — an
empty intersection would otherwise rank a model that measured nothing as the
perfect fit. So `steps` must actually include the steps the reference names.

Two storage details the readers hide, and anything new must too: nested
parameters are flattened with `$` in the table, and lists are stored as JSON
strings. You see dotted names and real lists.

## 6. Validator codes

| Code | Meaning |
|---|---|
| `E_NO_STATES` | no states — `enum State {}` is not valid C++ |
| `E_TRANSITION_UNDEFINED_TARGET` | `to` is not a declared state or a magic name |
| `E_TRANSITION_UNDEFINED_SOURCE` | `from` is not a declared state |
| `E_DUPLICATE_STATE` | two states share a name |
| `E_NO_INIT_TRANSITION` | nothing leaves `CellInstantiation` |
| `E_FUNC_NO_NAME` | function code has no parseable `name(` — crashes codegen |
| `E_UNKNOWN_MODULE` | not in `officialModules.json` |
| `E_MODULE_MISSING_ON_DISK` | module selected but its `config.json` is absent |
| `E_MODULE_DEPS` | unmet dependency |
| `E_SPACE_DIM_CONFLICT` | 2D and 3D modules together |
| `E_BAD_IDENTIFIER` | name is not a valid C++ identifier |
| `E_RECORDED_ATTR_TYPE` | recorded attribute type has no SQL mapping |
| `W_UNREACHABLE_STATE` | not reachable from `CellInstantiation` |
| `W_DEAD_END_STATE` | no way out, and not `DeadCell` |
| `W_UNORDERED_CONDITIONS` | second unconditional destination is dead code |
| `W_NO_COMMON_CELL_TYPE` | run params will need a `Cell.Common` block |

## 7. Known upstream breakage

- **Empty submodules.** In a zip export of ISiCell, every
  `core/modules/cellModules/*` is empty. Selecting an official module then fails
  at `mainGenerator.py:86` with `FileNotFoundError`, and `/api/defaultJson`
  throws. Fix is `git submodule update --init --recursive` on the ISiCell
  checkout. `E_MODULE_MISSING_ON_DISK` catches this before the compile.
- **`DatabaseManager.get_conditions()` raises `NameError`** — it references an
  undefined `rawParameters` (`DatabaseManager.py:188-203`). Use
  `get('condition', ...)`.
- **`MultiSimu` keeps a module-global process pool** (`MultiSimu.py:9`) reused
  across instances. A long-lived server must not assume it is clean.

## 8. Engineering standard

- The validator is the product. When a build fails for a reason the validator
  could have caught, add the rule and the test — do not just fix the model.
- Keep `domain/` pure. Subprocesses and files belong in `backend/` and `results/`.
- Test names state the invariant:
  `test_validator_flags_second_unconditional_destination_as_dead_branch`, not
  `test_validate_3`.
- Compiling and running execute arbitrary C++ and Python as the invoking user.
  The `confirm` flags are ergonomics against accidents, **not a security
  control** — real gating is the MCP host's permission prompts. Say so rather
  than implying otherwise.
- A tool describes, it does not program. A run names a measurement; a filter is a
  structured comparison. When something is unreachable through them, add a named
  measurement or a named reader — do not open a hole for Python or SQL.
- Never echo the `main` block of a runParams file back to a caller; it carries
  the database password.
- Do not add tools beyond what was asked.
