"""Each named query returns the expected rows on the demo plant."""

import pytest

from engineering_agents.queries import QUERIES, run_query


def column(rows, name):
    return [row[name] for row in rows]


def test_devices_lists_every_device_with_location_and_zone(graph):
    rows = run_query(graph, "devices")
    assert len(rows) == 18
    lt101 = next(row for row in rows if row["device"] == "ex:LT-101")
    assert lt101 == {
        "device": "ex:LT-101",
        "type": "ex:LevelTransmitterA",
        "location": "T-101 Solvent storage tank",
        "unit": "U100 Tank farm",
        "zone": "Zone 1",
        "medium": "solvent",
    }


def test_devices_keeps_devices_without_medium(graph):
    # OPTIONAL: devices without a recorded medium still appear, with an empty value.
    rows = run_query(graph, "devices")
    assert {row["device"] for row in rows if row["medium"] == ""} == {"ex:PT-302", "ex:TT-202"}


def test_installation_points_of_discontinued_type_a(graph):
    rows = run_query(graph, "installation-points", "LevelTransmitterA")
    assert [(row["device"], row["zone"]) for row in rows] == [
        ("ex:LT-101", "Zone 1"),
        ("ex:LT-102", "Non-hazardous"),
        ("ex:LT-103", "Zone 2"),
        ("ex:LT-301", "Non-hazardous"),
    ]


def test_exactly_one_type_a_installation_point_is_in_zone_1(graph):
    rows = run_query(graph, "installation-points", "LevelTransmitterA")
    zone1 = [row for row in rows if row["zone"] == "Zone 1"]
    assert column(zone1, "device") == ["ex:LT-101"]
    assert zone1[0]["location"] == "T-101 Solvent storage tank"


def test_type_b_is_not_approved_for_zone_1(graph):
    rows = run_query(graph, "type-approvals", "LevelTransmitterB")
    assert column(rows, "zone") == ["Non-hazardous", "Zone 2"]
    assert set(column(rows, "status")) == {"active"}


def test_type_a_is_discontinued(graph):
    rows = run_query(graph, "type-approvals", "ex:LevelTransmitterA")
    assert column(rows, "zone") == ["Non-hazardous", "Zone 1", "Zone 2"]
    assert set(column(rows, "status")) == {"discontinued"}


def test_only_type_c_can_replace_the_zone_1_device(graph):
    rows = run_query(graph, "replacement-candidates", "LT-101")
    assert column(rows, "candidate") == ["ex:LevelTransmitterC"]
    assert rows[0]["zone"] == "Zone 1"


def test_types_b_and_c_can_replace_a_non_hazardous_device(graph):
    rows = run_query(graph, "replacement-candidates", "LT-102")
    assert column(rows, "candidate") == ["ex:LevelTransmitterB", "ex:LevelTransmitterC"]


def test_discontinued_type_is_never_a_candidate(graph):
    # LT-201 already uses type C; type A would fit zone 1 but is discontinued.
    assert run_query(graph, "replacement-candidates", "LT-201") == []


def test_parameterised_query_requires_argument(graph):
    with pytest.raises(ValueError):
        run_query(graph, "installation-points")


def test_every_registered_query_has_a_sparql_file(graph):
    arguments = {"deviceType": "LevelTransmitterA", "device": "LT-101", None: None}
    for query in QUERIES.values():
        assert run_query(graph, query.name, arguments[query.parameter])
