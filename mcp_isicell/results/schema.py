"""The shape isicellpy writes into a results database, reimplemented.

DatabaseManager lives inside a built simulation and its sibling Enums.py is not
valid Python until codegen has run, so importing it would make results readable
only when a build exists. These are pure functions over the same conventions, so
a database can be read with nothing installed but this package.

Conventions reproduced from core/external/isicellpy/pythonlib/DatabaseManager.py:

  - `parameters` columns are nested dicts flattened with `$`, lists as JSON
    (`_flatten_dict`, :549)
  - `condition` columns holding JSON keep it as a string in SQLite
  - filters are written with dotted paths (`parameters.Cell.Common.x`) which are
    rewritten to `$` before hitting SQL (`_parse_json_conditions`, :160)
  - a referenced table is LEFT JOINed on ID_PARAMETER, else ID_CONDITION
    (`_build_clauses`, :200)
"""

from __future__ import annotations

import json
import re
from typing import Any

SEP = "$"

# Tables isicellpy creates by name; everything else is model-defined data.
PARAMETERS = "parameters"
CONDITION = "condition"
ERROR = "error"

# `table.a.b.c` -> the table, then the dotted path into its flattened columns.
DOTTED = re.compile(r"(\w+)\.(\w+(?:\.\w+)*)")


def flatten(d: dict, parent: str = "", sep: str = SEP) -> dict[str, Any]:
    """Nested dict -> flat columns. Lists become JSON, as isicellpy stores them."""
    items: list[tuple[str, Any]] = []
    for k, v in d.items():
        key = f"{parent}{sep}{k}" if parent else str(k)
        if isinstance(v, dict):
            items.extend(flatten(v, key, sep).items())
        elif isinstance(v, list):
            items.append((key, json.dumps(v)))
        else:
            items.append((key, v))
    return dict(items)


def unflatten(row: dict[str, Any], sep: str = SEP) -> dict[str, Any]:
    """Flat columns -> nested dict, decoding JSON-looking strings back to lists."""
    out: dict[str, Any] = {}
    for key, value in row.items():
        parts = str(key).split(sep)
        cursor = out
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
            if not isinstance(cursor, dict):  # a scalar already claimed this path
                break
        else:
            if isinstance(value, str) and value[:1] in ("[", "{"):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    pass
            cursor[parts[-1]] = value
    return out


def dotted_to_columns(expression: str) -> str:
    """Rewrite `parameters.Cell.Common.x` to `parameters.Cell$Common$x`.

    Only the path after the table name is joined with `$`; the table stays a
    real SQL qualifier so the join builder can still see which tables a filter
    refers to.
    """

    def repl(m: re.Match[str]) -> str:
        return f"{m.group(1)}.{m.group(2).replace('.', SEP)}"

    return DOTTED.sub(repl, expression)


def referenced_tables(expression: str) -> set[str]:
    """Table names a filter mentions, before aliasing."""
    return {m.group(1) for m in DOTTED.finditer(expression or "")}
