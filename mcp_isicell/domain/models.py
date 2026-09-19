"""The builderData document, typed.

Field names and nesting mirror `web/CellBuilder/app/inputs_outputs/getFinalJson.js`
exactly, because this structure is fed verbatim to `scripts/mainGenerator.py` on
stdin and every isigen template block indexes into it by these names. A rename
here is a silent codegen failure there, so the aliases are not cosmetic.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Not states. They compile to method calls -- `this->init()` and `this->die()`
# respectively -- at core/src/cells/PrimoCell.hpp:29-32.
CELL_INSTANTIATION = "CellInstantiation"
DEAD_CELL = "DeadCell"
MAGIC_STATES = frozenset({CELL_INSTANTIATION, DEAD_CELL})

# core/src/core/Model.hpp:52 falls back to cellConfig["Common"] for any cell type
# that does not name a value, and an empty cellTypes list generates
# `enum Type { Common }`. So "Common" is always a valid cell type.
COMMON_CELL_TYPE = "Common"


class Attribute(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type: str
    value: str
    # 'core' attributes describe what the engine already provides and are skipped
    # in codegen (PrimoCell.hpp:176-178 emits only source != 'core').
    source: Literal["core", "user"] = "user"


class ParamInput(BaseModel):
    type: str
    value: str


class Destination(BaseModel):
    to: str
    # A C++ boolean expression. Empty means unconditional; buildConditionsCode
    # sorts unconditional destinations last and emits them as the else branch.
    condition: str = ""


class Transition(BaseModel):
    from_: str = Field(alias="from")
    destinations: list[Destination] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class State(BaseModel):
    name: str
    # C++ executed every step while the cell is in this state.
    code: str = ""


class FunctionCode(BaseModel):
    code: str


class Scenario(BaseModel):
    attributes: list[Attribute] = Field(default_factory=list)
    codeInit: str = ""
    codeLoop: str = ""
    functions: dict[str, FunctionCode] = Field(default_factory=dict)


class Cell(BaseModel):
    attributes: list[Attribute] = Field(default_factory=list)
    initializationCode: str = ""
    initializationTransitions: list[Transition] = Field(default_factory=list)
    States: list[State] = Field(default_factory=list)
    Transitions: list[Transition] = Field(default_factory=list)
    functions: dict[str, FunctionCode] = Field(default_factory=dict)


class ParamInputs(BaseModel):
    Cell: list[ParamInput] = Field(default_factory=list)
    Scenario: list[ParamInput] = Field(default_factory=list)


class BuilderData(BaseModel):
    """A complete ISiCell model definition."""

    model_config = ConfigDict(populate_by_name=True)

    moduleSelected: list[str] = Field(default_factory=list)
    customModule: dict[str, dict[str, Any]] = Field(default_factory=dict)
    enums: dict[str, list[str]] = Field(default_factory=dict)
    cellTypes: list[str] = Field(default_factory=list)
    scenario: Scenario = Field(default_factory=Scenario)
    cell: Cell = Field(default_factory=Cell)
    paramInputs: ParamInputs = Field(default_factory=ParamInputs)
    CellAttributesRecorded: list[ParamInput] = Field(default_factory=list)
    CellFunctionsRecorded: list[ParamInput] = Field(default_factory=list)

    def to_generator_json(self) -> dict[str, Any]:
        """The exact dict to pipe to mainGenerator.py on stdin.

        `by_alias` matters: Transition.from_ must serialise back to "from".
        """
        return self.model_dump(by_alias=True)

    def state_names(self) -> list[str]:
        return [s.name for s in self.cell.States]

    def effective_cell_types(self) -> list[str]:
        """cellTypes as the generator sees them (Model.hpp:10)."""
        return list(self.cellTypes) if self.cellTypes else [COMMON_CELL_TYPE]
