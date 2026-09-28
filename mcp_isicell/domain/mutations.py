"""Authoring operations on a model.

Every function returns a new model and leaves its argument untouched. The server
holds one current model and rebinds it; keeping the operations pure is what makes
them testable without a session and what stops a half-applied edit from leaving
the session model corrupt when validation rejects the result.
"""

from __future__ import annotations

from .models import (
    Attribute,
    BuilderData,
    Destination,
    FunctionCode,
    ParamInput,
    State,
    Transition,
)


def _copy(model: BuilderData) -> BuilderData:
    return model.model_copy(deep=True)


def add_state(model: BuilderData, name: str, code: str = "") -> BuilderData:
    out = _copy(model)
    out.cell.States.append(State(name=name, code=code))
    return out


def set_state_code(model: BuilderData, name: str, code: str) -> BuilderData:
    out = _copy(model)
    for state in out.cell.States:
        if state.name == name:
            state.code = code
            return out
    raise KeyError(f"no such state: {name!r}")


def remove_state(model: BuilderData, name: str) -> BuilderData:
    """Drop a state and every transition that mentions it, so no dangling refs."""
    out = _copy(model)
    out.cell.States = [s for s in out.cell.States if s.name != name]
    for group in (out.cell.Transitions, out.cell.initializationTransitions):
        for t in list(group):
            t.destinations = [d for d in t.destinations if d.to != name]
    out.cell.Transitions = [t for t in out.cell.Transitions if t.from_ != name]
    return out


def add_transition(
    model: BuilderData, source: str, target: str, condition: str = ""
) -> BuilderData:
    """Add source -> target. Routed to initializationTransitions when the source
    is CellInstantiation, because PrimoCell.hpp puts those in init() rather than
    in the nextState() switch."""
    from .models import CELL_INSTANTIATION

    out = _copy(model)
    group = (
        out.cell.initializationTransitions
        if source == CELL_INSTANTIATION
        else out.cell.Transitions
    )
    for t in group:
        if t.from_ == source:
            t.destinations.append(Destination(to=target, condition=condition))
            return out
    group.append(
        Transition(**{"from": source, "destinations": [Destination(to=target, condition=condition)]})
    )
    return out


def add_attribute(
    model: BuilderData,
    scope: str,
    type_: str,
    name: str,
    *,
    is_param: bool = False,
    is_recorded: bool = False,
) -> BuilderData:
    """Declare an attribute on the cell or the scenario.

    `is_param` makes it a runtime input (paramInputs, the MODEL tag in the UI);
    `is_recorded` makes it a Postgres column (CellAttributesRecorded, VIEWER).
    """
    if scope not in {"cell", "scenario"}:
        raise ValueError("scope must be 'cell' or 'scenario'")
    out = _copy(model)
    target = out.cell if scope == "cell" else out.scenario
    target.attributes.append(Attribute(type=type_, value=name, source="user"))
    if is_param:
        bucket = out.paramInputs.Cell if scope == "cell" else out.paramInputs.Scenario
        bucket.append(ParamInput(type=type_, value=name))
    if is_recorded:
        if scope != "cell":
            raise ValueError("only cell attributes can be recorded")
        out.CellAttributesRecorded.append(ParamInput(type=type_, value=name))
    return out


def add_function(model: BuilderData, scope: str, key: str, code: str) -> BuilderData:
    if scope not in {"cell", "scenario"}:
        raise ValueError("scope must be 'cell' or 'scenario'")
    out = _copy(model)
    target = out.cell.functions if scope == "cell" else out.scenario.functions
    target[key] = FunctionCode(code=code)
    return out


def select_module(model: BuilderData, name: str) -> BuilderData:
    out = _copy(model)
    if name not in out.moduleSelected:
        out.moduleSelected.append(name)
    return out


def deselect_module(model: BuilderData, name: str) -> BuilderData:
    out = _copy(model)
    out.moduleSelected = [m for m in out.moduleSelected if m != name]
    return out


def set_scenario_code(
    model: BuilderData, *, init: str | None = None, loop: str | None = None
) -> BuilderData:
    out = _copy(model)
    if init is not None:
        out.scenario.codeInit = init
    if loop is not None:
        out.scenario.codeLoop = loop
    return out


def set_cell_init_code(model: BuilderData, code: str) -> BuilderData:
    """C++ run once at a cell's birth, before it takes its first transition.

    The counterpart of set_scenario_code for the cell. Without it a model
    authored through the tools alone has no way to give a newborn cell a value,
    so every attribute starts at whatever the generated member happens to hold
    -- which is a silent wrong answer, not a build failure.
    """
    out = _copy(model)
    out.cell.initializationCode = code
    return out


def set_cell_types(model: BuilderData, types: list[str]) -> BuilderData:
    out = _copy(model)
    out.cellTypes = list(types)
    return out


def add_enum(model: BuilderData, name: str, values: list[str]) -> BuilderData:
    out = _copy(model)
    out.enums[name] = list(values)
    return out
