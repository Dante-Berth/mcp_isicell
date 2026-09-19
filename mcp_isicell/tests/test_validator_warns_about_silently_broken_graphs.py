"""Warnings cover the defects that compile cleanly and then do nothing."""

from mcp_isicell.domain.models import Destination, State, Transition
from mcp_isicell.domain.validation import validate


def test_a_state_unreachable_from_cell_instantiation_is_flagged(minimal_model, catalogue):
    minimal_model.cell.States.append(State(name="Quiescent", code=""))
    minimal_model.cell.Transitions.append(
        Transition(**{"from": "Quiescent", "destinations": [Destination(to="DeadCell")]})
    )
    report = validate(minimal_model, catalogue)
    assert "W_UNREACHABLE_STATE" in report.codes()
    # Unreachable is a warning, not an error: it builds and runs.
    assert report.ok


def test_a_state_with_no_way_out_is_flagged(minimal_model, catalogue):
    minimal_model.cell.States.append(State(name="Senescent", code=""))
    minimal_model.cell.initializationTransitions[0].destinations.append(
        Destination(to="Senescent", condition="getId() % 2 == 0")
    )
    report = validate(minimal_model, catalogue)
    assert "W_DEAD_END_STATE" in report.codes()


def test_a_second_unconditional_destination_is_flagged_as_dead_code(minimal_model, catalogue):
    """buildConditionsCode emits every unconditional destination as one else."""
    minimal_model.cell.States.append(State(name="Quiescent", code=""))
    minimal_model.cell.Transitions[0].destinations = [
        Destination(to="Quiescent", condition=""),
        Destination(to="DeadCell", condition=""),
    ]
    report = validate(minimal_model, catalogue)
    assert "W_UNORDERED_CONDITIONS" in report.codes()


def test_one_unconditional_destination_is_not_flagged(minimal_model, catalogue):
    minimal_model.cell.Transitions[0].destinations = [Destination(to="DeadCell", condition="")]
    report = validate(minimal_model, catalogue)
    assert "W_UNORDERED_CONDITIONS" not in report.codes()
