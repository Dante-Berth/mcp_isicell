"""The server surface, driven the way an LLM drives it.

These call the tool functions directly rather than over stdio: the transport is
the SDK's business, the behaviour is ours.
"""

import json

import pytest

from mcp.server.mcpserver.exceptions import ToolError

from mcp_isicell import server


@pytest.fixture(autouse=True)
def fresh_session():
    server.SESSION.reset("test")
    yield
    server.SESSION.reset("test")


def test_a_new_model_is_reported_as_broken_with_an_actionable_reason():
    """An empty model has no states, and the reply says so rather than failing."""
    out = server.isicell_model_new("tumour")
    assert out["ok"] is False
    assert "E_NO_STATES" in [f["code"] for f in out["findings"]]


def test_authoring_a_two_state_model_reaches_a_clean_validation():
    server.isicell_model_new("tumour")
    server.isicell_model_set_cell_types(["Common"])
    server.isicell_model_add_state("Proliferating", "this->grow();")
    server.isicell_model_add_state("Quiescent", "// wait")
    server.isicell_model_add_transition("CellInstantiation", "Proliferating")
    server.isicell_model_add_transition("Proliferating", "Quiescent", "crowded()")
    server.isicell_model_add_transition("Quiescent", "Proliferating", "!crowded()")
    out = server.isicell_model_add_transition("Proliferating", "DeadCell", "age > 100")
    assert out["ok"], [f["code"] for f in out["findings"]]
    assert set(out["model"]["states"]) == {"Proliferating", "Quiescent"}


def test_every_authoring_call_returns_findings_so_a_mistake_surfaces_at_once():
    server.isicell_model_new("t")
    server.isicell_model_add_state("Alive")
    out = server.isicell_model_add_transition("Alive", "Necrotic")
    assert "E_TRANSITION_UNDEFINED_TARGET" in [f["code"] for f in out["findings"]]


def test_a_finding_carries_the_path_and_a_suggestion():
    server.isicell_model_new("t")
    server.isicell_model_add_state("Alive")
    out = server.isicell_model_add_transition("Alive", "Necrotic")
    finding = next(f for f in out["findings"] if f["code"] == "E_TRANSITION_UNDEFINED_TARGET")
    assert finding["path"].startswith("cell.Transitions[")
    assert finding["suggestion"]


def test_compiling_a_model_with_errors_is_refused_before_anything_is_spawned():
    """The refusal names the findings, so the caller can act without guessing."""
    server.isicell_model_new("t")  # no states
    with pytest.raises(ToolError) as excinfo:
        server.isicell_build_compile()
    assert "E_NO_STATES" in str(excinfo.value)


def test_a_deliberate_failure_keeps_its_message_instead_of_a_generic_one():
    """Raised bare, an IsicellError would reach the client as 'Error executing tool'."""
    server.isicell_model_new("t")
    server.isicell_model_add_state("Alive")
    server.isicell_model_add_transition("CellInstantiation", "Alive")
    with pytest.raises(ToolError) as excinfo:
        server.isicell_model_set_state_code("Nonexistent", "// x")
    assert "Nonexistent" in str(excinfo.value)


def test_export_round_trips_through_import():
    server.isicell_model_new("t")
    server.isicell_model_add_state("Alive")
    server.isicell_model_add_transition("CellInstantiation", "Alive")
    exported = server.isicell_model_export()
    server.isicell_model_new("other")
    server.isicell_model_import(exported)
    assert server.isicell_model_export() == exported


def test_a_model_survives_a_save_and_load(tmp_path):
    server.isicell_model_new("t")
    server.isicell_model_add_state("Alive")
    path = tmp_path / "model.json"
    server.isicell_model_save(str(path))
    server.isicell_model_new("cleared")
    server.isicell_model_load(str(path))
    assert server.SESSION.model.state_names() == ["Alive"]


def test_loading_a_demo_file_unwraps_its_final_json(tmp_path):
    """Demo files wrap the model under finalJson alongside svg and bpmn."""
    path = tmp_path / "demo.json"
    path.write_text(
        json.dumps(
            {
                "description": "a demo",
                "svg": "<svg/>",
                "finalJson": {"cell": {"States": [{"name": "Alive", "code": ""}]}},
            }
        )
    )
    server.isicell_model_load(str(path))
    assert server.SESSION.model.state_names() == ["Alive"]


def test_modules_are_listed_with_their_dependencies_even_with_no_checkout(monkeypatch):
    """The catalogue is bundled, so discovery works before ISiCell is configured."""
    monkeypatch.delenv("ISICELL_ROOT", raising=False)
    modules = server.isicell_list_modules()
    grimes = next(m for m in modules if m["name"] == "Grimes Oxygen")
    assert grimes["dependencies"] == ["Spheroid Manager"]


def test_status_reports_that_nothing_is_configured_rather_than_failing(monkeypatch):
    monkeypatch.delenv("ISICELL_ROOT", raising=False)
    status = server.isicell_status()
    assert status["configured"] is False
    assert status["modules_known"]


def test_selecting_a_module_absent_from_disk_is_flagged(monkeypatch, tmp_path):
    """The empty-submodule case, surfaced at authoring time."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "mainGenerator.py").write_text("")
    (tmp_path / "core" / "modules" / "cellModules").mkdir(parents=True)
    monkeypatch.setenv("ISICELL_ROOT", str(tmp_path))
    server.isicell_model_new("t")
    server.isicell_model_add_state("Alive")
    server.isicell_model_add_transition("CellInstantiation", "Alive")
    out = server.isicell_model_select_module("Diffusion2D")
    assert "E_MODULE_MISSING_ON_DISK" in [f["code"] for f in out["findings"]]
