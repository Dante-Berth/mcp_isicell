"""Narrowing is arithmetic, so what it proposes is checkable exactly."""

import pytest

from mcp_isicell.explore.refine import refine
from mcp_isicell.explore.sampling import Parameter


def axis(**kw):
    return Parameter.from_dict(kw)


FLOAT_AXIS = [axis(name="g", kind="float", low=0.0, high=10.0)]


def test_a_winner_in_the_middle_gives_a_box_centred_on_it():
    out, notes = refine(FLOAT_AXIS, {"g": 5.0}, shrink=0.5)
    assert (out[0].low, out[0].high) == (2.5, 7.5)
    assert notes[0]["narrowed"] is True and notes[0]["centre"] == 5.0


def test_shrink_is_the_fraction_of_the_original_width_kept():
    out, _ = refine(FLOAT_AXIS, {"g": 5.0}, shrink=0.2)
    assert out[0].high - out[0].low == pytest.approx(2.0)


def test_a_shrink_of_one_keeps_the_original_width():
    out, _ = refine(FLOAT_AXIS, {"g": 5.0}, shrink=1.0)
    assert (out[0].low, out[0].high) == (0.0, 10.0)


def test_a_winner_at_the_edge_keeps_the_full_narrowed_width_inside_the_bounds():
    """Clipping alone would hand back half a box and halve the next sweep."""
    out, notes = refine(FLOAT_AXIS, {"g": 0.0}, shrink=0.4)
    assert (out[0].low, out[0].high) == (0.0, 4.0)
    assert out[0].high - out[0].low == pytest.approx(4.0)
    assert notes[0]["clipped_to_original_bounds"] is True


def test_a_winner_at_the_top_edge_is_slid_inside_too():
    out, _ = refine(FLOAT_AXIS, {"g": 10.0}, shrink=0.4)
    assert (out[0].low, out[0].high) == (6.0, 10.0)


def test_the_narrowed_box_never_leaves_the_original_bounds():
    for centre in (0.0, 0.1, 5.0, 9.9, 10.0):
        out, _ = refine(FLOAT_AXIS, {"g": centre}, shrink=0.5)
        assert out[0].low >= 0.0 and out[0].high <= 10.0


def test_an_integer_axis_stays_integral():
    out, _ = refine([axis(name="n", kind="int", low=1, high=100)], {"n": 50}, shrink=0.5)
    assert isinstance(out[0].low, int) and isinstance(out[0].high, int)
    assert (out[0].low, out[0].high) == (25, 75)


def test_an_integer_axis_never_narrows_to_a_single_value():
    """A range of one leaves the next sweep nothing to compare."""
    out, _ = refine([axis(name="n", kind="int", low=1, high=4)], {"n": 2}, shrink=0.01)
    assert out[0].high > out[0].low


def test_a_categorical_axis_is_left_alone_and_says_why():
    space = [axis(name="mode", kind="categorical", choices=["a", "b", "c"])]
    out, notes = refine(space, {"mode": "b"}, shrink=0.5)
    assert out[0].choices == ("a", "b", "c")
    assert notes[0]["narrowed"] is False and "neighbourhood" in notes[0]["reason"]


def test_an_axis_the_winner_says_nothing_about_is_left_alone():
    out, notes = refine(FLOAT_AXIS, {}, shrink=0.5)
    assert (out[0].low, out[0].high) == (0.0, 10.0)
    assert notes[0]["narrowed"] is False


def test_a_log_axis_keeps_its_log_scale():
    space = [axis(name="k", kind="float", low=1e-3, high=1e3, log=True)]
    out, _ = refine(space, {"k": 1.0}, shrink=0.5)
    assert out[0].log is True and out[0].low > 0


def test_every_refined_axis_is_still_a_valid_parameter():
    space = [
        axis(name="a", kind="float", low=0, high=1),
        axis(name="b", kind="int", low=1, high=10),
        axis(name="c", kind="categorical", choices=[1, 2]),
    ]
    out, _ = refine(space, {"a": 0.5, "b": 5, "c": 1}, shrink=0.5)
    for p in out:
        p.validate()  # must not raise


def test_a_shrink_outside_zero_to_one_is_refused():
    for bad in (0, -0.5, 1.5):
        with pytest.raises(ValueError):
            refine(FLOAT_AXIS, {"g": 5.0}, shrink=bad)


def test_refining_nothing_is_refused():
    with pytest.raises(ValueError):
        refine([], {}, shrink=0.5)


def test_refining_repeatedly_converges_on_the_winner():
    """Each pass should bracket the target more tightly than the last."""
    space = FLOAT_AXIS
    target = 3.7
    widths = []
    for _ in range(5):
        space, _ = refine(space, {"g": target}, shrink=0.5)
        widths.append(space[0].high - space[0].low)
        assert space[0].low <= target <= space[0].high
    assert widths == sorted(widths, reverse=True)
    assert widths[-1] < 1.0
