# Roadmap

Working notes, not a spec. Delete entries as they are done.

## State of play

| Milestone | Does | Status |
|---|---|---|
| M0 scaffold | package, pyproject, ruff, pytest | done |
| M1 offline validator | 16 rules, state graph, module deps | done |
| M2 discovery | modules, builds, status | done |
| M3 build | compile via mainGenerator, errors mapped to model | done |
| M4 run + results | run scripts; read result databases | partial |
| M5 explore | LHS sweeps, background jobs, Optuna | not started |

88 tests, none needing ISiCell. `uv run pytest && uv run ruff check .`

## M4 — run and results

`isicell_run_script` works: it drives `scriptLauncher.py`, which puts the build on
`sys.path` so a script can `from isicell import *` and construct `Simu(params)`.

Not done:

- **A `Simu` driver that does not need a written script.** Take params, steps and a
  catcher expression, generate the script, run it, return a table. The catcher is
  Python evaluated in the simulation process, so its trust boundary needs stating.
- **Result readers.** `DatabaseManager` is a rich SQLite API — `getBest`, `getPareto`,
  `getByID`, `query`, `summarize`. Reading the file directly is why the server runs on
  the same host. Note `get_conditions()` raises `NameError` on an undefined
  `rawParameters` (`DatabaseManager.py:188-203`); route around it via `get('condition')`.
- **Params are flattened with `$`** inside the database and lists become JSON strings;
  a reader has to unflatten before the numbers mean anything.

## M5 — exploration

- `paramatersExploration.py` takes its JSON in **argv[2]**, not stdin, and `exec()`s
  `exploration.functionPYTHON`. With more than one range it Latin-hypercube samples via
  SALib; with exactly one it is a `linspace`. Output is NDJSON, one line per sample, with
  base64 PNGs.
- `LHSIterator` has an Optuna-shaped `suggest_*` API and can be previewed without running
  anything — worth a pure tool that shows the sample points before any compute is spent.
- Background jobs are `nohup` + a `backgroundRun.json` registry + a per-token log. That
  is the right shape for runs that outlive a tool call.

## Watch

- **The HTTP backend is not built and may never need to be.** Running on the same host
  removes the cookie session, the IP pinning that silently logs a client out when its
  address changes, and the `cookie.secure` problem over plain HTTP. If a remote topology
  is ever needed, the seam is `backend/`, and `docs` in the git history record the full
  API inventory.
- **`isicell_run_script` takes a path, not code.** Generating a script from a string is
  the obvious next step and the obvious way to make the trust boundary worse. Decide
  deliberately.

## Upstream defects worth reporting

Found by reading; not this repo's to fix.

1. `routes/python.js:518` wires `validatePackageName` onto a `:packageSpecifier` route.
   It reads `req.params.packageName`, `undefined` there, so the regex tests the string
   `"undefined"` and always passes. The check is a no-op; `validatePackageSpecifier` is
   dead code.
2. `service/db.js:45` (`getStep`) builds SQL by string concatenation. Unreachable from
   any route today, a live injection sink if it is ever wired up.
3. `backend/dbInfo.json` holds a plaintext Postgres password in the repository.
