"""Scoring a run against reference data, without caller-supplied code.

isicellpy scores with a user function. That is the one thing it does with caller
Python that this server has no answer for, so instead a metric is named and the
reference is data: a series of observed values per step, per measured column.

Every metric is a pure function of two aligned sequences, so scoring is testable
arithmetic and a score can be explained rather than merely produced.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Iterable, Sequence


def _rmse(a: Sequence[float], b: Sequence[float]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)) / len(a))


def _mae(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def _mape(a: Sequence[float], b: Sequence[float]) -> float:
    """Mean absolute percentage error, against the reference.

    Undefined where the reference is zero, so those points are skipped; if every
    reference value is zero there is nothing to be a percentage of.
    """
    pairs = [(x, y) for x, y in zip(a, b) if y != 0]
    if not pairs:
        raise ValueError("mape is undefined when every reference value is zero")
    return sum(abs(x - y) / abs(y) for x, y in pairs) / len(pairs)


def _max_abs(a: Sequence[float], b: Sequence[float]) -> float:
    return max(abs(x - y) for x, y in zip(a, b))


def _final_abs(a: Sequence[float], b: Sequence[float]) -> float:
    """Absolute difference at the last step only.

    Useful when the endpoint is the claim -- a final population, a plateau --
    and the path there is not being asserted.
    """
    return abs(a[-1] - b[-1])


METRICS: dict[str, tuple[Callable[[Sequence[float], Sequence[float]], float], str]] = {
    "rmse": (_rmse, "root mean squared error over the matched steps"),
    "mae": (_mae, "mean absolute error over the matched steps"),
    "mape": (_mape, "mean absolute percentage error, skipping zero references"),
    "max_abs": (_max_abs, "the largest absolute difference at any matched step"),
    "final_abs": (_final_abs, "absolute difference at the last matched step only"),
}


def describe_metrics() -> list[dict[str, str]]:
    return [{"name": name, "measures": doc} for name, (_, doc) in METRICS.items()]


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    return None


def align(
    measured: dict[str, Any], reference: dict[str, Any], column: str
) -> tuple[list[float], list[float], list[int]]:
    """Line a run's measurements up with the reference, step by step.

    Only steps present on both sides and numeric on both sides are compared. A
    reference that names steps the run never measured is a mismatch worth
    surfacing, not something to interpolate over silently.
    """
    steps = sorted({int(s) for s in reference} & {int(s) for s in measured})
    got: list[float] = []
    want: list[float] = []
    used: list[int] = []
    for step in steps:
        cell = measured.get(str(step), measured.get(step))
        expected = reference.get(str(step), reference.get(step))
        if isinstance(cell, dict):
            cell = cell.get(column)
        if isinstance(expected, dict):
            expected = expected.get(column)
        a, b = _numeric(cell), _numeric(expected)
        if a is None or b is None:
            continue
        got.append(a)
        want.append(b)
        used.append(step)
    return got, want, used


def score(
    measured: dict[str, Any],
    reference: dict[str, Any],
    column: str,
    metrics: Iterable[str] = ("rmse",),
) -> dict[str, float]:
    """Score one run's measurements for one column against the reference."""
    names = list(metrics)
    unknown = [n for n in names if n not in METRICS]
    if unknown:
        raise ValueError(
            f"unknown metric(s) {unknown}; use one of {sorted(METRICS)}"
        )
    got, want, used = align(measured, reference, column)
    if not used:
        raise ValueError(
            f"no steps to compare for column {column!r}: the reference names "
            f"{sorted(int(s) for s in reference)[:8]} and the run measured "
            f"{sorted(int(s) for s in measured)[:8]}"
        )
    return {name: METRICS[name][0](got, want) for name in names}
