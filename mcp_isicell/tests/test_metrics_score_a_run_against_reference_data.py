"""Scoring is arithmetic over two aligned series, so it is checkable exactly."""

import math

import pytest

from mcp_isicell.explore.metrics import METRICS, align, describe_metrics, score

# A run measuring one column at three steps.
RUN = {"0": {"Alive": 10.0}, "10": {"Alive": 20.0}, "20": {"Alive": 30.0}}


def test_an_exact_match_scores_zero_on_every_metric():
    out = score(RUN, RUN, "Alive", metrics=list(METRICS))
    assert all(v == pytest.approx(0.0) for v in out.values())


def test_rmse_is_the_root_mean_square_of_the_differences():
    reference = {"0": {"Alive": 11.0}, "10": {"Alive": 18.0}, "20": {"Alive": 30.0}}
    # differences 1, 2, 0 -> sqrt((1 + 4 + 0) / 3)
    assert score(RUN, reference, "Alive")["rmse"] == pytest.approx(math.sqrt(5 / 3))


def test_mae_is_the_mean_of_the_absolute_differences():
    reference = {"0": {"Alive": 11.0}, "10": {"Alive": 18.0}, "20": {"Alive": 30.0}}
    assert score(RUN, reference, "Alive", ["mae"])["mae"] == pytest.approx(1.0)


def test_max_abs_reports_the_worst_single_step():
    reference = {"0": {"Alive": 11.0}, "10": {"Alive": 18.0}, "20": {"Alive": 30.0}}
    assert score(RUN, reference, "Alive", ["max_abs"])["max_abs"] == pytest.approx(2.0)


def test_final_abs_looks_only_at_the_last_matched_step():
    """The endpoint is the claim; the path there is not being asserted."""
    reference = {"0": {"Alive": 99.0}, "20": {"Alive": 25.0}}
    assert score(RUN, reference, "Alive", ["final_abs"])["final_abs"] == pytest.approx(5.0)


def test_mape_is_relative_to_the_reference():
    reference = {"0": {"Alive": 20.0}}
    assert score(RUN, reference, "Alive", ["mape"])["mape"] == pytest.approx(0.5)


def test_mape_skips_steps_where_the_reference_is_zero():
    """Dividing by a zero reference is undefined, not infinite."""
    reference = {"0": {"Alive": 0.0}, "10": {"Alive": 10.0}}
    assert score(RUN, reference, "Alive", ["mape"])["mape"] == pytest.approx(1.0)


def test_mape_against_an_all_zero_reference_is_refused():
    with pytest.raises(ValueError):
        score(RUN, {"0": {"Alive": 0.0}}, "Alive", ["mape"])


def test_only_steps_present_on_both_sides_are_compared():
    reference = {"10": {"Alive": 20.0}, "999": {"Alive": 1.0}}
    got, want, used = align(RUN, reference, "Alive")
    assert used == [10] and got == [20.0] and want == [20.0]


def test_a_reference_sharing_no_steps_with_the_run_is_refused_with_both_sides():
    with pytest.raises(ValueError) as excinfo:
        score(RUN, {"999": {"Alive": 1.0}}, "Alive")
    message = str(excinfo.value)
    assert "999" in message and "[0, 10, 20]" in message


def test_a_bare_series_works_as_well_as_one_keyed_by_column():
    """A reference may name the column or just give the numbers."""
    assert score(RUN, {"0": 10.0, "10": 20.0, "20": 30.0}, "Alive")["rmse"] == 0.0


def test_a_non_numeric_measurement_is_skipped_rather_than_crashing():
    """An attribute catcher stores a list per step; it cannot be scored."""
    run = {"0": {"Alive": [1, 2]}, "10": {"Alive": 20.0}}
    got, _, used = align(run, {"0": {"Alive": 1.0}, "10": {"Alive": 20.0}}, "Alive")
    assert used == [10] and got == [20.0]


def test_an_unknown_metric_is_refused_and_names_the_real_ones():
    with pytest.raises(ValueError) as excinfo:
        score(RUN, RUN, "Alive", ["sharpe"])
    assert "rmse" in str(excinfo.value)


def test_an_unknown_column_gives_no_comparable_steps_rather_than_a_false_zero():
    with pytest.raises(ValueError):
        score(RUN, {"0": {"Necrotic": 1.0}}, "Necrotic")


def test_every_metric_is_described_for_the_caller():
    described = {d["name"] for d in describe_metrics()}
    assert described == set(METRICS)
    assert all(d["measures"] for d in describe_metrics())
