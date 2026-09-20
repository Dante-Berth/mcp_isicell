"""The model the server is currently authoring.

Held server-side so an LLM can say "add a state" without echoing the whole
document on every call. Mutations are pure functions; this object only rebinds
the current model, so a rejected edit leaves the previous one intact.

The session autosaves. Authoring a model takes many calls, and losing all of
them to a restart -- or to the client reconnecting -- would be the most
expensive way this server can fail. `isicell_model_save` remains the explicit
export to a file the user chose; this is just the server not forgetting.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .domain.models import BuilderData

VERSION = 1


@dataclass
class ModelSession:
    name: str = "untitled"
    model: BuilderData = field(default_factory=BuilderData)
    # The build token this model was last compiled under, if any.
    token: str | None = None
    # Where to autosave. None disables it, which is what tests want.
    path: Path | None = None

    def replace(self, model: BuilderData) -> None:
        """Rebind the current model. The old one is simply dropped."""
        self.model = model
        self.save()

    def reset(self, name: str = "untitled") -> None:
        self.name = name
        self.model = BuilderData()
        self.token = None
        self.save()

    def set_token(self, token: str | None) -> None:
        self.token = token
        self.save()

    # -- persistence ----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": VERSION,
            "name": self.name,
            "token": self.token,
            "model": self.model.to_generator_json(),
        }

    def save(self) -> None:
        """Write the session out, atomically. Never raises.

        An autosave that could fail a tool call would turn a full disk into a
        failure to add a state, so a write that cannot happen is simply skipped.
        """
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(self.to_dict(), indent=2))
            temporary.replace(self.path)
        except OSError:
            pass

    def load(self) -> bool:
        """Restore a saved session. True when one was found and read.

        A session written by a newer version, or one that no longer parses, is
        left on disk and ignored rather than deleted -- it is the user's model,
        and a confusing restart is better than a silent loss.
        """
        if self.path is None or not self.path.is_file():
            return False
        try:
            payload = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(payload, dict) or payload.get("version") != VERSION:
            return False
        try:
            model = BuilderData.model_validate(payload.get("model") or {})
        except Exception:
            return False
        self.name = payload.get("name") or "untitled"
        self.token = payload.get("token")
        self.model = model
        return True

    @staticmethod
    def restored(path: Path | None) -> "ModelSession":
        """A session loaded from `path` if one is there, else a fresh one."""
        session = ModelSession(path=path)
        session.load()
        return session

    # -- reporting ------------------------------------------------------

    def summary(self) -> dict[str, Any]:
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
