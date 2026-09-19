"""The generator forwards pty output, so chunk boundaries fall anywhere."""

import pytest

from mcp_isicell.backend.streams import (
    iter_lines,
    locate_in_model,
    parse_meta_info,
    parse_progress,
)

BUILD_OUTPUT = (
    "-- The C compiler identification is GNU 12.2.0\n"
    "-- Configuring done\n"
    "-- Generating done\n"
    "-- Build files have been written to: /usr/src/app/ISiCell/simulations/sim_1/build\n"
    "[  5%] Building CXX object CMakeFiles/console.dir/src/main.cpp.o\n"
    "[ 50%] Building CXX object CMakeFiles/console.dir/src/simu.cpp.o\n"
    "[100%] Linking CXX executable console\n"
)


def chunk(text, size):
    return [text[i : i + size] for i in range(0, len(text), size)]


@pytest.mark.parametrize("size", [1, 2, 3, 7, 13, 64, 4096])
def test_lines_reassemble_whatever_the_chunk_size(size):
    assert list(iter_lines(chunk(BUILD_OUTPUT, size))) == BUILD_OUTPUT.rstrip("\n").split("\n")


@pytest.mark.parametrize("size", [1, 5, 17, 4096])
def test_the_final_percentage_is_found_whatever_the_chunk_size(size):
    progress = parse_progress(chunk(BUILD_OUTPUT, size))
    assert progress.percent == 100
    assert progress.phase == "building"


def test_a_line_split_mid_percentage_still_parses():
    """The worst case: the boundary falls inside the `[ 50%]` token itself."""
    progress = parse_progress(["[ 5", "0%] Building CXX object foo.o\n"])
    assert progress.percent == 50


def test_ansi_colour_codes_are_stripped():
    progress = parse_progress(["\x1b[32m[ 42%]\x1b[0m Building\n"])
    assert progress.percent == 42


def test_out_of_order_make_progress_keeps_the_furthest_point():
    """Under -j, make interleaves recipes and percentages can go backwards."""
    progress = parse_progress(["[ 90%] a\n", "[ 30%] b\n"])
    assert progress.percent == 90


def test_configuring_precedes_building():
    progress = parse_progress(["-- Configuring done\n"])
    assert progress.phase == "configuring"


def test_meta_info_is_recovered_from_stderr():
    stderr = 'some warning\nmetaInfo: {"PrimoCell.hpp": {"divide": {"line": 200, "column": 4}}}\n'
    assert parse_meta_info(stderr) == {
        "PrimoCell.hpp": {"divide": {"line": 200, "column": 4}}
    }


def test_absent_or_malformed_meta_info_yields_an_empty_map():
    assert parse_meta_info("") == {}
    assert parse_meta_info("metaInfo: {not json}\n") == {}


def test_a_generated_line_maps_back_to_the_model_element_that_owns_it():
    meta = {
        "PrimoCell.hpp": {
            "divide": {"line": 200, "column": 4, "idFunc": "f1"},
            "grow": {"line": 240, "column": 4, "idFunc": "f2"},
        }
    }
    found = locate_in_model(meta, "PrimoCell.hpp", 212)
    assert found is not None and found["element"] == "divide"
    assert found["idFunc"] == "f1"


def test_a_line_before_every_known_element_maps_to_nothing():
    meta = {"PrimoCell.hpp": {"divide": {"line": 200}}}
    assert locate_in_model(meta, "PrimoCell.hpp", 10) is None
