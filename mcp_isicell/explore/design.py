"""Planning a sweep: points, conditions and replicates.

A sweep is a full crossing of three axes, in the order MultiSimu uses
(core/external/isicellpy/pythonlib/MultiSimu.py:316) -- parameter, then
condition, then replicate. Laying it out is arithmetic, so the whole plan can be
shown before a single simulation runs.

Two things MultiSimu does through user code are done here as data instead:

  - a *condition* is a dict of dotted-path overrides, not a tuple a Python
    function maps onto params
  - a *replicate* is the same run under a different seed, derived from the
    sweep's own seed so the whole design replays from one number

Indices are attached only for axes that actually vary, which is what MultiSimu's
autoIndex does (:299-312) and what keeps a single-point run from carrying three
meaningless columns.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from .sampling import Parameter, assign, sample

# A sweep of this size is almost always a mistake rather than an intention.
MAX_RUNS = 2000


def derive_seed(sweep_seed: int, id_parameter: int, id_condition: int, id_replicate: int) -> int:
    """A per-run seed fixed by the sweep's seed and the run's position.

    Hashing rather than adding keeps neighbouring runs from getting neighbouring
    seeds, which matters because a poor RNG can correlate nearby streams. The
    result is stable across processes and platforms, so a sweep replays.
    """
    key = f"{sweep_seed}:{id_parameter}:{id_condition}:{id_replicate}".encode()
    digest = hashlib.sha256(key).digest()
    # Simu takes a C int; stay inside 2**31 - 1.
    return int.from_bytes(digest[:4], "big") % 2147483647


@dataclass(frozen=True)
class Run:
    """One simulation: which point, which condition, which replicate."""

    id_parameter: int
    id_condition: int
    id_replicate: int
    params: dict[str, Any]
    point: dict[str, Any]
    condition: dict[str, Any]
    seed: int | None

    def index(self, *, with_parameter: bool, with_condition: bool, with_replicate: bool) -> dict:
        """The ID columns this run carries, for the axes that vary."""
        out: dict[str, int] = {}
        if with_parameter:
            out["ID_PARAMETER"] = self.id_parameter
        if with_condition:
            out["ID_CONDITION"] = self.id_condition
        if with_replicate:
            out["ID_REPLICAT"] = self.id_replicate
        return out


@dataclass(frozen=True)
class Design:
    """A planned sweep, before anything has run."""

    runs: tuple[Run, ...]
    points: tuple[dict[str, Any], ...]
    conditions: tuple[dict[str, Any], ...]
    replicates: int
    seed: int | None
    bounds: dict[str, Any] = field(default_factory=dict)

    @property
    def with_parameter(self) -> bool:
        return len(self.points) > 1

    @property
    def with_condition(self) -> bool:
        return len(self.conditions) > 1 or bool(self.conditions and any(self.conditions))

    @property
    def with_replicate(self) -> bool:
        return self.replicates > 1

    def summary(self) -> dict[str, Any]:
        """Counts, named so they cannot collide with the lists themselves."""
        return {
            "totalRuns": len(self.runs),
            "totalPoints": len(self.points),
            "totalConditions": len(self.conditions),
            "replicates": self.replicates,
            "seed": self.seed,
            "varies": {
                "parameter": self.with_parameter,
                "condition": self.with_condition,
                "replicate": self.with_replicate,
            },
        }


def plan(
    base_params: dict[str, Any],
    parameters: list[Parameter] | None = None,
    n_samples: int = 1,
    conditions: list[dict[str, Any]] | None = None,
    replicates: int = 1,
    seed: int | None = None,
) -> Design:
    """Lay out every run a sweep would perform.

    `conditions` are dicts of dotted-path overrides applied after the sampled
    point, so a condition wins over a swept parameter when both name the same
    path -- a condition is the fixed experimental setup, the sweep explores
    within it.
    """
    if replicates < 1:
        raise ValueError("replicates must be at least 1")
    parameters = parameters or []
    points = (
        sample(parameters, n_samples, seed=seed) if parameters else [{}]
    )
    condition_list = list(conditions) if conditions else [{}]
    total = len(points) * len(condition_list) * replicates
    if total > MAX_RUNS:
        raise ValueError(
            f"this design is {total} runs ({len(points)} points x "
            f"{len(condition_list)} conditions x {replicates} replicates); "
            f"the limit is {MAX_RUNS}. Narrow it before running."
        )

    runs: list[Run] = []
    for id_p, point in enumerate(points):
        for id_c, condition in enumerate(condition_list):
            merged_point = assign(base_params, point)
            merged = assign(merged_point, condition) if condition else merged_point
            for id_r in range(replicates):
                runs.append(
                    Run(
                        id_parameter=id_p,
                        id_condition=id_c,
                        id_replicate=id_r,
                        params=merged,
                        point=point,
                        condition=condition,
                        seed=None if seed is None else derive_seed(seed, id_p, id_c, id_r),
                    )
                )
    from .sampling import bounds as describe_bounds

    return Design(
        runs=tuple(runs),
        points=tuple(points),
        conditions=tuple(condition_list),
        replicates=replicates,
        seed=seed,
        bounds=describe_bounds(parameters) if parameters else {},
    )
