import pytest
from rdflib import Graph

from engineering_agents.graph import load_graph


@pytest.fixture(scope="session")
def graph() -> Graph:
    return load_graph()
