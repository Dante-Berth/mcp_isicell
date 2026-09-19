"""Recording a sweep into a results database.

Writes the layout isicellpy's DatabaseManager produces, so everything already
built to read one of those reads these too: `parameters` with nested values
flattened by `$`, `condition` with one row per condition, and a measurement
table carrying the ID columns for whichever axes varied.

The reader opens `mode=ro`; this is the only place that opens a database for
writing, and it refuses to touch a file it did not create unless told to append.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..errors import IsicellError
from .schema import CONDITION, ERROR, PARAMETERS, flatten

# SQLite has no column type for these, and a measurement is a number or a label.
SQL_TYPES = {int: "INTEGER", float: "REAL", bool: "INTEGER", str: "TEXT"}


class WriteError(IsicellError):
    """The database cannot be written, or would be overwritten."""


def _sql_type(value: Any) -> str:
    return SQL_TYPES.get(type(value), "TEXT")


def _encode(value: Any) -> Any:
    """Scalars pass through; anything structured becomes JSON, as isicellpy does."""
    if isinstance(value, bool):
        return int(value)
    if value is None or isinstance(value, (int, float, str)):
        return value
    return json.dumps(value)


def _create(conn: sqlite3.Connection, table: str, columns: dict[str, Any]) -> None:
    cols = ", ".join(f'"{name}" {_sql_type(value)}' for name, value in columns.items())
    conn.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({cols})')


def _insert_many(conn: sqlite3.Connection, table: str, rows: Sequence[dict[str, Any]]) -> None:
    if not rows:
        return
    columns = list(rows[0])
    placeholders = ", ".join("?" for _ in columns)
    quoted = ", ".join(f'"{c}"' for c in columns)
    conn.executemany(
        f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders})',
        [tuple(_encode(r.get(c)) for c in columns) for r in rows],
    )


class ResultsWriter:
    """Creates a results database and records a sweep into it."""

    def __init__(self, path: str | Path, overwrite: bool = False):
        self.path = Path(path).expanduser()
        if self.path.exists() and not overwrite:
            raise WriteError(
                f"{self.path} already exists. Choose another path, or pass "
                "overwrite=True to replace it."
            )
        if self.path.exists():
            self.path.unlink()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    def __enter__(self) -> "ResultsWriter":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def write_parameters(self, points: Iterable[dict[str, Any]]) -> None:
        """One row per sampled point, nested values flattened with `$`.

        A point is stored as the dotted names the caller swept, so reading it
        back gives the same paths they wrote.
        """
        rows = []
        for index, point in enumerate(points):
            flat = flatten({k.replace(".", "$"): v for k, v in point.items()})
            rows.append({"ID_PARAMETER": index, **{k: _encode(v) for k, v in flat.items()}})
        if not rows:
            return
        columns = {k: v for row in rows for k, v in row.items()}
        _create(self.conn, PARAMETERS, columns)
        _insert_many(self.conn, PARAMETERS, _align(rows, columns))
        self.conn.execute(
            f'CREATE INDEX IF NOT EXISTS idx_{PARAMETERS}_ID_PARAMETER '
            f'ON "{PARAMETERS}" (ID_PARAMETER)'
        )

    def write_conditions(self, conditions: Iterable[dict[str, Any]]) -> None:
        rows = []
        for index, condition in enumerate(conditions):
            flat = {k.replace(".", "$"): _encode(v) for k, v in condition.items()}
            rows.append({"ID_CONDITION": index, **flat})
        if not rows:
            return
        columns = {k: v for row in rows for k, v in row.items()}
        _create(self.conn, CONDITION, columns)
        _insert_many(self.conn, CONDITION, _align(rows, columns))

    def write_errors(self, rows: Sequence[dict[str, Any]]) -> None:
        """One scored row per run: its ID columns plus a column per metric.

        This is the table isicell_results_best and _pareto read, and the reason
        a sweep with a reference produces a ranked answer rather than a pile of
        measurements.
        """
        self.write_measurements(ERROR, rows)

    def write_measurements(self, table: str, rows: Sequence[dict[str, Any]]) -> None:
        """Measurement rows, one per step per run.

        Rows may disagree about columns -- a model can gain a state between
        runs -- so the table is created over the union and missing values are
        stored as NULL rather than dropping the row.
        """
        if not rows:
            return
        columns: dict[str, Any] = {}
        for row in rows:
            for k, v in row.items():
                if k not in columns or columns[k] is None:
                    columns[k] = v
        _create(self.conn, table, columns)
        _insert_many(self.conn, table, _align(rows, columns))
        for id_column in ("ID_PARAMETER", "ID_CONDITION"):
            if id_column in columns:
                self.conn.execute(
                    f'CREATE INDEX IF NOT EXISTS idx_{table}_{id_column} '
                    f'ON "{table}" ({id_column})'
                )


def _align(rows: Sequence[dict[str, Any]], columns: dict[str, Any]) -> list[dict[str, Any]]:
    """Give every row the same keys, so executemany sees one shape."""
    return [{c: row.get(c) for c in columns} for row in rows]


def measurement_rows(
    data: dict[str, Any], index: dict[str, int], seed: int | None = None
) -> list[dict[str, Any]]:
    """Flatten one run's measurements into rows: one per step.

    `data` is what a catcher produced, keyed by step. A catcher returning a dict
    becomes one column per key; anything else is stored under `value`.
    """
    rows: list[dict[str, Any]] = []
    for step, measured in (data or {}).items():
        row: dict[str, Any] = {"step": int(step), **index}
        if seed is not None:
            row["seed"] = seed
        if isinstance(measured, dict):
            row.update({str(k): _encode(v) for k, v in measured.items()})
        else:
            row["value"] = _encode(measured)
        rows.append(row)
    return rows
