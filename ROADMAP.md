# Roadmap

Working notes, not a spec. Delete entries as they are done.

## State of play

| Milestone | Does | Status |
|---|---|---|
| M0 scaffold | package, pyproject, ruff, pytest | done |
| M1 offline validator | 16 rules, state graph, module deps | done |
| M2 discovery | modules, builds, status | done |
| M3 build | compile via mainGenerator, errors mapped to model | done |
| M4 run + results | generated run scripts, read-only result readers | done |
| M5 explore | LHS preview and sweeps | done |

34 tools, 153 tests, none needing ISiCell.
`uv run pytest && uv run ruff check .`

## What is deliberately not built

- **Raw Python catchers.** A run names a measurement from a fixed set, or reads one
  attribute. Accepting an expression would make remote code execution a first-class
  feature rather than a consequence of compiling. `isicell_run_script` remains the
  escape hatch, and it takes a path to a file a person wrote.
- **Raw SQL over results.** Readers take structured filters; a column is checked against
  the schema and a value is bound. If a real query turns out to be unreachable through
  them, add a named reader rather than an SQL passthrough.
- **`MultiSimu` and its process pool.** Sweeps run one simulation per point in sequence.
  `MultiSimu` parallelises over params × conditions × replicates, but it keeps a
  module-global pool (`MultiSimu.py:9`) that a long-lived server would inherit and have
  to manage. Worth doing when sweep durations justify it; not before.
- **The HTTP backend.** Running on the same host removes the cookie session, the IP
  pinning that silently logs a client out when its address changes, and the
  `cookie.secure` problem over plain HTTP. If a remote topology is ever needed, the seam
  is `backend/`.

## Next, in rough order

- **Replicates and conditions in a sweep.** Today one run per point. Real calibration
  wants n replicates per point and a condition axis, which is what the `error` and
  `condition` tables in a results database are shaped for.
- **Writing results.** The server reads databases it did not write. A sweep that recorded
  into one would close the loop: sweep, score, `isicell_results_best`, re-run the winner.
  Needs an error function, which is the same trust question as a catcher.
- **Background jobs.** `nohup` plus a `backgroundRun.json` registry plus a per-token log
  is the right shape for runs that outlive a tool call. Until then a long sweep blocks.
- **Optuna archives.** `OptunaDB` reads studies, trials and fitness from the same file;
  `build_params(trial_id)` replays an archived suggest function to rebuild a full
  parameter set. That replay executes archived code, so it needs the same care as a
  catcher.

## Watch

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
