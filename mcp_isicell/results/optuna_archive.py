"""Read-only access to an Optuna study archive, without importing Optuna.

ISiCell drives Optuna through `OptunaWrapper.py`, whose `OptunaDB` reads
studies, trials and fitness from one SQLite file. This package does not use it,
for one reason: `OptunaDB.__init__` calls `load_context()` whenever the archive
carries the `isicell_context` tables (:588-593), and `load_context` runs
`exec(payload_txt, g, g)` over source stored in the database (:1124) and
`pickle64b_to_variable` over pickles stored beside it (:1120). Constructing the
helper to ask for a list of study names therefore executes whatever the file
carries. Its `readonly=True` governs the SQLite connection, not that.

`build_params(trial_id)` (:1175) is the same hazard by design: it replays the
archived suggest function to rebuild a structured parameter set. Nothing here
replays it. What that function did for categorical axes -- turning a stored
category index back into the value -- is recoverable without executing
anything, because Optuna records the distribution beside the parameter, so
that part is done here from `trial_params.distribution_json`.

What is not recoverable without the archived function is the *structure* it
imposed: a suggest function that maps a flat axis onto a nested run parameter
leaves no record of the mapping. Trial parameters therefore come back flat,
named as Optuna recorded them.

The schema read here is Optuna's own RDB schema, and the subset used is the one
`OptunaWrapper` queries against real archives (:604-731) -- studies, trials,
trial_values, trial_params -- plus `distribution_json`, which is treated as
optional so an archive written by a version that lacks it still reads.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ..errors import IsicellError

# Optuna writes one row per objective, so a study is multi-objective when it has
# more than one direction. `objective` is the index into them.
DIRECTIONS = {"MINIMIZE": "minimize", "MAXIMIZE": "maximize", 1: "minimize", 2: "maximize"}

COMPLETE = "COMPLETE"
MAX_ROWS = 5000

REQUIRED_TABLES = ("studies", "trials", "trial_values", "trial_params")


class OptunaArchiveError(IsicellError):
    """The file is missing, is not an Optuna archive, or lacks what was asked."""


def _direction(raw: Any) -> str:
    """'MINIMIZE' | 'NOT_SET' | an enum int -> a word, or the raw value."""
    if isinstance(raw, str):
        return DIRECTIONS.get(raw.upper(), raw.lower())
    return DIRECTIONS.get(raw, str(raw))


def decode_param(value: Any, distribution_json: str | None) -> Any:
    """The value a trial actually used, from what Optuna stored.

    Optuna stores every parameter as a float: a categorical is the *index* of
    its choice, an int is a float that happens to be whole. The distribution
    beside it says which, so the decode needs no archived code.
    """
    if not distribution_json:
        return value
    try:
        spec = json.loads(distribution_json)
    except (TypeError, ValueError):
        return value
    name = spec.get("name", "")
    attrs = spec.get("attributes", {}) or {}
    if name == "CategoricalDistribution":
        choices = attrs.get("choices")
        if isinstance(choices, list) and isinstance(value, (int, float)):
            index = int(value)
            if 0 <= index < len(choices):
                return choices[index]
        return value
    if name == "IntDistribution" or name == "IntUniformDistribution":
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    return value


class OptunaArchive:
    """An Optuna SQLite archive, opened read-only and read as data."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        if not self.path.is_file():
            raise OptunaArchiveError(f"no such Optuna archive: {self.path}")
        try:
            self.conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        except sqlite3.Error as exc:  # pragma: no cover - platform dependent
            raise OptunaArchiveError(f"cannot open {self.path}: {exc}") from exc
        self.conn.row_factory = sqlite3.Row
        missing = [t for t in REQUIRED_TABLES if t not in self._tables()]
        if missing:
            raise OptunaArchiveError(
                f"{self.path} is not an Optuna archive: it has no {', '.join(missing)}. "
                "A sweep database written by this server is read with "
                "isicell_results_* instead."
            )

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "OptunaArchive":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- schema ---------------------------------------------------------

    def _tables(self) -> set[str]:
        rows = self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return {r["name"] for r in rows}

    def _has_distributions(self) -> bool:
        rows = self.conn.execute("PRAGMA table_info(trial_params)").fetchall()
        return any(r["name"] == "distribution_json" for r in rows)

    def _study_id(self, study: str) -> int:
        row = self.conn.execute(
            "SELECT study_id FROM studies WHERE study_name = ?", (study,)
        ).fetchone()
        if row is None:
            raise OptunaArchiveError(
                f"no study {study!r} in {self.path}; it has {self.studies_names()}"
            )
        return int(row["study_id"])

    def studies_names(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT study_name FROM studies ORDER BY study_name"
        ).fetchall()
        return [r["study_name"] for r in rows]

    # -- reads ----------------------------------------------------------

    def studies(self) -> list[dict[str, Any]]:
        """Every study, with how many trials completed and in what direction."""
        out: list[dict[str, Any]] = []
        has_directions = "study_directions" in self._tables()
        for row in self.conn.execute(
            "SELECT study_id, study_name FROM studies ORDER BY study_name"
        ).fetchall():
            study_id = int(row["study_id"])
            states = self.conn.execute(
                "SELECT state, COUNT(*) AS n FROM trials WHERE study_id = ? GROUP BY state",
                (study_id,),
            ).fetchall()
            directions: list[str] = []
            if has_directions:
                directions = [
                    _direction(d["direction"])
                    for d in self.conn.execute(
                        "SELECT direction FROM study_directions WHERE study_id = ? "
                        "ORDER BY objective",
                        (study_id,),
                    ).fetchall()
                ]
            out.append(
                {
                    "study": row["study_name"],
                    "trials": sum(int(s["n"]) for s in states),
                    "byState": {s["state"]: int(s["n"]) for s in states},
                    "directions": directions,
                    "objectives": len(directions) or 1,
                }
            )
        return out

    def trials(
        self, study: str, state: str | None = COMPLETE, limit: int = 200
    ) -> list[dict[str, Any]]:
        """Trials of one study, each with its objective values and parameters."""
        study_id = self._study_id(study)
        sql = "SELECT trial_id, number, state FROM trials WHERE study_id = ?"
        args: list[Any] = [study_id]
        if state is not None:
            sql += " AND state = ?"
            args.append(state)
        sql += " ORDER BY number LIMIT ?"
        args.append(min(limit, MAX_ROWS))
        rows = self.conn.execute(sql, args).fetchall()
        if not rows:
            return []

        ids = [int(r["trial_id"]) for r in rows]
        values = self._values_for(ids)
        params = self._params_for(ids)
        return [
            {
                "trial_id": int(r["trial_id"]),
                "number": int(r["number"]),
                "state": r["state"],
                "values": values.get(int(r["trial_id"]), []),
                "params": params.get(int(r["trial_id"]), {}),
            }
            for r in rows
        ]

    def best(self, study: str, objective: int = 0, n: int = 5) -> list[dict[str, Any]]:
        """The n trials this study scored best on one objective.

        Ranked by the study's own recorded direction, so a maximised objective
        is not silently read as a minimised one.
        """
        direction = "minimize"
        directions = next(
            (s["directions"] for s in self.studies() if s["study"] == study), []
        )
        if directions:
            if objective >= len(directions):
                raise OptunaArchiveError(
                    f"study {study!r} has {len(directions)} objective(s); "
                    f"there is no objective {objective}."
                )
            direction = directions[objective]

        ranked = [
            t for t in self.trials(study, state=COMPLETE, limit=MAX_ROWS)
            if len(t["values"]) > objective and t["values"][objective] is not None
        ]
        ranked.sort(
            key=lambda t: t["values"][objective], reverse=(direction == "maximize")
        )
        return [{**t, "direction": direction, "objective": objective} for t in ranked[:n]]

    def params(self, trial_id: int) -> dict[str, Any]:
        """One trial's parameters, decoded but flat.

        Flat because the mapping into run parameters lived in the suggest
        function, which is archived as source and is not executed here.
        """
        row = self.conn.execute(
            "SELECT trial_id FROM trials WHERE trial_id = ?", (int(trial_id),)
        ).fetchone()
        if row is None:
            raise OptunaArchiveError(f"no trial {trial_id} in {self.path}")
        return self._params_for([int(trial_id)]).get(int(trial_id), {})

    # -- helpers --------------------------------------------------------

    def _values_for(self, trial_ids: list[int]) -> dict[int, list[Any]]:
        placeholders = ",".join("?" * len(trial_ids))
        rows = self.conn.execute(
            f"SELECT trial_id, objective, value FROM trial_values "  # noqa: S608 - ids are ints
            f"WHERE trial_id IN ({placeholders}) ORDER BY trial_id, objective",
            trial_ids,
        ).fetchall()
        out: dict[int, list[Any]] = {}
        for r in rows:
            out.setdefault(int(r["trial_id"]), []).append(r["value"])
        return out

    def _params_for(self, trial_ids: list[int]) -> dict[int, dict[str, Any]]:
        placeholders = ",".join("?" * len(trial_ids))
        column = ", distribution_json" if self._has_distributions() else ""
        rows = self.conn.execute(
            f"SELECT trial_id, param_name, param_value{column} FROM trial_params "  # noqa: S608
            f"WHERE trial_id IN ({placeholders}) ORDER BY trial_id, param_name",
            trial_ids,
        ).fetchall()
        out: dict[int, dict[str, Any]] = {}
        for r in rows:
            spec = r["distribution_json"] if column else None
            out.setdefault(int(r["trial_id"]), {})[r["param_name"]] = decode_param(
                r["param_value"], spec
            )
        return out
