"""The subprocess boundary, exercised against a stub mainGenerator.py.

The stub replays realistic stdout/stderr, so the whole compile path runs here
without cmake, make or a C++ compiler.
"""

import json
import textwrap
from pathlib import Path

import pytest

from mcp_isicell.backend.local import LocalBackend, generate_token, validate_token
from mcp_isicell.config import IsicellPaths
from mcp_isicell.errors import ConfirmationRequired, NotConfigured

STUB = textwrap.dedent(
    '''
    import json, sys
    token, build_type = sys.argv[1], sys.argv[2]
    payload = json.loads(sys.stdin.read())
    print("-- Configuring done")
    print("-- Build files have been written to: /tmp/%s/build" % token)
    print("[ 50%] Building CXX object src/simu.cpp.o")
    print("[100%] Linking CXX executable console")
    print("states=%d" % len(payload["cell"]["States"]))
    print('metaInfo: {"PrimoCell.hpp": {"divide": {"line": 200, "idFunc": "f1"}}}',
          file=sys.stderr)
    sys.exit(0)
    '''
)

FAILING_STUB = textwrap.dedent(
    '''
    import sys
    sys.stdin.read()
    print("-- Configuring done")
    print("/usr/src/app/simulations/s/src/cells/PrimoCell.hpp:212:9: error: "
          "'oxygen' was not declared in this scope")
    print('metaInfo: {"PrimoCell.hpp": {"divide": {"line": 200, "idFunc": "f1"}}}',
          file=sys.stderr)
    sys.exit(2)
    '''
)


@pytest.fixture
def fake_root(tmp_path: Path) -> Path:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "simulations").mkdir()
    return tmp_path


def install_generator(root: Path, source: str) -> None:
    (root / "scripts" / "mainGenerator.py").write_text(source)


def backend(root: Path) -> LocalBackend:
    return LocalBackend(IsicellPaths(root))


def test_a_successful_compile_reports_the_final_percentage(fake_root, minimal_model):
    install_generator(fake_root, STUB)
    result = backend(fake_root).compile(minimal_model, "python")
    assert result.ok and result.percent == 100 and result.phase == "building"


def test_the_model_reaches_the_generator_on_stdin(fake_root, minimal_model):
    """mainGenerator.py reads builderData from stdin, never from argv."""
    install_generator(fake_root, STUB)
    result = backend(fake_root).compile(minimal_model, "python")
    assert "states=1" in "\n".join(result.stdout_tail)


def test_a_failing_compile_maps_the_error_onto_the_model_element(fake_root, minimal_model):
    """A g++ error in generated source is reported as the function the user wrote."""
    install_generator(fake_root, FAILING_STUB)
    result = backend(fake_root).compile(minimal_model, "python")
    assert not result.ok and result.returncode == 2
    errors = [d for d in result.diagnostics if d["severity"] == "error"]
    assert errors and errors[0]["model_element"] == "divide"
    assert errors[0]["idFunc"] == "f1"


def test_reusing_an_existing_token_requires_confirmation(fake_root, minimal_model):
    """mainGenerator.py rmtree's the directory before regenerating."""
    install_generator(fake_root, STUB)
    (fake_root / "simulations" / "sim_existing").mkdir()
    with pytest.raises(ConfirmationRequired):
        backend(fake_root).compile(minimal_model, "python", token="sim_existing")


def test_reusing_an_existing_token_proceeds_once_confirmed(fake_root, minimal_model):
    install_generator(fake_root, STUB)
    (fake_root / "simulations" / "sim_existing").mkdir()
    result = backend(fake_root).compile(
        minimal_model, "python", token="sim_existing", confirm=True
    )
    assert result.ok


def test_a_fresh_token_needs_no_confirmation(fake_root, minimal_model):
    install_generator(fake_root, STUB)
    assert backend(fake_root).compile(minimal_model, "python", token="sim_brand_new").ok


def test_an_unusable_build_type_is_refused_before_spawning(fake_root, minimal_model):
    install_generator(fake_root, STUB)
    with pytest.raises(ValueError):
        backend(fake_root).compile(minimal_model, "turbo")


def test_a_missing_checkout_is_reported_as_not_configured(tmp_path, minimal_model):
    with pytest.raises(NotConfigured):
        LocalBackend(IsicellPaths(tmp_path)).compile(minimal_model)


@pytest.mark.parametrize("bad", ["../etc", "a/b", "tok en", "", "nul\0byte"])
def test_a_token_outside_the_servers_own_charset_is_refused(bad):
    """Mirrors safePathCheck.js validateToken so a bad token never reaches disk."""
    with pytest.raises(ValueError):
        validate_token(bad)


def test_a_generated_token_is_always_acceptable():
    assert validate_token(generate_token())


def test_builds_are_listed_with_what_they_actually_contain(fake_root):
    install_generator(fake_root, STUB)
    done = fake_root / "simulations" / "sim_done" / "build"
    done.mkdir(parents=True)
    (done / "console").write_text("")
    listed = {b["token"]: b for b in backend(fake_root).list_builds()}
    assert listed["sim_done"]["compiled"] is True


def test_the_checksum_is_read_back_when_present(fake_root):
    d = fake_root / "simulations" / "sim_c"
    d.mkdir(parents=True)
    (d / "checksum.txt").write_text("d41d8cd98f00b204e9800998ecf8427e\n")
    assert backend(fake_root).checksum("sim_c") == "d41d8cd98f00b204e9800998ecf8427e"
    assert backend(fake_root).checksum("sim_absent") is None


def test_installed_modules_sees_only_those_with_a_config(fake_root):
    """A zip export carries empty module dirs; those are not usable modules."""
    mods = fake_root / "core" / "modules" / "cellModules"
    (mods / "Diffusion2D").mkdir(parents=True)
    (mods / "Diffusion2D" / "config.json").write_text("{}")
    (mods / "GrimesOxygen").mkdir(parents=True)  # empty submodule
    assert IsicellPaths(fake_root).installed_modules() == {"Diffusion2D"}


def test_removing_a_build_requires_confirmation(fake_root):
    (fake_root / "simulations" / "sim_x").mkdir(parents=True)
    with pytest.raises(ConfirmationRequired):
        backend(fake_root).remove_build("sim_x")
    assert backend(fake_root).remove_build("sim_x", confirm=True)["removed"] is True
    assert not (fake_root / "simulations" / "sim_x").exists()


def test_the_payload_on_stdin_is_valid_builder_data(fake_root, minimal_model):
    """Guards the alias: `from` must survive, `from_` must not appear."""
    recorder = fake_root / "captured.json"
    install_generator(
        fake_root,
        f"import sys, pathlib\n"
        f"pathlib.Path({str(recorder)!r}).write_text(sys.stdin.read())\n"
        f"sys.exit(0)\n",
    )
    backend(fake_root).compile(minimal_model, "python")
    payload = json.loads(recorder.read_text())
    assert payload["cell"]["Transitions"][0]["from"] == "Proliferating"
