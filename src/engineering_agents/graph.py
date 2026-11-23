"""Load the plant knowledge graph into memory.

An rdflib ``Graph`` is simply a set of triples (subject, predicate, object).
Parsing two Turtle files into the same graph merges them: the ontology (TBox,
the vocabulary) and the plant data (ABox, the facts) become one queryable set.
"""

from pathlib import Path

from rdflib import Graph, Namespace

ROOT = Path(__file__).resolve().parents[2]
ONTOLOGY_FILE = ROOT / "ontology" / "plant.ttl"
DATA_FILE = ROOT / "data" / "plant.ttl"

# Namespaces turn short names such as PLANT.Device into full IRIs
# (https://example.org/plant#Device).
PLANT = Namespace("https://example.org/plant#")  # vocabulary (ontology)
EX = Namespace("https://example.org/demo-plant#")  # instances of the demo plant


def load_graph() -> Graph:
    """Return one graph containing the ontology and the demo plant data."""
    graph = Graph()
    graph.parse(ONTOLOGY_FILE, format="turtle")
    graph.parse(DATA_FILE, format="turtle")
    # Prefixes only affect how IRIs are printed, e.g. "ex:LT-101".
    graph.bind("plant", PLANT)
    graph.bind("ex", EX)
    return graph
