"""Attributing compiler errors Isigen leaves unmarked.

Markers exist for functions, codeInit and codeLoop only (PrimoCell.hpp:182,
Scenario.hpp:39), so an error in a state's behaviour or a transition's condition
arrives with an empty metaInfo. The generated switch still names the element:
both bodies are built from the same caseTemplate (PrimoCell.hpp:18).

The fixture below is the shape mainGenerator.py really produces -- checked
against a generated tree, not written from the template by hand.
"""

from __future__ import annotations

from mcp_isicell.backend.streams import locate_by_case_label

# Trimmed from simulations/<token>/src/cells/PrimoCell.hpp. Line numbers matter,
# so keep the blank lines: the error in the real build landed inside a case body.
GENERATED = """\
class PrimoCell : public Cell {

    /// @ignore
    void behave() {
        switch(this->state) {

            case State::Alive : {
                age += 1.0;
                break;
            }
            case State::Sick : {
                age += notAThing;
                break;
            }

        default:
        {
            cout << "UNSPECIFIED CELL STATE" << endl;
            break;
            }
        }
    }

    /// @ignore
    void nextState() {
        switch(this->state){

            case State::Alive : {
                if(age > bogusVar) this->die();
                break;
            }

        default:
        {
            break;
            }
        }
    }
};
"""

STATE_CODE_LINE = 12  # age += notAThing;
CONDITION_LINE = 29  # if(age > bogusVar) this->die();
BOILERPLATE_LINE = 18  # the default: arm, which no model element owns


def test_an_error_in_a_state_body_names_that_state() -> None:
    found = locate_by_case_label(GENERATED, STATE_CODE_LINE)
    assert found is not None
    assert found["element"] == "Sick"
    assert found["kind"] == "state"
    assert found["model_path"] == "cell.States[Sick].code"


def test_an_error_in_a_transition_condition_names_the_source_state() -> None:
    found = locate_by_case_label(GENERATED, CONDITION_LINE)
    assert found is not None
    assert found["element"] == "Alive"
    assert found["kind"] == "transition"
    assert found["model_path"] == "cell.Transitions[from=Alive].condition"


def test_the_same_state_name_is_a_state_or_a_transition_by_enclosing_function() -> None:
    """Alive appears as a case in both switches; only the function tells them apart."""
    behaviour = locate_by_case_label(GENERATED, 8)
    condition = locate_by_case_label(GENERATED, CONDITION_LINE)
    assert behaviour is not None and condition is not None
    assert behaviour["element"] == condition["element"] == "Alive"
    assert behaviour["kind"] == "state"
    assert condition["kind"] == "transition"


def test_an_error_in_template_boilerplate_is_not_blamed_on_the_last_state() -> None:
    """Past `default:` the switch is Isigen's, so guessing would misattribute it."""
    assert locate_by_case_label(GENERATED, BOILERPLATE_LINE) is None


def test_an_error_outside_the_generated_switches_is_not_attributed() -> None:
    assert locate_by_case_label(GENERATED, 1) is None


def test_attribution_is_marked_as_inferred_rather_than_reported() -> None:
    """A caller must be able to tell a marker from a guess about layout."""
    found = locate_by_case_label(GENERATED, STATE_CODE_LINE)
    assert found is not None
    assert found["attributed_by"] == "case-label"


def test_locating_never_raises_on_source_that_is_not_generated_cpp() -> None:
    for text in ("", "\n\n", "case State::Nope : {", "void behave() {"):
        locate_by_case_label(text, 1)
