"""Each test introduces exactly one defect into an otherwise valid model."""

from mcp_isicell.domain.models import BuilderData, Destination, State, Transition
from mcp_isicell.domain.validation import validate


def test_a_transition_to_an_undeclared_state_is_an_error(minimal_model, catalogue):
    minimal_model.cell.Transitions[0].destinations[0] = Destination(
        to="Necrotic", condition="oxygen < 0.1"
    )
    report = validate(minimal_model, catalogue)
    assert "E_TRANSITION_UNDEFINED_TARGET" in report.codes()
    assert not report.ok


def test_a_transition_from_an_undeclared_state_is_an_error(minimal_model, catalogue):
    minimal_model.cell.Transitions.append(
        Transition(**{"from": "Quiescent", "destinations": [Destination(to="DeadCell")]})
    )
    report = validate(minimal_model, catalogue)
    assert "E_TRANSITION_UNDEFINED_SOURCE" in report.codes()


def test_a_model_with_no_states_is_an_error(catalogue):
    report = validate(BuilderData(), catalogue)
    assert "E_NO_STATES" in report.codes()


def test_a_model_with_nothing_leaving_cell_instantiation_is_an_error(minimal_model, catalogue):
    minimal_model.cell.initializationTransitions = []
    report = validate(minimal_model, catalogue)
    assert "E_NO_INIT_TRANSITION" in report.codes()


def test_a_duplicated_state_name_is_an_error(minimal_model, catalogue):
    minimal_model.cell.States.append(State(name="Proliferating", code=""))
    report = validate(minimal_model, catalogue)
    assert "E_DUPLICATE_STATE" in report.codes()


def test_a_state_named_like_a_magic_target_is_an_error(minimal_model, catalogue):
    """DeadCell compiles to die(), so it can never also be an enum member."""
    minimal_model.cell.States.append(State(name="DeadCell", code=""))
    report = validate(minimal_model, catalogue)
    assert "E_RESERVED_STATE_NAME" in report.codes()


def test_a_state_name_that_is_not_a_cpp_identifier_is_an_error(minimal_model, catalogue):
    minimal_model.cell.States.append(State(name="2Fast", code=""))
    report = validate(minimal_model, catalogue)
    assert "E_BAD_IDENTIFIER" in report.codes()
