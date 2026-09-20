"""Shared fixtures.

The model fixtures are deliberately minimal: the smallest thing that is valid,
so a test that adds one defect isolates that defect and nothing else.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_isicell.domain.models import BuilderData
from mcp_isicell.domain.modules import Catalogue

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Keep every test out of the user's real state directory.

    The session autosaves and jobs keep a registry, both under ISICELL_STATE_DIR.
    Without this a test run would overwrite whatever model the user was
    authoring.
    """
    state = tmp_path / "state"
    monkeypatch.setenv("ISICELL_STATE_DIR", str(state))
    monkeypatch.setenv("ISICELL_JOBS_DIR", str(state / "jobs"))
    from mcp_isicell import server

    monkeypatch.setattr(server.SESSION, "path", state / "session.json")
    yield state


@pytest.fixture
def catalogue() -> Catalogue:
    """The real officialModules.json, vendored from the ISiCell distribution."""
    return Catalogue.from_path(FIXTURES / "officialModules.json")


@pytest.fixture
def minimal_model() -> BuilderData:
    """A cell that is born, proliferates, and can die. Validates clean."""
    return BuilderData.model_validate(
        {
            "cellTypes": ["Common"],
            "cell": {
                "States": [
                    {"name": "Proliferating", "code": "this->grow();"},
                ],
                "initializationTransitions": [
                    {
                        "from": "CellInstantiation",
                        "destinations": [{"to": "Proliferating", "condition": ""}],
                    }
                ],
                "Transitions": [
                    {
                        "from": "Proliferating",
                        "destinations": [{"to": "DeadCell", "condition": "getRadius() > 10"}],
                    }
                ],
            },
        }
    )


def write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload))
    return path
