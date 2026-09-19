"""Module dependencies come in two kinds: module Types and 2D/3D pseudo-caps."""

from mcp_isicell.domain.validation import validate


def test_grimes_oxygen_without_spheroid_manager_reports_its_unmet_dependency(
    minimal_model, catalogue
):
    """Grimes Oxygen declares Dependencies: ["Spheroid Manager"], a module Type."""
    minimal_model.moduleSelected = ["Grimes Oxygen"]
    report = validate(minimal_model, catalogue)
    assert "E_MODULE_DEPS" in report.codes()


def test_grimes_oxygen_with_spheroid_manager_satisfies_the_dependency(minimal_model, catalogue):
    minimal_model.moduleSelected = ["Grimes Oxygen", "Spheroid Manager"]
    report = validate(minimal_model, catalogue)
    assert "E_MODULE_DEPS" not in report.codes()


def test_mixing_two_and_three_dimensional_modules_is_an_error(minimal_model, catalogue):
    minimal_model.moduleSelected = ["Diffusion2D", "Mass Spring Damper"]
    report = validate(minimal_model, catalogue)
    assert "E_SPACE_DIM_CONFLICT" in report.codes()


def test_a_module_name_that_is_not_in_the_catalogue_is_an_error(minimal_model, catalogue):
    """mainGenerator.py matches the Name field, so a DirName is not accepted."""
    minimal_model.moduleSelected = ["MassSpringDamper"]  # DirName, not Name
    report = validate(minimal_model, catalogue)
    assert "E_UNKNOWN_MODULE" in report.codes()


def test_a_selected_module_absent_from_disk_is_caught_before_the_compile(
    minimal_model, catalogue
):
    """The empty-submodule case: config.json is missing, mainGenerator.py:86 throws."""
    minimal_model.moduleSelected = ["Diffusion2D"]
    report = validate(minimal_model, catalogue, installed_modules=set())
    assert "E_MODULE_MISSING_ON_DISK" in report.codes()


def test_a_selected_module_present_on_disk_is_not_flagged(minimal_model, catalogue):
    minimal_model.moduleSelected = ["Diffusion2D"]
    report = validate(minimal_model, catalogue, installed_modules={"Diffusion2D"})
    assert "E_MODULE_MISSING_ON_DISK" not in report.codes()
