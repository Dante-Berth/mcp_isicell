"""Reachability over the cell state machine.

The entry point is always CellInstantiation: a newborn cell runs init() and then
takes one of `cell.initializationTransitions`. A state nobody can reach is
behaviour code that can never run, which compiles cleanly and does nothing --
worth a warning precisely because the build stays green.
"""

from __future__ import annotations

from collections import deque

from .models import CELL_INSTANTIATION, DEAD_CELL, BuilderData


def outgoing(model: BuilderData) -> dict[str, list[str]]:
    """Adjacency, including the synthetic CellInstantiation source."""
    edges: dict[str, list[str]] = {}
    for t in list(model.cell.initializationTransitions) + list(model.cell.Transitions):
        edges.setdefault(t.from_, []).extend(d.to for d in t.destinations)
    return edges


def reachable(model: BuilderData) -> set[str]:
    """Every state reachable from CellInstantiation."""
    edges = outgoing(model)
    seen: set[str] = set()
    queue: deque[str] = deque([CELL_INSTANTIATION])
    while queue:
        node = queue.popleft()
        for nxt in edges.get(node, ()):
            if nxt in seen:
                continue
            seen.add(nxt)
            queue.append(nxt)
    return seen


def unreachable_states(model: BuilderData) -> list[str]:
    seen = reachable(model)
    return [name for name in model.state_names() if name not in seen]


def dead_end_states(model: BuilderData) -> list[str]:
    """States with no way out. A cell entering one is stuck there for good."""
    edges = outgoing(model)
    out: list[str] = []
    for name in model.state_names():
        if not edges.get(name):
            out.append(name)
    return out


def unconditional_counts(model: BuilderData) -> dict[str, int]:
    """How many empty-condition destinations each source declares.

    buildConditionsCode (PrimoCell.hpp:15-39) sorts unconditional destinations
    last and emits them as `else`. Two of them means the second is unreachable.
    """
    counts: dict[str, int] = {}
    for t in list(model.cell.initializationTransitions) + list(model.cell.Transitions):
        n = sum(1 for d in t.destinations if not d.condition.strip())
        if n:
            counts[t.from_] = counts.get(t.from_, 0) + n
    return counts


def known_targets(model: BuilderData) -> set[str]:
    """Valid transition targets: declared states plus the two magic names."""
    return set(model.state_names()) | {CELL_INSTANTIATION, DEAD_CELL}
