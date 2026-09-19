"""Read-only access to an isicellpy results database.

Opened through SQLite's read-only URI, so a tool call cannot write to a run's
output even by accident.

Filters are not raw SQL. A caller supplies structured comparisons; this builds
the WHERE clause with bound parameters, so a column name is validated against
the schema and a value is never interpolated into the statement.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ..errors import IsicellError
from .schema import (
    CONDITION,
    ERROR,
    PARAMETERS,
    SEP,
    unflatten,
)

# Comparisons a filter may use. Mapped to SQL operators explicitly so nothing
# the caller writes reaches the statement as text.
OPERATORS = {
    "eq": "=",
    "ne": "!=",
    "lt": "<",
    "le": "<=",
    "gt": ">",
    "ge": ">=",
}

MAX_ROWS = 5000


class ResultsError(IsicellError):
    """The database is missing, unreadable, or asked for something absent."""


@dataclass(frozen=True)
class Filter:
    """One comparison against a column of the queried table.

    `column` is a dotted path as a user writes it (`Cell.Common.cycleDuration`);
    it is translated to the flattened column name before use.
    """

    column: str
    op: str
    value: Any

    def sql_column(self) -> str:
        return self.column.replace(".", SEP)


def _as_filters(raw: Iterable[dict[str, Any]] | None) -> list[Filter]:
    out: list[Filter] = []
    for item in raw or []:
        try:
            f = Filter(column=item["column"], op=item.get("op", "eq"), value=item["value"])
        except (KeyError, TypeError) as exc:
            raise ResultsError(
                "each filter needs 'column' and 'value', and may set 'op' "
                f"({', '.join(sorted(OPERATORS))})"
            ) from exc
        if f.op not in OPERATORS:
            raise ResultsError(f"unknown operator {f.op!r}; use one of {sorted(OPERATORS)}")
        out.append(f)
    return out


class ResultsDatabase:
    """A results file, opened read-only."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        if not self.path.is_file():
            raise ResultsError(f"no such results database: {self.path}")
        try:
            self.conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        except sqlite3.Error as exc:  # pragma: no cover - platform dependent
            raise ResultsError(f"cannot open {self.path}: {exc}") from exc
        self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "ResultsDatabase":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- schema ---------------------------------------------------------

    def tables(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        return [r["name"] for r in rows if not r["name"].startswith("sqlite_")]

    def columns(self, table: str) -> list[str]:
        self._require_table(table)
        rows = self.conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        return [r["name"] for r in rows]

    def count(self, table: str) -> int:
        self._require_table(table)
        return int(self.conn.execute(f'SELECT COUNT(*) AS n FROM "{table}"').fetchone()["n"])

    def _require_table(self, table: str) -> None:
        if table not in self.tables():
            raise ResultsError(f"no table {table!r}; this database has {self.tables()}")

    def _require_column(self, table: str, column: str) -> str:
        cols = self.columns(table)
        if column in cols:
            return column
        raise ResultsError(f"no column {column!r} in {table!r}; it has {cols}")

    # -- reads ----------------------------------------------------------

    def summarize(self) -> dict[str, Any]:
        """Tables, row counts, and the error columns worth optimising against."""
        out: dict[str, Any] = {"path": str(self.path), "tables": {}}
        for table in self.tables():
            out["tables"][table] = {"rows": self.count(table), "columns": self.columns(table)}
        if ERROR in self.tables():
            out["errorColumns"] = [
                c for c in self.columns(ERROR) if not c.startswith("ID_")
            ]
        if PARAMETERS in self.tables():
            out["parameterNames"] = [
                c.replace(SEP, ".") for c in self.columns(PARAMETERS) if c != "ID_PARAMETER"
            ]
        return out

    def get(
        self,
        table: str,
        filters: Iterable[dict[str, Any]] | None = None,
        limit: int = 200,
        nested: bool = True,
    ) -> list[dict[str, Any]]:
        """Rows of a table, optionally filtered on its own columns.

        `parameters` rows come back nested unless nested=False, because that is
        the shape a run actually takes.
        """
        self._require_table(table)
        where, params = self._where(table, _as_filters(filters))
        sql = f'SELECT * FROM "{table}" {where} LIMIT ?'
        rows = self.conn.execute(sql, (*params, min(limit, MAX_ROWS))).fetchall()
        records = [dict(r) for r in rows]
        if table == PARAMETERS and nested:
            return [unflatten(r) for r in records]
        if table == CONDITION:
            return [unflatten(r) for r in records]
        return records

    def _where(self, table: str, filters: list[Filter]) -> tuple[str, list[Any]]:
        if not filters:
            return "", []
        clauses: list[str] = []
        values: list[Any] = []
        for f in filters:
            col = self._require_column(table, f.sql_column())
            clauses.append(f'"{col}" {OPERATORS[f.op]} ?')
            values.append(f.value)
        return "WHERE " + " AND ".join(clauses), values

    def best(
        self, error_name: str, n: int = 5, for_each_condition: bool = False
    ) -> list[dict[str, Any]]:
        """Lowest-scoring parameter sets for one error column.

        Without a condition split this averages an error over conditions and
        replicates, which is what makes two parameter sets comparable.
        """
        self._require_table(ERROR)
        col = self._require_column(ERROR, error_name)
        n = max(1, min(int(n), MAX_ROWS))
        if for_each_condition:
            self._require_column(ERROR, "ID_CONDITION")
            sql = f'''
                WITH ranked AS (
                    SELECT ID_PARAMETER, ID_CONDITION, "{col}" AS score,
                           ROW_NUMBER() OVER (
                               PARTITION BY ID_CONDITION
                               ORDER BY "{col}" ASC, ID_PARAMETER ASC) AS rn
                    FROM "{ERROR}")
                SELECT ID_PARAMETER, ID_CONDITION, score
                FROM ranked WHERE rn <= ? ORDER BY ID_CONDITION, score
            '''
        else:
            sql = f'''
                SELECT ID_PARAMETER, AVG("{col}") AS score
                FROM "{ERROR}"
                GROUP BY ID_PARAMETER
                ORDER BY score ASC
                LIMIT ?
            '''
        return [dict(r) for r in self.conn.execute(sql, (n,)).fetchall()]

    def pareto(self, error_names: list[str]) -> list[dict[str, Any]]:
        """Parameter sets no other set beats on every error at once.

        A set is dominated when another is at least as good everywhere and
        strictly better somewhere; the survivors are the front.
        """
        self._require_table(ERROR)
        if not error_names:
            raise ResultsError("pareto needs at least one error column")
        cols = [self._require_column(ERROR, name) for name in error_names]
        averages = ", ".join(f'AVG("{c}") AS "{c}"' for c in cols)
        dominates = " AND ".join(f't1."{c}" >= t2."{c}"' for c in cols)
        strictly = " OR ".join(f't1."{c}" > t2."{c}"' for c in cols)
        sql = f'''
            WITH avg_errors AS (
                SELECT ID_PARAMETER, {averages}
                FROM "{ERROR}" GROUP BY ID_PARAMETER
            )
            SELECT t1.* FROM avg_errors t1
            LEFT JOIN avg_errors t2
              ON t1.ID_PARAMETER != t2.ID_PARAMETER
             AND {dominates}
             AND ({strictly})
            WHERE t2.ID_PARAMETER IS NULL
            ORDER BY t1.ID_PARAMETER
        '''
        return [dict(r) for r in self.conn.execute(sql).fetchall()]

    def parameters_for(self, id_parameter: int) -> dict[str, Any]:
        """The nested parameter set behind an ID, ready to feed back into a run."""
        self._require_table(PARAMETERS)
        row = self.conn.execute(
            f'SELECT * FROM "{PARAMETERS}" WHERE ID_PARAMETER = ?', (id_parameter,)
        ).fetchone()
        if row is None:
            raise ResultsError(f"no parameter set with ID_PARAMETER = {id_parameter}")
        nested = unflatten(dict(row))
        nested.pop("ID_PARAMETER", None)
        return nested
