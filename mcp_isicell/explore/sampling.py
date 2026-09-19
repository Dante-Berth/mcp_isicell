"""Latin hypercube sampling over a parameter space, in pure Python.

Reimplements LHSIterator's algorithm and its value mapping
(core/external/isicellpy/pythonlib/LHSIterator.py:121-164) so a sweep can be
previewed before any compute is spent, and without numpy.

LHSIterator defines its space by calling a user function with a Trial object.
That is a Python callback, so here the space is declared as data instead: the
same three kinds of parameter, described rather than executed.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any

FLOAT = "float"
INT = "int"
CATEGORICAL = "categorical"
KINDS = (FLOAT, INT, CATEGORICAL)


@dataclass(frozen=True)
class Parameter:
    """One axis of the space. `name` is a dotted path into the run params."""

    name: str
    kind: str
    low: float | None = None
    high: float | None = None
    choices: tuple[Any, ...] = ()
    log: bool = False
    step: float | None = None

    def validate(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"{self.name}: kind must be one of {KINDS}, got {self.kind!r}")
        if self.kind == CATEGORICAL:
            if not self.choices:
                raise ValueError(f"{self.name}: a categorical parameter needs choices")
            return
        if self.low is None or self.high is None:
            raise ValueError(f"{self.name}: a {self.kind} parameter needs low and high")
        if self.high <= self.low:
            raise ValueError(f"{self.name}: high must exceed low")
        if self.log and self.low <= 0:
            raise ValueError(f"{self.name}: a log scale needs a positive low")

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> "Parameter":
        try:
            p = Parameter(
                name=raw["name"],
                kind=raw.get("kind", FLOAT),
                low=raw.get("low"),
                high=raw.get("high"),
                choices=tuple(raw.get("choices", ())),
                log=bool(raw.get("log", False)),
                step=raw.get("step"),
            )
        except KeyError as exc:
            raise ValueError(f"each parameter needs a 'name': {raw!r}") from exc
        p.validate()
        return p


def _lhs_unit(n_params: int, samples: int, rng: random.Random) -> list[list[float]]:
    """The classic LHS grid: one point per stratum per axis, then shuffled.

    Each column is a permutation of `samples` strata, so every axis is evenly
    covered no matter how many axes there are -- the property a plain random
    sample loses as dimensions grow.
    """
    grid: list[list[float]] = [[0.0] * n_params for _ in range(samples)]
    for j in range(n_params):
        column = [(i + rng.random()) / samples for i in range(samples)]
        rng.shuffle(column)
        for i in range(samples):
            grid[i][j] = column[i]
    return grid


def _map_value(p: Parameter, u: float) -> Any:
    """Turn a unit-interval draw into a parameter value.

    Mirrors LHSIterator._map_sample_to_params, including its int truncation:
    an int axis spans low..high inclusive via int(u * (high - low + 1)).
    """
    if p.kind == CATEGORICAL:
        index = min(int(u * len(p.choices)), len(p.choices) - 1)
        return p.choices[index]
    low, high = float(p.low), float(p.high)
    if p.log:
        value = math.exp(u * (math.log(high) - math.log(low)) + math.log(low))
        return int(value) if p.kind == INT else value
    if p.step:
        if p.kind == INT:
            steps = int((high - low) // p.step)
            return int(low + int(u * steps) * p.step)
        steps = int((high - low) / p.step)
        return low + int(u * steps) * p.step
    if p.kind == INT:
        return int(low + min(int(u * (high - low + 1)), high - low))
    return low + u * (high - low)


def sample(
    parameters: list[Parameter], n_samples: int, seed: int | None = None
) -> list[dict[str, Any]]:
    """`n_samples` points over the space, each a flat {dotted name: value}."""
    if n_samples < 1:
        raise ValueError("n_samples must be at least 1")
    if not parameters:
        raise ValueError("a sweep needs at least one parameter")
    for p in parameters:
        p.validate()
    rng = random.Random(seed)
    grid = _lhs_unit(len(parameters), n_samples, rng)
    return [
        {p.name: _map_value(p, row[j]) for j, p in enumerate(parameters)} for row in grid
    ]


def assign(base: dict[str, Any], flat: dict[str, Any]) -> dict[str, Any]:
    """Apply a sampled point onto a copy of the base run params.

    Names are dotted paths -- `Cell.Common.cycleDuration` -- matching how
    parameters are named everywhere else.
    """
    import copy

    out = copy.deepcopy(base)
    for dotted, value in flat.items():
        parts = dotted.split(".")
        cursor = out
        for part in parts[:-1]:
            nxt = cursor.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                cursor[part] = nxt
            cursor = nxt
        cursor[parts[-1]] = value
    return out


def bounds(parameters: list[Parameter]) -> dict[str, Any]:
    """What the space covers, for showing before a sweep is run."""
    out: dict[str, Any] = {}
    for p in parameters:
        if p.kind == CATEGORICAL:
            out[p.name] = {"kind": p.kind, "choices": list(p.choices)}
        else:
            entry = {"kind": p.kind, "low": p.low, "high": p.high}
            if p.log:
                entry["log"] = True
            if p.step:
                entry["step"] = p.step
            out[p.name] = entry
    return out
