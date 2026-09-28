"""`cell.initializationCode` was reachable by import and by nothing else.

The field has always existed and codegen has always emitted it, but no tool
wrote it, so a model authored purely through the tool surface could not give a
newborn cell a single value: every attribute began at whatever the generated
member happened to hold. That compiles, runs, and is quietly wrong -- the
failure mode this package exists to remove. Authoring the sterile-inflammation
model hit it, and the only way through was to export the JSON, edit it by hand
and load it back.
"""

import pytest

from mcp_isicell import server
from mcp_isicell.domain import mutations as mut

BIRTH = "polarisation = 0.0;\nlife = lifespanMean;"


@pytest.fixture(autouse=True)
def fresh_session():
    server.SESSION.reset("test")
    yield
    server.SESSION.reset("test")


def test_the_mutation_does_not_touch_the_original(minimal_model):
    before = minimal_model.to_generator_json()
    mut.set_cell_init_code(minimal_model, BIRTH)
    assert minimal_model.to_generator_json() == before


def test_the_birth_block_reaches_the_document_the_generator_reads(minimal_model):
    """mainGenerator.py reads this key off stdin; nothing else carries it."""
    after = mut.set_cell_init_code(minimal_model, BIRTH)
    assert after.to_generator_json()["cell"]["initializationCode"] == BIRTH


def test_setting_it_twice_replaces_rather_than_appends(minimal_model):
    after = mut.set_cell_init_code(minimal_model, BIRTH)
    after = mut.set_cell_init_code(after, "age = 0.0;")
    assert after.cell.initializationCode == "age = 0.0;"


def test_it_can_be_cleared(minimal_model):
    after = mut.set_cell_init_code(minimal_model, BIRTH)
    assert mut.set_cell_init_code(after, "").cell.initializationCode == ""


def test_the_tool_sets_it_and_reports_the_model_back():
    server.isicell_model_new("tumour")
    server.isicell_model_add_state("Alive")
    server.isicell_model_add_transition("CellInstantiation", "Alive")
    out = server.isicell_model_set_cell_init_code(BIRTH)
    assert out["ok"] is True
    assert server.isicell_model_export()["cell"]["initializationCode"] == BIRTH


def test_a_model_authored_only_through_tools_survives_a_save_and_load(tmp_path):
    """The gap that mattered: previously this needed a hand edit between the two."""
    server.isicell_model_new("tumour")
    server.isicell_model_add_state("Alive")
    server.isicell_model_add_transition("CellInstantiation", "Alive")
    server.isicell_model_set_cell_init_code(BIRTH)
    path = tmp_path / "model.json"
    server.isicell_model_save(str(path))
    server.isicell_model_new("scratch")
    server.isicell_model_load(str(path))
    assert server.isicell_model_export()["cell"]["initializationCode"] == BIRTH


def test_a_birth_block_is_validated_like_any_other_cpp():
    """It is emitted C++, so the rules about emitted C++ have to reach it."""
    server.isicell_model_new("tumour")
    server.isicell_model_add_state("Alive")
    server.isicell_model_add_transition("CellInstantiation", "Alive")
    out = server.isicell_model_set_cell_init_code("life = 10;\nif(life &lt; 0) die();")
    assert "E_HTML_ENTITY_IN_CODE" in [f["code"] for f in out["findings"]]
