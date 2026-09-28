"""`life &lt; 0` is not a condition, and the validator has to say so.

This exists because it happened: a transition authored through the tool layer
arrived carrying `&lt;` instead of `<`, and the model validated clean, saved
clean and would have reached g++ as garbage in a generated file that names no
model element. The entity survives any path that renders and re-reads code --
a copy out of a rendered page, an escaped tool call -- so the rule covers every
place a model contributes C++, not only the place it was first seen.
"""

from mcp_isicell.domain.models import (
    BuilderData,
    Cell,
    Destination,
    FunctionCode,
    Scenario,
    State,
    Transition,
)
from mcp_isicell.domain.validation import validate

CODE = "E_HTML_ENTITY_IN_CODE"


def _model(**cell_kwargs) -> BuilderData:
    """A model that is otherwise clean, so any finding is the one under test."""
    cell = Cell(
        States=[State(name="Alive", code="age += 1.0;\n")],
        initializationTransitions=[
            Transition(**{"from": "CellInstantiation"}, destinations=[Destination(to="Alive")])
        ],
        Transitions=[
            Transition(**{"from": "Alive"}, destinations=[Destination(to="DeadCell", condition="age > 50")])
        ],
    )
    for key, value in cell_kwargs.items():
        setattr(cell, key, value)
    return BuilderData(cell=cell)


def _codes(model: BuilderData) -> list[str]:
    return [f.code for f in validate(model).findings]


def test_a_clean_model_is_not_flagged():
    """The rule must not fire on the `>` and `&&` that real conditions are made of."""
    model = _model()
    model.cell.Transitions[0].destinations[0].condition = "age > 50 && isType(Common)"
    assert CODE not in _codes(model)


def test_an_entity_in_a_transition_condition_is_an_error():
    """The case that got through: an escaped `<` in a condition."""
    model = _model()
    model.cell.Transitions[0].destinations[0].condition = "life &lt; 0"
    finding = next(f for f in validate(model).findings if f.code == CODE)
    assert finding.severity == "error"
    assert "&lt;" in finding.message
    # The path has to name the edge, because a model has many conditions and
    # the generated file will name none of them.
    assert finding.path == "cell.Transitions[Alive -> DeadCell].condition"


def test_an_entity_in_an_initialization_condition_is_an_error():
    """Init transitions are a separate list and were the easiest place to miss."""
    model = _model()
    model.cell.initializationTransitions[0].destinations[0].condition = "count &gt; 0"
    assert CODE in _codes(model)


def test_an_entity_in_the_cell_birth_block_is_an_error():
    birth = "life = lifespanMean;\nif(life &lt; 0) die();\n"
    assert CODE in _codes(_model(initializationCode=birth))


def test_an_entity_in_state_code_is_an_error():
    model = _model()
    model.cell.States[0].code = "if(age &gt; 3) {\n\tdie();\n}"
    assert CODE in _codes(model)


def test_an_entity_in_a_cell_function_is_an_error():
    model = _model()
    model.cell.functions = {"f1": FunctionCode(code="void grow() {\n\tif(a &amp;& b) age += 1;\n}")}
    assert CODE in _codes(model)


def test_an_entity_in_scenario_code_is_an_error():
    model = _model()
    model.scenario = Scenario(codeInit="for(int i = 0; i &lt; 10; i++) {\n\tcreateCell();\n}")
    assert CODE in _codes(model)


def test_an_entity_in_a_scenario_function_is_an_error():
    model = _model()
    model.scenario = Scenario(
        functions={"f1": FunctionCode(code="void seed() {\n\tif(n &lt; 5) createCell();\n}")}
    )
    assert CODE in _codes(model)


def test_a_numeric_entity_is_caught_too():
    """`&#60;` is the same `<` by another spelling, and the same failure."""
    model = _model()
    model.cell.States[0].code = "if(age &#60; 3) {\n\tdie();\n}"
    assert CODE in _codes(model)


def test_one_finding_per_block_however_many_entities_it_carries():
    """A block with four escapes is one thing to fix, not four."""
    model = _model()
    model.cell.States[0].code = "if(a &lt; b &amp;& c &gt; d) {\n\tdie();\n}"
    entity_findings = [f for f in validate(model).findings if f.code == CODE]
    assert len(entity_findings) == 1
    # All of them are named, so the fix does not have to be found by eye.
    assert all(e in entity_findings[0].message for e in ("&lt;", "&amp;", "&gt;"))
