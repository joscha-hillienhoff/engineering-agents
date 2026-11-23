"""Validate the plant graph, or a proposed change to it, against SHACL shapes.

The shapes in ``shapes/plant-shapes.ttl`` hold the rules. pySHACL checks a
data graph against them and returns a validation report, itself an RDF graph.
This module turns that report into a small result object that other code (and
later the planning agent) can read without knowing SHACL.

A proposed change is never written into the real graph: it is applied to a
copy, and the copy is validated. The original plant stays untouched.
"""

from dataclasses import dataclass

from pyshacl import validate
from rdflib import RDF, Graph, URIRef
from rdflib.namespace import SH

from engineering_agents.graph import EX, PLANT, ROOT

SHAPES_FILE = ROOT / "shapes" / "plant-shapes.ttl"


@dataclass(frozen=True)
class Violation:
    """One broken rule. IRIs are written with their prefix, e.g. ``ex:LT-101``.

    ``device`` is None when the violation is about a location itself; the other
    fields are None when the data does not contain them (for example a device
    without a type).
    """

    device: str | None
    location: str | None
    zone: str | None
    type: str | None
    message: str


@dataclass(frozen=True)
class ValidationResult:
    conforms: bool
    violations: list[Violation]


def load_shapes() -> Graph:
    return Graph().parse(SHAPES_FILE, format="turtle")


def validate_plant(graph: Graph) -> ValidationResult:
    """Validate the graph against the plant shapes."""
    conforms, report, _text = validate(graph, shacl_graph=load_shapes())
    # The report is an RDF graph with one sh:ValidationResult node per violation.
    violations = []
    for result in report.subjects(RDF.type, SH.ValidationResult):
        focus = report.value(result, SH.focusNode)  # the resource that broke a rule
        message = str(report.value(result, SH.resultMessage))
        violations.append(_violation(graph, focus, message))
    violations.sort(key=lambda v: (v.device or "", v.location or "", v.message))
    return ValidationResult(conforms=bool(conforms), violations=violations)


def apply_replacements(graph: Graph, replacements: dict[str, str]) -> Graph:
    """Return a copy of the graph in which each device has its new type.

    ``replacements`` maps a device to its new device type, both as local names
    of the demo plant, for example ``{"LT-101": "LevelTransmitterB"}`` (an
    ``ex:`` prefix is accepted too).
    """
    changed = graph + Graph()  # a new graph with the same triples and prefixes
    for device_name, type_name in replacements.items():
        device, new_type = _resource(device_name), _resource(type_name)
        if (device, RDF.type, PLANT.Device) not in graph:
            raise ValueError(f"unknown device: {device_name}")
        if (new_type, RDF.type, PLANT.DeviceType) not in graph:
            raise ValueError(f"unknown device type: {type_name}")
        # set() replaces all existing values of hasType with the new one.
        changed.set((device, PLANT.hasType, new_type))
    return changed


def check_replacements(graph: Graph, replacements: dict[str, str]) -> ValidationResult:
    """Validate the plant as it would be after the proposed replacements."""
    return validate_plant(apply_replacements(graph, replacements))


def _resource(name: str) -> URIRef:
    return EX[name.removeprefix("ex:")]


def _violation(graph: Graph, focus: URIRef, message: str) -> Violation:
    """Describe a validation result by the device, location, zone and type involved."""
    if (focus, RDF.type, PLANT.InstallationLocation) in graph:
        device, location = None, focus
    else:
        device, location = focus, graph.value(focus, PLANT.installedAt)
    zone = graph.value(location, PLANT.inZone) if location is not None else None
    device_type = graph.value(device, PLANT.hasType) if device is not None else None

    def text(term):
        return None if term is None else term.n3(graph.namespace_manager)

    return Violation(
        device=text(device),
        location=text(location),
        zone=text(zone),
        type=text(device_type),
        message=message,
    )
