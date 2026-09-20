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

41 tools, 273 tests, none needing ISiCell.
`uv run pytest && uv run ruff check .`

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

- **Parallel runs.** Sweeps run sequentially. `MultiSimu` parallelises over the same
  three axes, but keeps a module-global pool (`MultiSimu.py:9`) a long-lived server would
  inherit. Worth it once sweep durations justify the lifecycle work.
- **Optuna archives.** `OptunaDB` reads studies, trials and fitness from the same file;
  `build_params(trial_id)` replays an archived suggest function to rebuild a full
  parameter set. That replay executes archived code, so it needs the same care as a
  catcher.

## Watch

- **Refinement is local.** It narrows around the single best point, so a space with two
  good regions loses one of them at the first round. That is the usual trade for a local
  search and is fine when the surface has one basin; it is the wrong tool when it does
  not. Optuna exists for that case.

- **A job outliving the server that started it.** The registry survives a restart and
  reconciliation reads /proc, so a job started by a previous process is still tracked --
  but its exit status cannot be collected, because only the parent can wait on it. Such a
  job settles as `finished` with no exit code even if it failed. Worth revisiting if
  restarts during long sweeps turn out to be common.

- **`get_conditions()` upstream raises `NameError`** on an undefined `rawParameters`
  (`DatabaseManager.py:198`). This package does not call it; read the table through
  `isicell_results_get(database, "condition")`.
- **Parameters are stored flattened with `$`** and lists as JSON strings. The readers
  unflatten on the way out, so a caller sees dotted names and real lists. Anything new
  that touches `parameters` must do the same or the numbers will not mean what they say.

## Upstream defects worth reporting

Found by reading; not this repo's to fix.

1. `routes/python.js:518` wires `validatePackageName` onto a `:packageSpecifier` route.
   It reads `req.params.packageName`, `undefined` there, so the regex tests the string
   `"undefined"` and always passes. The check is a no-op; `validatePackageSpecifier` is
   dead code.
2. `service/db.js:45` (`getStep`) builds SQL by string concatenation. Unreachable from
   any route today, a live injection sink if it is ever wired up.
3. `DatabaseManager.get_conditions()` raises `NameError` (`DatabaseManager.py:198`).
4. `backend/dbInfo.json` holds a plaintext Postgres password in the repository.
