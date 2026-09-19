# mcp_isicell — working notes for an agent

An MCP server making ISiCell drivable by an LLM. Read `README.md` for what it is and
`skill/SKILL.md` for how ISiCell models actually work — the schema, the state-machine
idiom, and the validator codes. This file is about working in the repo.

## Reading order

1. `skill/SKILL.md` — the domain. Nothing here makes sense without it.
2. `mcp_isicell/domain/validation.py` — the rules, each citing the source line it
   prevents failing at.
3. `mcp_isicell/server.py` — the tool surface.
4. `ROADMAP.md` — what is deliberately not built.

## Architecture

`domain/` is **pure**: no I/O, no subprocesses, no environment reads. Everything that
touches the world is in `backend/` (subprocesses), `results/` (databases) and `config.py`
(environment). This is what lets 88 tests run with no ISiCell, no C++ toolchain, no
Docker and no node.

Authoring operations return a new model and never mutate their argument. `session.py`
rebinds the current model. A rejected edit therefore cannot leave the session corrupt.

## Standing rules

- **The validator is the product.** When a build fails for something the validator could
  have caught, add the rule and its test. Do not just fix the model and move on.
- **Every rule cites what it prevents.** A finding that cannot name the source location
  it saves you from is a guess.
- **Test names state the invariant.**
  `test_a_second_unconditional_destination_is_flagged_as_dead_code`, not `test_validate_3`.
- **Deliberate failures raise `ToolError`** with a message the caller can act on. Raised
  bare, an `IsicellError` reaches the client as "Error executing tool" and the
  explanation — the useful part — is thrown away.
- **Never widen a test to make it pass.** Do not relax an assertion or mark a test
  `skip` to get green.
- `uv run pytest && uv run ruff check .` green before every commit.
- Commits: capitalised imperative sentence with a comma-reason clause, prose body saying
  what was found and what was deliberately not done. Never `git add -A` without looking.
  Do not push; that stays a manual action.

## Things that will bite

- `mainGenerator.py` **rmtree's the simulation directory** before regenerating. Reusing a
  token destroys the previous build — hence `confirm=True`.
- It reads the model on **stdin**. argv[3] exists in a legacy call path and is ignored.
- `from` is a Python keyword, so `Transition.from_` carries `alias="from"`. Serialise
  with `by_alias=True` or codegen silently sees no transitions.
- Progress arrives as pty chunks that split anywhere, including inside `[ 50%]`.
- The `confirm` flags are ergonomics, not security. Say so; do not imply otherwise.
