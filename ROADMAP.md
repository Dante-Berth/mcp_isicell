# Roadmap

Working notes, not a spec. Delete entries as they are done.

## State of play

| Milestone | Does | Status |
|---|---|---|
| M0 scaffold | package, pyproject, ruff, pytest | done |
| M1 offline validator | 16 rules, state graph, module deps | done |
| M2 discovery | modules, builds, status | done |
| M3 build | compile via mainGenerator, errors mapped to model | done |
| M4 run + results | generated run scripts, result readers and writer | done |
| M5 explore | LHS sweeps across points, conditions and replicates | done |
| M6 scoring | named metrics against a reference, ranked results | done |
| M7 jobs | background sweeps, status, logs, stop | done |
| M8 refine | narrow the space around a winner; session survives restart | done |
| M9 proof | the whole pipeline run against a real ISiCell checkout | done |

41 tools, 305 tests that need no ISiCell, plus 17 integration tests that do
(`ISICELL_ROOT=... uv run pytest -m integration`).
`uv run pytest && uv run ruff check .`

M9 covers the full path *through the tools themselves*: a model authored one
`isicell_model_*` call at a time, compiled by `isicell_build_compile`, and measured by
`isicell_run_simulate` -- ten cells that age and die on schedule. The layers below it
are covered separately, so a bug that only appears when they are composed has somewhere
to show up. A scored sweep runs the M5-M7 chain over real simulations and checks the
ranking is a fact about the model, not just a number the writer round-tripped, and a
background sweep proves a detached child reaches a real build and that stopping one
takes its simulations down with it. The system libraries do **not**
need root: `apt-get download` plus `dpkg-deb -x` into a prefix, with `CMAKE_PREFIX_PATH`,
`BOOST_ROOT` and `CPATH` pointed at it, satisfies every `find_package` in
`core/CMakeLists.txt`.

## What is deliberately not built

- **Raw Python catchers.** A run names a measurement from a fixed set, or reads one
  attribute. Accepting an expression would make remote code execution a first-class
  feature rather than a consequence of compiling. `isicell_run_script` remains the
  escape hatch, and it takes a path to a file a person wrote.
- **Raw SQL over results.** Readers take structured filters; a column is checked against
  the schema and a value is bound. If a real query turns out to be unreachable through
  them, add a named reader rather than an SQL passthrough.
- **An error *function*.** Scoring is by named metric against a reference series. An
  expression scoring a run would be flexible and would also reintroduce exactly the
  arbitrary execution the catcher design avoids. If a real study needs a metric the set
  lacks, add the metric.
- **The HTTP backend.** Running on the same host removes the cookie session, the IP
  pinning that silently logs a client out when its address changes, and the
  `cookie.secure` problem over plain HTTP. If a remote topology is ever needed, the seam
  is `backend/`.

## Next, in rough order

- **Optuna archives.** `OptunaDB` reads studies, trials and fitness from the same file;
  `build_params(trial_id)` replays an archived suggest function to rebuild a full
  parameter set. That replay executes archived code, so it needs the same care as a
  catcher.

## Watch

- **Parallelism is per sweep, not per server.** `workers` fans out one sweep's runs
  across threads blocking on subprocesses, capped at the CPU count. Nothing limits two
  sweeps started at once from asking for the CPU count each; the runs are separate
  processes, so the machine oversubscribes rather than corrupting anything. A shared
  limit would have to live in the job registry.

- **The console binary is built but never executed by a test.** `main.cpp` takes a
  config path as argv[1] and writes through PluginSQL, so exercising it means standing up
  a database. The runs this package actually makes go through the SWIG module, which
  *is* covered end to end. Worth adding if the `sql` build type gains a caller.

- **Refinement is local.** It narrows around the single best point, so a space with two
  good regions loses one of them at the first round. That is the usual trade for a local
  search and is fine when the surface has one basin; it is the wrong tool when it does
  not. Optuna exists for that case.

- **A job outliving the server that started it.** The registry survives a restart and
  reconciliation reads /proc, so a job started by a previous process is still tracked --
  but its exit status cannot be collected, because only the parent can wait on it. Such a
  job settles as `finished` with no exit code even if it failed. Confirmed against real
  ISiCell: in the starting process a background sweep reports `exit_code: 0` and a
  non-zero exit reads as `failed`; from a later process the same job reads `finished`
  with none. Worth revisiting if restarts during long sweeps turn out to be common.

- **`get_conditions()` upstream raises `NameError`** on an undefined `rawParameters`
  (`DatabaseManager.py:198`). This package does not call it; read the table through
  `isicell_results_get(database, "condition")`.
- **Parameters are stored flattened with `$`** and lists as JSON strings. The readers
  unflatten on the way out, so a caller sees dotted names and real lists. Anything new
  that touches `parameters` must do the same or the numbers will not mean what they say.

## Upstream defects worth reporting

Not this repo's to fix. 1-4 were found by reading; 5 and 6 by running the
real generator and watching the mapping come back empty.

1. `routes/python.js:518` wires `validatePackageName` onto a `:packageSpecifier` route.
   It reads `req.params.packageName`, `undefined` there, so the regex tests the string
   `"undefined"` and always passes. The check is a no-op; `validatePackageSpecifier` is
   dead code.
2. `service/db.js:45` (`getStep`) builds SQL by string concatenation. Unreachable from
   any route today, a live injection sink if it is ever wired up.
3. `DatabaseManager.get_conditions()` raises `NameError` (`DatabaseManager.py:198`).
4. `backend/dbInfo.json` holds a plaintext Postgres password in the repository.
5. Position markers are emitted with `code.replace('\n', marker, 1)`
   (`PrimoCell.hpp:182`, `Scenario.hpp:39`), so a single-line function gets no marker
   and the build reports no `metaInfo` for it. Compiler errors inside such a function
   cannot be mapped back to the model. `W_FUNC_SINGLE_LINE` warns; the fix upstream is
   to prepend the marker rather than substitute a newline.
6. `metaDataDebug` markers exist only for functions, `codeInit` and `codeLoop`. State
   code and transition conditions get none, so upstream reports no `metaInfo` for the
   most likely place for a user to write bad C++. Worked around here rather than waited
   on: `locate_by_case_label` reads the generated switch, whose `case State::<name>`
   labels come from the same caseTemplate (`PrimoCell.hpp:18`), and the enclosing
   function (`behave` vs `nextState`) says whether the line is state code or a
   transition condition. Such a finding is tagged `attributed_by: "case-label"` to
   distinguish it from one upstream actually reported. The upstream fix is still worth
   making -- markers would survive template changes; a layout inference will not.
