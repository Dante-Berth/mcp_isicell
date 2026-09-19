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
