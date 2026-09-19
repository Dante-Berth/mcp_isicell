"""Laying out a sweep is arithmetic, so it is fully checkable with no ISiCell."""

import pytest

from mcp_isicell.explore.design import MAX_RUNS, derive_seed, plan
from mcp_isicell.explore.sampling import Parameter


def axis(**kw):
    return Parameter.from_dict(kw)


BASE = {"input": {"Cell": {"Common": {}}, "Scenario": {"dt": 360}}}
ONE_AXIS = [axis(name="input.Cell.Common.x", kind="float", low=0, high=1)]


def test_a_design_is_the_full_crossing_of_its_three_axes():
    d = plan(BASE, ONE_AXIS, n_samples=4, conditions=[{"a": 1}, {"a": 2}], replicates=3)
    assert len(d.runs) == 4 * 2 * 3


def test_runs_come_out_in_parameter_then_condition_then_replicate_order():
    """The order MultiSimu._combine uses, so a batch means the same thing here."""
    d = plan(BASE, ONE_AXIS, n_samples=2, conditions=[{"a": 1}, {"a": 2}], replicates=2)
    triples = [(r.id_parameter, r.id_condition, r.id_replicate) for r in d.runs]
    assert triples == sorted(triples)
    assert triples[:4] == [(0, 0, 0), (0, 0, 1), (0, 1, 0), (0, 1, 1)]


def test_a_single_point_with_no_conditions_is_one_run():
    d = plan(BASE)
    assert len(d.runs) == 1
    assert d.runs[0].params["input"]["Scenario"]["dt"] == 360


def test_only_the_axes_that_vary_get_an_index_column():
    """A one-point run carrying three ID columns would be three lies."""
    d = plan(BASE, replicates=1)
    run = d.runs[0]
    assert run.index(
        with_parameter=d.with_parameter,
        with_condition=d.with_condition,
        with_replicate=d.with_replicate,
    ) == {}


def test_replicates_alone_index_only_the_replicate_axis():
    d = plan(BASE, replicates=3)
    assert d.with_replicate and not d.with_parameter and not d.with_condition
    assert set(
        d.runs[0].index(with_parameter=False, with_condition=False, with_replicate=True)
    ) == {"ID_REPLICAT"}


def test_a_condition_is_applied_on_top_of_the_base_params():
    d = plan(BASE, conditions=[{"input.Scenario.MOI": 5}])
    assert d.runs[0].params["input"]["Scenario"]["MOI"] == 5
    assert d.runs[0].params["input"]["Scenario"]["dt"] == 360


def test_a_condition_wins_over_a_swept_parameter_on_the_same_path():
    """A condition is the fixed setup; the sweep explores inside it."""
    d = plan(
        BASE,
        [axis(name="input.Scenario.dt", kind="float", low=1, high=2)],
        n_samples=3,
        conditions=[{"input.Scenario.dt": 999}],
        seed=1,
    )
    assert all(r.params["input"]["Scenario"]["dt"] == 999 for r in d.runs)


def test_planning_does_not_mutate_the_base_params():
    base = {"input": {"Scenario": {"dt": 360}}}
    plan(base, conditions=[{"input.Scenario.dt": 1}], replicates=2)
    assert base["input"]["Scenario"]["dt"] == 360


def test_replicates_of_one_point_share_parameters_but_not_seeds():
    d = plan(BASE, replicates=4, seed=7)
    assert len({r.seed for r in d.runs}) == 4
    assert all(r.params == d.runs[0].params for r in d.runs)


def test_the_same_sweep_seed_reproduces_every_run_seed():
    a = plan(BASE, ONE_AXIS, n_samples=3, replicates=2, seed=11)
    b = plan(BASE, ONE_AXIS, n_samples=3, replicates=2, seed=11)
    assert [r.seed for r in a.runs] == [r.seed for r in b.runs]
    assert [r.params for r in a.runs] == [r.params for r in b.runs]


def test_a_different_sweep_seed_gives_different_run_seeds():
    a = plan(BASE, replicates=3, seed=1)
    b = plan(BASE, replicates=3, seed=2)
    assert [r.seed for r in a.runs] != [r.seed for r in b.runs]


def test_without_a_sweep_seed_no_run_is_seeded():
    """The engine then draws its own, and the sweep is not reproducible."""
    d = plan(BASE, replicates=3)
    assert all(r.seed is None for r in d.runs)


def test_neighbouring_runs_do_not_get_neighbouring_seeds():
    """Derived by hashing, so a weak RNG cannot correlate adjacent streams."""
    seeds = [derive_seed(1, 0, 0, r) for r in range(5)]
    assert all(abs(b - a) > 1000 for a, b in zip(seeds, seeds[1:]))


def test_a_derived_seed_stays_inside_the_range_the_engine_accepts():
    for r in range(50):
        assert 0 <= derive_seed(99, r, r, r) < 2147483647


def test_an_oversized_design_is_refused_with_its_arithmetic():
    with pytest.raises(ValueError) as excinfo:
        plan(BASE, ONE_AXIS, n_samples=100, conditions=[{}] * 10, replicates=10)
    assert str(MAX_RUNS) in str(excinfo.value)
    assert "10000 runs" in str(excinfo.value)


def test_zero_replicates_is_refused():
    with pytest.raises(ValueError):
        plan(BASE, replicates=0)


def test_the_summary_says_what_will_run_before_anything_does():
    d = plan(BASE, ONE_AXIS, n_samples=5, conditions=[{"a": 1}, {"a": 2}], replicates=2, seed=3)
    s = d.summary()
    assert s["totalRuns"] == 20 and s["totalPoints"] == 5 and s["totalConditions"] == 2
    assert s["varies"] == {"parameter": True, "condition": True, "replicate": True}
