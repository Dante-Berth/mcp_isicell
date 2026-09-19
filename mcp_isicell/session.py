"""The model the server is currently authoring.

Held server-side so an LLM can say "add a state" without echoing the whole
document on every call. Mutations are pure functions; this object only rebinds
the current model, so a rejected edit leaves the previous one intact.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .domain.models import BuilderData


@dataclass
class ModelSession:
    name: str = "untitled"
    model: BuilderData = field(default_factory=BuilderData)
    # The build token this model was last compiled under, if any.
    token: str | None = None

    def replace(self, model: BuilderData) -> None:
        """Rebind the current model. The old one is simply dropped."""
        self.model = model

    def reset(self, name: str = "untitled") -> None:
        self.name = name
        self.model = BuilderData()
        self.token = None

    def summary(self) -> dict:
        m = self.model
        return {
            "name": self.name,
            "token": self.token,
            "states": m.state_names(),
            "cellTypes": m.effective_cell_types(),
            "modules": list(m.moduleSelected),
            "transitions": sum(len(t.destinations) for t in m.cell.Transitions),
            "initTransitions": sum(
                len(t.destinations) for t in m.cell.initializationTransitions
            ),
            "cellAttributes": [a.value for a in m.cell.attributes],
            "scenarioAttributes": [a.value for a in m.scenario.attributes],
            "paramInputs": {
                "Cell": [p.value for p in m.paramInputs.Cell],
                "Scenario": [p.value for p in m.paramInputs.Scenario],
            },
            "recorded": [p.value for p in m.CellAttributesRecorded],
        }
