"""Authoring operations are pure: the session model is rebound, never edited."""

from mcp_isicell.domain import mutations as mut
from mcp_isicell.domain.validation import validate


def test_adding_a_state_does_not_touch_the_original(minimal_model):
    before = minimal_model.to_generator_json()
    mut.add_state(minimal_model, "Quiescent", "// idle")
    assert minimal_model.to_generator_json() == before


def test_adding_a_state_returns_a_model_that_has_it(minimal_model):
    after = mut.add_state(minimal_model, "Quiescent")
    assert "Quiescent" in after.state_names()


def test_a_transition_from_cell_instantiation_lands_in_the_initialization_group(minimal_model):
    """PrimoCell.hpp puts those in init(), not in the nextState() switch."""
    after = mut.add_transition(minimal_model, "CellInstantiation", "Proliferating", "true")
    assert all(t.from_ != "CellInstantiation" for t in after.cell.Transitions)
    assert any(t.from_ == "CellInstantiation" for t in after.cell.initializationTransitions)


def test_two_transitions_from_one_source_share_a_single_group(minimal_model):
    after = mut.add_state(minimal_model, "Quiescent")
    after = mut.add_transition(after, "Proliferating", "Quiescent", "crowded()")
    sources = [t.from_ for t in after.cell.Transitions]
    assert sources.count("Proliferating") == 1
    proliferating = next(t for t in after.cell.Transitions if t.from_ == "Proliferating")
    assert len(proliferating.destinations) == 2


def test_removing_a_state_removes_every_transition_that_named_it(minimal_model, catalogue):
    """Otherwise the model is left with a dangling target it never had before."""
    after = mut.add_state(minimal_model, "Quiescent")
    after = mut.add_transition(after, "Proliferating", "Quiescent", "crowded()")
    after = mut.remove_state(after, "Quiescent")
    report = validate(after, catalogue)
    assert "E_TRANSITION_UNDEFINED_TARGET" not in report.codes()


def test_a_param_attribute_reaches_param_inputs(minimal_model):
    after = mut.add_attribute(minimal_model, "cell", "double", "cycleDuration", is_param=True)
    assert [p.value for p in after.paramInputs.Cell] == ["cycleDuration"]


def test_a_recorded_attribute_reaches_the_recorded_set(minimal_model):
    after = mut.add_attribute(minimal_model, "cell", "double", "oxygen", is_recorded=True)
    assert [p.value for p in after.CellAttributesRecorded] == ["oxygen"]


def test_selecting_the_same_module_twice_keeps_one_entry(minimal_model):
    after = mut.select_module(minimal_model, "Diffusion2D")
    after = mut.select_module(after, "Diffusion2D")
    assert after.moduleSelected == ["Diffusion2D"]
