from mcp_isicell.domain.models import Destination
from mcp_isicell.domain.validation import validate


def test_a_minimal_well_formed_model_produces_no_errors(minimal_model, catalogue):
    report = validate(minimal_model, catalogue)
    assert report.ok, report.codes()


def test_transitions_to_dead_cell_and_cell_instantiation_are_accepted(minimal_model, catalogue):
    """The two magic names are valid targets though they are not declared states."""
    minimal_model.cell.Transitions[0].destinations.append(
        Destination(to="CellInstantiation", condition="getId() == 0")
    )
    report = validate(minimal_model, catalogue)
    assert "E_TRANSITION_UNDEFINED_TARGET" not in report.codes()
