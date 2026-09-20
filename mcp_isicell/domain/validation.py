"""Offline validation of a builderData model.

Every rule here exists because of a specific way the generate-compile pipeline
fails, and each carries the source location it prevents failing at. The point is
to turn a three-minute C++ build failure -- reported against generated source the
user never wrote -- into an instant finding against the model.

Pure: no I/O, no subprocesses. `catalogue` and `installed_modules` are passed in.
"""

from __future__ import annotations

import re

from .findings import Finding, ValidationReport
from .models import (
    CELL_INSTANTIATION,
    COMMON_CELL_TYPE,
    DEAD_CELL,
    MAGIC_STATES,
    BuilderData,
)
from .modules import Catalogue
from . import statemachine as sm

# A C++ identifier. State, type and enum names all become enum members.
IDENTIFIER = re.compile(r"^[A-Za-z_]\w*$")

# PrimoCell.hpp:182 does re.search(r'(\w+[\d_\w]+)\s*\(', code).group(1) with no
# guard, so code that never matches crashes codegen with a bare AttributeError.
FUNCTION_NAME = re.compile(r"(\w+[\d_\w]+)\s*\(")

# SchemeSQL.hpp convert() maps these to SQL columns; anything else raises.
SQL_SCALARS = frozenset({"bool", "double", "int"})


def validate(
    model: BuilderData,
    catalogue: Catalogue | None = None,
    installed_modules: set[str] | None = None,
) -> ValidationReport:
    """Check a model for anything that would break codegen or the C++ build.

    `installed_modules` is the set of module DirNames actually present on disk.
    Pass None to skip that check (e.g. when no ISiCell checkout is configured).
    """
    findings: list[Finding] = []
    findings += _check_states(model)
    findings += _check_transitions(model)
    findings += _check_identifiers(model)
    findings += _check_functions(model)
    findings += _check_recorded(model)
    findings += _check_cell_types(model)
    findings += _check_scenario_code(model)
    findings += _check_modules(model, catalogue, installed_modules)
    findings += _check_graph(model)
    return ValidationReport(findings)


def _check_states(model: BuilderData) -> list[Finding]:
    out: list[Finding] = []
    if not model.cell.States:
        out.append(
            Finding(
                "error",
                "E_NO_STATES",
                "cell.States",
                "The model declares no states, so Model.hpp generates `enum State { }`, "
                "which is not valid C++.",
                "Add at least one state with isicell_model_add_state.",
            )
        )
    seen: set[str] = set()
    for i, state in enumerate(model.cell.States):
        if state.name in seen:
            out.append(
                Finding(
                    "error",
                    "E_DUPLICATE_STATE",
                    f"cell.States[{i}]",
                    f"State {state.name!r} is declared more than once, which generates a "
                    "duplicate enum member and a duplicate switch case.",
                    "Rename or remove the duplicate.",
                )
            )
        seen.add(state.name)
        if state.name in MAGIC_STATES:
            out.append(
                Finding(
                    "error",
                    "E_RESERVED_STATE_NAME",
                    f"cell.States[{i}]",
                    f"{state.name!r} is reserved: it compiles to a method call "
                    "(init() or die()), not to a State enum value.",
                    "Pick another name; use it as a transition target instead.",
                )
            )
    return out


def _check_transitions(model: BuilderData) -> list[Finding]:
    out: list[Finding] = []
    declared = set(model.state_names())
    targets = sm.known_targets(model)

    if not model.cell.initializationTransitions:
        out.append(
            Finding(
                "error",
                "E_NO_INIT_TRANSITION",
                "cell.initializationTransitions",
                "Nothing leaves CellInstantiation, so a newborn cell never enters a state.",
                f"Add a transition from {CELL_INSTANTIATION} to a declared state.",
            )
        )

    groups = (
        ("cell.initializationTransitions", model.cell.initializationTransitions),
        ("cell.Transitions", model.cell.Transitions),
    )
    for label, transitions in groups:
        for i, t in enumerate(transitions):
            # A source must be a real state; CellInstantiation is only valid as a
            # source in initializationTransitions, where PrimoCell.hpp puts it in
            # init() rather than in the nextState() switch.
            allowed_sources = declared | ({CELL_INSTANTIATION} if "initialization" in label else set())
            if t.from_ not in allowed_sources:
                out.append(
                    Finding(
                        "error",
                        "E_TRANSITION_UNDEFINED_SOURCE",
                        f"{label}[{i}].from",
                        f"Transition source {t.from_!r} is not a declared state, so "
                        "nextState() generates `case State::" + t.from_ + "` for an "
                        "enum member that does not exist.",
                        "Declare the state, or correct the name.",
                    )
                )
            for j, d in enumerate(t.destinations):
                if d.to not in targets:
                    out.append(
                        Finding(
                            "error",
                            "E_TRANSITION_UNDEFINED_TARGET",
                            f"{label}[{i}].destinations[{j}].to",
                            f"Transition target {d.to!r} is neither a declared state nor "
                            f"{CELL_INSTANTIATION}/{DEAD_CELL}, so the generated "
                            f"`State::{d.to}` does not compile.",
                            "Declare the state, or use DeadCell to kill the cell.",
                        )
                    )
    return out


def _check_identifiers(model: BuilderData) -> list[Finding]:
    out: list[Finding] = []
    for i, state in enumerate(model.cell.States):
        if not IDENTIFIER.match(state.name):
            out.append(
                Finding(
                    "error",
                    "E_BAD_IDENTIFIER",
                    f"cell.States[{i}].name",
                    f"State name {state.name!r} is not a valid C++ identifier and cannot "
                    "become an enum member.",
                    "Use letters, digits and underscores, not starting with a digit.",
                )
            )
    for i, ct in enumerate(model.cellTypes):
        if not IDENTIFIER.match(ct):
            out.append(
                Finding(
                    "error",
                    "E_BAD_IDENTIFIER",
                    f"cellTypes[{i}]",
                    f"Cell type {ct!r} is not a valid C++ identifier.",
                    "Use letters, digits and underscores, not starting with a digit.",
                )
            )
    for name, values in model.enums.items():
        if not IDENTIFIER.match(name):
            out.append(
                Finding(
                    "error",
                    "E_BAD_IDENTIFIER",
                    f"enums.{name}",
                    f"Enum name {name!r} is not a valid C++ identifier.",
                    "Use letters, digits and underscores, not starting with a digit.",
                )
            )
        for v in values:
            if not IDENTIFIER.match(v):
                out.append(
                    Finding(
                        "error",
                        "E_BAD_IDENTIFIER",
                        f"enums.{name}",
                        f"Enum value {v!r} is not a valid C++ identifier.",
                        "Use letters, digits and underscores, not starting with a digit.",
                    )
                )
    return out


def _check_functions(model: BuilderData) -> list[Finding]:
    out: list[Finding] = []
    for label, functions in (
        ("cell.functions", model.cell.functions),
        ("scenario.functions", model.scenario.functions),
    ):
        for key, fn in functions.items():
            if not FUNCTION_NAME.search(fn.code or ""):
                out.append(
                    Finding(
                        "error",
                        "E_FUNC_NO_NAME",
                        f"{label}.{key}",
                        "No function name could be parsed from this code. Codegen does an "
                        "unguarded regex match on it (PrimoCell.hpp:182) and dies with an "
                        "opaque AttributeError.",
                        "Write a full definition, e.g. `void divide() { ... }`.",
                    )
                )
    return out


def _check_recorded(model: BuilderData) -> list[Finding]:
    """Recorded attributes become SQL columns; their types must map."""
    enum_names = set(model.enums)
    out: list[Finding] = []
    for i, attr in enumerate(model.CellAttributesRecorded):
        if attr.type not in SQL_SCALARS and attr.type not in enum_names:
            out.append(
                Finding(
                    "error",
                    "E_RECORDED_ATTR_TYPE",
                    f"CellAttributesRecorded[{i}]",
                    f"Type {attr.type!r} has no SQL mapping; SchemeSQL.hpp convert() raises "
                    "`Unknown type` for anything outside bool/double/int and enums.",
                    "Record a scalar or enum attribute, or drop it from the recorded set.",
                )
            )
    return out


# `s->createCell()` is the idiom inside a Scenario *function*, where the scenario
# arrives as a pointer. codeInit and codeLoop are inlined into Scenario::init and
# Scenario::loop instead (core/src/scenarios/Scenario.hpp:168,179), where the
# scope already is the Scenario, so `s` is simply undeclared.
SCENARIO_POINTER = re.compile(r"\bs\s*->")

# ... unless the code declares its own `s` first, in which case the deref is that
# variable's and nothing is wrong. Matches `Scenario* s =`, `auto* s =`, `auto s =`.
SCENARIO_LOCAL_S = re.compile(r"\b\w+\s*\*?\s*\bs\b\s*=")


def _check_scenario_code(model: BuilderData) -> list[Finding]:
    """Scenario init/loop code runs as the Scenario, not on a pointer to one."""
    out: list[Finding] = []
    for label, code in (
        ("scenario.codeInit", model.scenario.codeInit),
        ("scenario.codeLoop", model.scenario.codeLoop),
    ):
        text = code or ""
        if SCENARIO_POINTER.search(text) and not SCENARIO_LOCAL_S.search(text):
            out.append(
                Finding(
                    "error",
                    "E_SCENARIO_POINTER",
                    label,
                    "This code calls through `s->`, but codeInit and codeLoop are inlined "
                    "into Scenario::init and Scenario::loop "
                    "(core/src/scenarios/Scenario.hpp:168,179), where the scope is the "
                    "Scenario itself. g++ fails with `'s' was not declared in this scope`.",
                    "Call the member directly, e.g. `createCell();` rather than "
                    "`s->createCell();`.",
                )
            )
    return out


def _check_cell_types(model: BuilderData) -> list[Finding]:
    out: list[Finding] = []
    if model.cellTypes and COMMON_CELL_TYPE not in model.cellTypes:
        out.append(
            Finding(
                "warning",
                "W_NO_COMMON_CELL_TYPE",
                "cellTypes",
                "No 'Common' cell type. Model.hpp:52 falls back to cellConfig[\"Common\"] "
                "for any parameter a type does not name, so run params usually need a "
                "Cell.Common block anyway.",
                "Add 'Common', or give every type a full parameter block at run time.",
            )
        )
    return out


def _check_modules(
    model: BuilderData,
    catalogue: Catalogue | None,
    installed: set[str] | None,
) -> list[Finding]:
    if catalogue is None:
        return []
    out: list[Finding] = []
    known = set(catalogue.names())
    for i, name in enumerate(model.moduleSelected):
        if name not in known:
            out.append(
                Finding(
                    "error",
                    "E_UNKNOWN_MODULE",
                    f"moduleSelected[{i}]",
                    f"{name!r} is not in officialModules.json. mainGenerator.py matches on "
                    "the module Name, not its DirName.",
                    f"One of: {', '.join(sorted(known))}.",
                )
            )
            continue
        if installed is not None:
            mod = catalogue.by_name(name)
            assert mod is not None
            if mod.DirName not in installed:
                out.append(
                    Finding(
                        "error",
                        "E_MODULE_MISSING_ON_DISK",
                        f"moduleSelected[{i}]",
                        f"Module {name!r} is selected but core/modules/cellModules/"
                        f"{mod.DirName}/config.json is absent, so mainGenerator.py:86 "
                        "raises FileNotFoundError. Its submodule was never checked out.",
                        "Run `git submodule update --init --recursive` in the ISiCell checkout.",
                    )
                )

    for name, dep in catalogue.unmet(model.moduleSelected):
        out.append(
            Finding(
                "error",
                "E_MODULE_DEPS",
                "moduleSelected",
                f"Module {name!r} needs capability {dep!r}, which nothing selected provides.",
                "Select a module providing it, or drop the dependent module.",
            )
        )

    spaces = catalogue.spaces(model.moduleSelected)
    if len(spaces) > 1:
        out.append(
            Finding(
                "error",
                "E_SPACE_DIM_CONFLICT",
                "moduleSelected",
                f"Both {' and '.join(sorted(spaces))} modules are selected. The generated "
                "world cannot be both two- and three-dimensional.",
                "Keep the modules of a single dimensionality.",
            )
        )
    return out


def _check_graph(model: BuilderData) -> list[Finding]:
    out: list[Finding] = []
    for name in sm.unreachable_states(model):
        out.append(
            Finding(
                "warning",
                "W_UNREACHABLE_STATE",
                "cell.States",
                f"State {name!r} is not reachable from {CELL_INSTANTIATION}; its behaviour "
                "code can never run. This compiles cleanly, which is why it is easy to miss.",
                "Add a transition into it, or remove it.",
            )
        )
    for name in sm.dead_end_states(model):
        out.append(
            Finding(
                "warning",
                "W_DEAD_END_STATE",
                "cell.Transitions",
                f"State {name!r} has no outgoing transitions, so a cell entering it stays "
                "there for the rest of the run and never dies.",
                f"Add a transition out, to another state or to {DEAD_CELL}.",
            )
        )
    for source, count in sm.unconditional_counts(model).items():
        if count > 1:
            out.append(
                Finding(
                    "warning",
                    "W_UNORDERED_CONDITIONS",
                    "cell.Transitions",
                    f"State {source!r} declares {count} unconditional destinations. "
                    "buildConditionsCode emits them as a single else branch, so all but "
                    "one are dead code that silently never fires.",
                    "Give all but one of them a condition.",
                )
            )
    return out
