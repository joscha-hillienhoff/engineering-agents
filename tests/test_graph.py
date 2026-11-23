"""The ontology and the demo plant load and are internally consistent."""

from rdflib import OWL, RDF, RDFS, Graph

from engineering_agents.graph import EX, PLANT


def instances(graph: Graph, cls) -> set:
    return set(graph.subjects(RDF.type, cls))


def test_counts(graph):
    assert len(instances(graph, PLANT.Device)) == 18
    assert len(instances(graph, PLANT.DeviceType)) == 6
    assert len(instances(graph, PLANT.Plant)) == 1
    assert len(instances(graph, PLANT.Unit)) == 3
    assert len(instances(graph, PLANT.InstallationLocation)) == 8
    assert len(instances(graph, PLANT.ExZone)) == 4


def test_every_device_has_exactly_one_type_and_location(graph):
    for device in instances(graph, PLANT.Device):
        types = list(graph.objects(device, PLANT.hasType))
        locations = list(graph.objects(device, PLANT.installedAt))
        assert len(types) == 1, device
        assert len(locations) == 1, device
        assert (types[0], RDF.type, PLANT.DeviceType) in graph
        assert (locations[0], RDF.type, PLANT.InstallationLocation) in graph


def test_location_hierarchy_is_complete(graph):
    for location in instances(graph, PLANT.InstallationLocation):
        assert graph.value(location, PLANT.inZone) in instances(graph, PLANT.ExZone)
        assert graph.value(location, PLANT.partOf) in instances(graph, PLANT.Unit)
    for unit in instances(graph, PLANT.Unit):
        assert graph.value(unit, PLANT.partOf) == EX.DemoPlant


def test_every_device_type_has_status_function_and_approval(graph):
    for device_type in instances(graph, PLANT.DeviceType):
        assert str(graph.value(device_type, PLANT.status)) in {"active", "discontinued"}
        assert graph.value(device_type, PLANT.measuredVariable) is not None
        approval = graph.value(device_type, PLANT.hasApproval)
        assert set(graph.objects(approval, PLANT.approvedFor)), device_type


def test_existing_installations_respect_approvals(graph):
    # The plant as built is valid; only a proposed replacement can break this.
    for device in instances(graph, PLANT.Device):
        zone = graph.value(graph.value(device, PLANT.installedAt), PLANT.inZone)
        approval = graph.value(graph.value(device, PLANT.hasType), PLANT.hasApproval)
        assert (approval, PLANT.approvedFor, zone) in graph, device


def test_ontology_terms_document_themselves(graph):
    terms = (
        instances(graph, OWL.Class)
        | instances(graph, OWL.ObjectProperty)
        | instances(graph, OWL.DatatypeProperty)
    )
    assert len(terms) == 8 + 6 + 5  # classes, object properties, datatype properties
    for term in terms:
        assert graph.value(term, RDFS.label) is not None, term
        assert graph.value(term, RDFS.comment) is not None, term
