"""The validator is the thing that reports problems; it may not become one."""

from hypothesis import given, settings
from hypothesis import strategies as st

from mcp_isicell.domain.models import BuilderData, Destination, State, Transition
from mcp_isicell.domain.validation import validate

names = st.text(min_size=0, max_size=6)


@settings(max_examples=200, deadline=None)
@given(
    states=st.lists(names, max_size=5),
    edges=st.lists(st.tuples(names, names), max_size=5),
)
def test_the_validator_never_raises_whatever_the_model(states, edges):
    model = BuilderData(
        cell={
            "States": [State(name=n) for n in states],
            "Transitions": [
                Transition(**{"from": a, "destinations": [Destination(to=b)]}) for a, b in edges
            ],
        }
    )
    validate(model)  # must not raise


@settings(max_examples=100, deadline=None)
@given(target=st.text(min_size=1, max_size=6).filter(lambda s: s.isidentifier()))
def test_a_target_outside_the_declared_set_always_reports_exactly_once(target):
    """Every dangling target yields one finding -- no more, no fewer."""
    model = BuilderData(
        cell={
            "States": [State(name="Alive")],
            "initializationTransitions": [
                Transition(**{"from": "CellInstantiation", "destinations": [Destination(to="Alive")]})
            ],
            "Transitions": [
                Transition(**{"from": "Alive", "destinations": [Destination(to=target)]})
            ],
        }
    )
    report = validate(model)
    dangling = [f for f in report.findings if f.code == "E_TRANSITION_UNDEFINED_TARGET"]
    expected = 0 if target in {"Alive", "CellInstantiation", "DeadCell"} else 1
    assert len(dangling) == expected
