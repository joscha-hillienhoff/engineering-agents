"""The SHACL shapes accept the plant as built and catch the expected violations."""

import pytest
from rdflib import Graph

from engineering_agents.graph import EX, PLANT
from engineering_agents.validation import (
    Violation,
    apply_replacements,
    check_replacements,
    validate_plant,
)

TYPE_A_DEVICES = ["LT-101", "LT-102", "LT-103", "LT-301"]


def replace_type_a_with(new_type: str) -> dict[str, str]:
    return {device: new_type for device in TYPE_A_DEVICES}


def test_current_plant_conforms(graph):
    result = validate_plant(graph)
    assert result.conforms
    assert result.violations == []


def test_type_b_everywhere_violates_only_at_the_zone_1_location(graph):
    result = check_replacements(graph, replace_type_a_with("LevelTransmitterB"))
    assert not result.conforms
    assert result.violations == [
        Violation(
            device="ex:LT-101",
            location="ex:T-101",
            zone="plant:Zone1",
            type="ex:LevelTransmitterB",
            message="LT-101 at T-101 Solvent storage tank (Zone 1): "
            "type Level transmitter type B is not approved for Zone 1.",
        )
    ]


def test_type_c_everywhere_conforms(graph):
    result = check_replacements(graph, replace_type_a_with("LevelTransmitterC"))
    assert result.conforms
    assert result.violations == []


def test_mixed_plan_with_c_in_zone_1_conforms(graph):
    plan = replace_type_a_with("LevelTransmitterB") | {"LT-101": "LevelTransmitterC"}
    assert check_replacements(graph, plan).conforms


def test_replacement_does_not_change_the_original_graph(graph):
    changed = apply_replacements(graph, {"ex:LT-101": "ex:LevelTransmitterB"})
    assert changed.value(EX["LT-101"], PLANT.hasType) == EX.LevelTransmitterB
    assert graph.value(EX["LT-101"], PLANT.hasType) == EX.LevelTransmitterA


@pytest.mark.parametrize("plan", [{"LT-999": "LevelTransmitterB"}, {"LT-101": "LevelTransmitterZ"}])
def test_unknown_device_or_type_is_rejected(graph, plan):
    with pytest.raises(ValueError, match="unknown"):
        apply_replacements(graph, plan)


def test_structural_shape_catches_device_without_type(graph):
    broken = graph + Graph()
    broken.remove((EX["PT-101"], PLANT.hasType, None))
    result = validate_plant(broken)
    assert not result.conforms
    assert result.violations == [
        Violation(
            device="ex:PT-101",
            location="ex:T-101",
            zone="plant:Zone1",
            type=None,
            message="A device must have exactly one device type.",
        )
    ]


def test_structural_shape_catches_location_without_zone(graph):
    broken = graph + Graph()
    broken.remove((EX["K-302"], PLANT.inZone, None))
    result = validate_plant(broken)
    assert [(v.device, v.location, v.zone, v.message) for v in result.violations] == [
        (None, "ex:K-302", None, "An installation location must lie in exactly one Ex zone.")
    ]
