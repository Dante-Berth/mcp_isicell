"""LHS, reimplemented so a sweep can be previewed before compute is spent."""

import pytest

from mcp_isicell.explore.sampling import Parameter, assign, bounds, sample


def p(**kw):
    return Parameter.from_dict(kw)


def test_the_requested_number_of_points_comes_back():
    pts = sample([p(name="a", kind="float", low=0, high=1)], 12, seed=1)
    assert len(pts) == 12


def test_every_stratum_of_every_axis_is_hit_exactly_once():
    """This is what LHS buys over random sampling: even coverage per axis."""
    n = 20
    params = [
        p(name="a", kind="float", low=0.0, high=1.0),
        p(name="b", kind="float", low=0.0, high=1.0),
    ]
    pts = sample(params, n, seed=7)
    for axis in ("a", "b"):
        strata = sorted(int(pt[axis] * n) for pt in pts)
        assert strata == list(range(n))


def test_values_stay_inside_the_declared_bounds():
    params = [p(name="x", kind="float", low=2.5, high=7.5)]
    for pt in sample(params, 50, seed=3):
        assert 2.5 <= pt["x"] <= 7.5


def test_an_int_axis_yields_ints_across_the_inclusive_range():
    pts = sample([p(name="n", kind="int", low=1, high=4)], 200, seed=5)
    values = {pt["n"] for pt in pts}
    assert values <= {1, 2, 3, 4}
    assert values == {1, 2, 3, 4}
    assert all(isinstance(pt["n"], int) for pt in pts)


def test_a_log_axis_spans_its_decades_rather_than_clustering_at_the_top():
    pts = sample([p(name="k", kind="float", low=1e-3, high=1e3, log=True)], 60, seed=11)
    values = sorted(pt["k"] for pt in pts)
    assert values[0] < 1e-2
    assert values[-1] > 1e2
    # Half the points below 1.0 is what a log scale means; a linear scale would
    # put almost everything above it.
    below = sum(1 for v in values if v < 1.0)
    assert 20 <= below <= 40


def test_a_categorical_axis_only_ever_yields_declared_choices():
    pts = sample([p(name="c", kind="categorical", choices=["a", "b", "c"])], 30, seed=2)
    assert {pt["c"] for pt in pts} <= {"a", "b", "c"}


def test_a_categorical_draw_at_the_top_of_the_interval_stays_in_range():
    """int(1.0 * len(choices)) would index off the end."""
    from mcp_isicell.explore.sampling import _map_value

    param = p(name="c", kind="categorical", choices=["a", "b"])
    assert _map_value(param, 0.999999) == "b"
    assert _map_value(param, 1.0) == "b"


def test_the_same_seed_gives_the_same_points():
    a = sample([p(name="x", kind="float", low=0, high=1)], 10, seed=42)
    b = sample([p(name="x", kind="float", low=0, high=1)], 10, seed=42)
    assert a == b


def test_different_seeds_give_different_points():
    a = sample([p(name="x", kind="float", low=0, high=1)], 10, seed=1)
    b = sample([p(name="x", kind="float", low=0, high=1)], 10, seed=2)
    assert a != b


def test_a_space_with_no_parameters_is_refused():
    with pytest.raises(ValueError):
        sample([], 5)


def test_an_inverted_range_is_refused():
    with pytest.raises(ValueError):
        p(name="x", kind="float", low=10, high=1)


def test_a_log_axis_starting_at_zero_is_refused():
    with pytest.raises(ValueError):
        p(name="x", kind="float", low=0, high=10, log=True)


def test_a_categorical_axis_with_no_choices_is_refused():
    with pytest.raises(ValueError):
        p(name="x", kind="categorical")


def test_an_unknown_kind_is_refused():
    with pytest.raises(ValueError):
        p(name="x", kind="gaussian", low=0, high=1)


def test_a_sampled_point_lands_on_the_right_nested_run_parameter():
    base = {"input": {"Cell": {"Common": {"cycleDuration": 1.0}}, "Scenario": {"dt": 360}}}
    out = assign(base, {"input.Cell.Common.cycleDuration": 18.0})
    assert out["input"]["Cell"]["Common"]["cycleDuration"] == 18.0
    assert out["input"]["Scenario"]["dt"] == 360


def test_assigning_does_not_touch_the_base_params():
    base = {"input": {"Scenario": {"dt": 360}}}
    assign(base, {"input.Scenario.dt": 60})
    assert base["input"]["Scenario"]["dt"] == 360


def test_assigning_creates_a_path_that_did_not_exist():
    out = assign({}, {"input.Cell.Common.x": 3})
    assert out["input"]["Cell"]["Common"]["x"] == 3


def test_bounds_describe_the_space_without_sampling_it():
    described = bounds(
        [
            p(name="x", kind="float", low=0, high=1, log=False),
            p(name="c", kind="categorical", choices=[1, 2]),
        ]
    )
    assert described["x"] == {"kind": "float", "low": 0, "high": 1}
    assert described["c"] == {"kind": "categorical", "choices": [1, 2]}
