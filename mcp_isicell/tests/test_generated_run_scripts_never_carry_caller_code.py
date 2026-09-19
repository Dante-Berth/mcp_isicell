"""The run tool describes a measurement; it does not accept Python."""

import ast
import json

import pytest

from mcp_isicell.backend.simscript import CATCHERS, RunSpec, catcher_expression, render


def test_a_generated_script_is_valid_python():
    script = render(RunSpec(params={"input": {}}, steps=[0, 10], catcher="countState"))
    ast.parse(script)


def test_every_named_catcher_generates_a_parseable_script():
    for name in CATCHERS:
        ast.parse(render(RunSpec(params={}, steps=[0], catcher=name)))


def test_parameters_cross_into_the_script_as_json_not_as_source():
    """A param value that looks like code is a string literal, not a statement."""
    hostile = {"input": {"Cell": {"Common": {"note": "'); import os; os.system('x'); ('"}}}}
    script = render(RunSpec(params=hostile, steps=[0]))
    tree = ast.parse(script)  # a SyntaxError here would mean it was spliced as code

    # The hostile text exists only inside one string constant, never as an AST
    # node that does anything.
    assert not any(isinstance(n, ast.Import) and n.names[0].name == "os"
                   for n in ast.walk(tree))
    assert not any(isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "system"
                   for n in ast.walk(tree))

    # And it survives a round trip, so it is data all the way through.
    embedded = ast.literal_eval(script.split("PARAMS = json.loads(")[1].split(")\n")[0])
    assert json.loads(embedded) == hostile


def test_an_unknown_catcher_is_refused_and_lists_the_real_ones():
    with pytest.raises(ValueError) as excinfo:
        catcher_expression("simu.cells.get('x'); __import__('os').system('rm -rf /')")
    assert "countState" in str(excinfo.value)


def test_an_attribute_catcher_accepts_only_an_identifier():
    assert "oxygen" in catcher_expression("attribute:oxygen")
    with pytest.raises(ValueError):
        catcher_expression("attribute:x') or __import__('os').system('x')#")


def test_steps_are_sorted_and_deduplicated():
    script = render(RunSpec(params={}, steps=[30, 10, 10, 0]))
    embedded = ast.literal_eval(script.split("STEPS = json.loads(")[1].split(")\n")[0])
    assert json.loads(embedded) == [0, 10, 30]


def test_a_run_with_no_steps_is_refused():
    with pytest.raises(ValueError):
        render(RunSpec(params={}, steps=[]))


def test_negative_steps_are_refused():
    with pytest.raises(ValueError):
        render(RunSpec(params={}, steps=[-1, 10]))


def test_the_script_advances_only_forwards():
    """Steps are cumulative: the script updates by the difference, never backwards."""
    script = render(RunSpec(params={}, steps=[0, 10]))
    assert "if target > current:" in script
    assert "simu.update(target - current)" in script


def test_a_failure_is_reported_as_json_rather_than_only_a_traceback():
    script = render(RunSpec(params={}, steps=[0]))
    assert '"ok": False' in script or "'ok': False" in script or '{{"ok": False' in script or 'json.dump({"ok": False' in script
