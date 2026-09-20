"""Narrowing a parameter space around the best point found so far.

Scoring ranks the points a sweep tried; the next move is to look more closely
where the best one was. This computes that narrower space and returns it. It
does not run anything -- spending another sweep stays an explicit decision,
made by passing the result to isicell_sweep_run.

The box is centred on the winner and shrunk by a factor, then clipped to the
original bounds. Clipping matters: a winner at the edge of the space would
otherwise produce a box half outside it, silently proposing values the original
sweep had already been told were out of range.
"""

from __future__ import annotations

from typing import Any

from .sampling import CATEGORICAL, FLOAT, INT, Parameter


def refine_parameter(
    parameter: Parameter, centre: Any, shrink: float
) -> tuple[Parameter, dict[str, Any]]:
    """A narrower version of one axis, centred on `centre`.

    Returns the parameter and a note about what happened, because a caller
    deciding whether to spend another sweep wants to see that a bound was
    clipped or that an axis could not be narrowed at all.
    """
    if parameter.kind == CATEGORICAL:
        # There is no "near" a category. Pinning it to the winner would hide
        # the others for good, so the axis is left alone.
        return parameter, {
            "name": parameter.name,
            "narrowed": False,
            "reason": "categorical axes have no neighbourhood to narrow into",
        }

    low, high = float(parameter.low), float(parameter.high)
    if not isinstance(centre, (int, float)):
        return parameter, {
            "name": parameter.name,
            "narrowed": False,
            "reason": f"no numeric winning value for {parameter.name!r}",
        }

    width = (high - low) * shrink
    new_low = float(centre) - width / 2
    new_high = float(centre) + width / 2

    # Slide the box back inside the original bounds before clipping, so a
    # winner near an edge keeps the full narrowed width rather than half of it.
    if new_low < low:
        new_high += low - new_low
        new_low = low
    if new_high > high:
        new_low -= new_high - high
        new_high = high
    new_low, new_high = max(low, new_low), min(high, new_high)

    if parameter.kind == INT:
        new_low, new_high = int(round(new_low)), int(round(new_high))
        if new_high <= new_low:
            # An integer axis can narrow to nothing; keep at least two values
            # so the next sweep still has something to compare.
            new_high = min(int(high), new_low + 1)
            new_low = max(int(low), new_high - 1)
        if new_high <= new_low:
            return parameter, {
                "name": parameter.name,
                "narrowed": False,
                "reason": "the integer range is already as small as it can be",
            }

    narrowed = Parameter(
        name=parameter.name,
        kind=parameter.kind,
        low=new_low,
        high=new_high,
        log=parameter.log,
        step=parameter.step,
    )
    narrowed.validate()
    note = {
        "name": parameter.name,
        "narrowed": True,
        "centre": centre,
        "from": [parameter.low, parameter.high],
        "to": [new_low, new_high],
    }
    if new_low == low or new_high == high:
        note["clipped_to_original_bounds"] = True
    return narrowed, note


def refine(
    parameters: list[Parameter], winner: dict[str, Any], shrink: float = 0.5
) -> tuple[list[Parameter], list[dict[str, Any]]]:
    """Narrow every axis around the winning point.

    `winner` maps the same dotted names the parameters carry to the values that
    scored best. `shrink` is the fraction of the original width to keep.
    """
    if not 0 < shrink <= 1:
        raise ValueError("shrink must be greater than 0 and at most 1")
    if not parameters:
        raise ValueError("there is nothing to refine without parameters")
    out: list[Parameter] = []
    notes: list[dict[str, Any]] = []
    for p in parameters:
        if p.kind not in (FLOAT, INT, CATEGORICAL):  # pragma: no cover - guarded upstream
            raise ValueError(f"unknown kind {p.kind!r}")
        narrowed, note = refine_parameter(p, winner.get(p.name), shrink)
        out.append(narrowed)
        notes.append(note)
    return out, notes
