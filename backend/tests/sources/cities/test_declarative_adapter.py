"""A city described instead of coded, proved against the city that was coded.

Adding a city means writing ~130 lines whose only real content is five facts: which fields
carry the name, the inverts, the diameter and the material, and what a missing value looks
like. The rest — paging, geometry ends, duplicate names, unit conversion, assembly — is the
same in all 35 and already lives in `base`.

These tests pin the only thing that matters about moving those five facts out of code: the
model must not change. Each city is built twice from its own recorded fixture, once through
its hand-written adapter and once from a description, and the two are compared element by
element. A description that produces a different network is not a simplification, it is a
silent regression in a city someone already validated.
"""
import json
from pathlib import Path

import pytest

from swmmcanada.sources.cities import base
from swmmcanada.sources.cities.declarative import PipeFields, build_pipes

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def _mains(city, name="mains"):
    d = json.loads((FIXTURES / city / f"{name}.geojson").read_text())
    return d["features"] if isinstance(d, dict) else d


#: The five facts, per city. Everything else is the shared spine.
DESCRIBED = {
    "northvandistrict": PipeFields(
        name=("ASSET_ID",), name_fallback_feature_id=True,
        invert_up="UP_ELEV", invert_down="DN_ELEV", invert_missing="below:-90",
        diameter="AM_SIZE", material="AM_MATERIA",
        material_aliases={"NON REINF CONC": "CONC", "REINF CONC": "CONC"},
    ),
    "whitby": PipeFields(
        name=("FACILITYID", "OBJECTID"), dedupe="objectid",
        invert_up="UP_INV", invert_down="DOWN_INV", invert_missing="zero",
        diameter="DIAM", material="PIPE_MATER",
        node_from="FR_NODE", node_to="TO_NODE",
    ),
    "burnaby": PipeFields(
        name=("COMPKEY", "OBJECTID"), dedupe="none",
        invert_up="UPSELEV", invert_down="DWNELEV", invert_missing="zero",
        diameter="PIPEDIAM", material="PIPETYPE",
        length="PIPELEN", shape="PIPESHP", height="PIPEHT",
        node_from="UNITID", node_to="UNITID2", width_equals_diameter=True,
    ),
}


def _handwritten(city, features):
    mod = __import__(f"swmmcanada.sources.cities.{city}", fromlist=["x"])
    return getattr(mod, f"build_{city}_network")({"mains": features}).network


def _described(city, features):
    cfg = base.AssembleConfig(snap_decimals=5)
    pipes, diag = build_pipes(features, DESCRIBED[city], cfg)
    labels = diag.get("label_points")
    if labels:
        labels, *_ = base.safe_labels(labels, cfg.snap_decimals)
        return base.assemble_network(pipes, label_points=labels, config=cfg).network
    return base.assemble_network(pipes, config=cfg).network


@pytest.mark.parametrize("city", sorted(DESCRIBED))
class TestADescribedCityIsTheSameCity:
    def test_the_same_conduits_come_out(self, city):
        f = _mains(city)
        a, b = _handwritten(city, f), _described(city, f)
        assert [(c.name, c.from_node, c.to_node) for c in a.conduits] == \
               [(c.name, c.from_node, c.to_node) for c in b.conduits]

    def test_the_same_diameters_and_roughness(self, city):
        f = _mains(city)
        a, b = _handwritten(city, f), _described(city, f)
        assert [(c.diameter_m, c.roughness_n) for c in a.conduits] == \
               [(c.diameter_m, c.roughness_n) for c in b.conduits]

    def test_the_same_nodes_at_the_same_elevations(self, city):
        f = _mains(city)
        a, b = _handwritten(city, f), _described(city, f)
        assert [(j.name, round(j.invert_m, 6)) for j in a.junctions] == \
               [(j.name, round(j.invert_m, 6)) for j in b.junctions]
