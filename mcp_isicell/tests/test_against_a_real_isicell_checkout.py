"""The real pipeline, against a real ISiCell checkout.

Marked `integration` and deselected by default: these need ISICELL_ROOT to point
at a checkout, and the build ones additionally need the system libraries the
Dockerfile installs (libboost-all-dev, libcgal-dev) plus cmake, make and a C++17
compiler.

    ISICELL_ROOT=/path/to/ISiCell uv run pytest -m integration

The codegen test needs none of that beyond Python: mainGenerator.py copies the
tree and runs Isigen before it ever invokes cmake, so what the server produces
can be checked even where the C++ cannot be built.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from mcp_isicell.domain.models import BuilderData

pytestmark = pytest.mark.integration

MODEL = {
    "cellTypes": ["Common"],
    "cell": {
        "attributes": [{"type": "double", "value": "age", "source": "user"}],
        "States": [{"name": "Alive", "code": "age += 1.0;"}],
        "initializationTransitions": [
            {"from": "CellInstantiation", "destinations": [{"to": "Alive", "condition": ""}]}
        ],
        "Transitions": [
            {"from": "Alive", "destinations": [{"to": "DeadCell", "condition": "age > 50"}]}
        ],
    },
    "paramInputs": {"Cell": [{"type": "double", "value": "age"}], "Scenario": []},
}


@pytest.fixture
def checkout() -> Path:
    raw = os.environ.get("ISICELL_ROOT")
    if not raw:
        pytest.skip("set ISICELL_ROOT to an ISiCell checkout to run integration tests")
    root = Path(raw).expanduser()
    if not (root / "scripts" / "mainGenerator.py").is_file():
        pytest.skip(f"{root} has no scripts/mainGenerator.py")
    return root


def run_generator(root: Path, token: str, mode: str) -> subprocess.CompletedProcess:
    """Drive the real mainGenerator.py exactly as the backend does."""
    return subprocess.run(
        ["python3", str(root / "scripts" / "mainGenerator.py"), token, mode],
        input=json.dumps(BuilderData.model_validate(MODEL).to_generator_json()),
        capture_output=True,
        text=True,
        cwd=str(root),
        timeout=900,
    )


@pytest.fixture
def generated(checkout: Path):
    """Generate into the real checkout, then clean up after."""
    token = "mcp_integration_test"
    target = checkout / "simulations" / token
    if target.exists():
        shutil.rmtree(target)
    result = run_generator(checkout, token, "sql")
    yield result, target
    if target.exists():
        shutil.rmtree(target)


def test_the_model_authored_here_becomes_the_expected_generated_cpp(generated):
    """Every claim the validator makes about codegen, confirmed by running it."""
    _, target = generated
    model_hpp = (target / "src" / "core" / "Model.hpp").read_text()
    primo = (target / "src" / "cells" / "PrimoCell.hpp").read_text()

    # A declared state becomes an enum member.
    assert "enum State { Alive}" in model_hpp
    # Its code lands in the behave() switch.
    assert "case State::Alive" in primo and "age += 1.0;" in primo
    # DeadCell compiles to die(), not to a State value -- the magic-name rule.
    assert "if(age > 50) this->die();" in primo
    assert "State::DeadCell" not in primo
    # A user attribute is emitted, with the Cell.Common fallback.
    assert "double age;" in primo
    assert 'cellConfig["Common"]["age"]' in model_hpp


def test_the_generator_records_the_checksum_of_what_it_was_given(generated):
    _, target = generated
    assert (target / "checksum.txt").read_text().strip()


def test_a_generated_tree_carries_the_pieces_a_build_needs(generated):
    _, target = generated
    for expected in ("CMakeLists.txt", "src", "external/MecaCell", "config/runsParams"):
        assert (target / expected).exists(), expected


def test_no_isigen_directives_survive_into_the_generated_source(generated):
    """keepIsigen=False, so a leftover block would be a template that never fired."""
    _, target = generated
    for path in (target / "src").rglob("*.hpp"):
        assert "isigen:insert@" not in path.read_text(), path


@pytest.mark.skipif(
    shutil.which("cmake") is None, reason="cmake is needed to build the generated project"
)
def test_the_generated_project_compiles(generated):
    """The whole pipeline. Needs Boost and CGAL, which the Dockerfile installs."""
    result, target = generated
    if result.returncode != 0:
        tail = (result.stdout or "")[-2000:]
        if "Could NOT find" in tail:
            pytest.skip(f"a system dependency is missing:\n{tail[-400:]}")
        pytest.fail(f"generation failed:\n{tail}")
    assert (target / "build" / "console").is_file()
