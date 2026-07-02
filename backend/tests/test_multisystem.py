"""Multi-system foundation (#10 / ADR 0011): N tagged systems in ONE model — merge, datastore
round-trip (+legacy default), per-system validation scoping, storm-only MIKE+ export, and the
Regina sanitary tracer assembled from the recorded fixture."""
import json
from datetime import date, datetime
from pathlib import Path

from swmmcanada.build import (
    BuildConfig,
    ConduitIn,
    JunctionIn,
    NetworkIn,
    OutfallIn,
    RainfallSeries,
    SubcatchmentIn,
    build_model,
)
from swmmcanada.geo import aoi_from_geojson
from swmmcanada.sources.cities import base
from swmmcanada.sources.cities.regina import build_regina_network
from swmmcanada.validate import MethodDescriptor, validate_model

FIX = Path(__file__).resolve().parent / "fixtures" / "regina"


def _storm():
    return NetworkIn(
        junctions=[JunctionIn("J1", 99.0, -104.62, 50.44), JunctionIn("J2", 98.5, -104.615, 50.44)],
        outfalls=[OutfallIn("O1", 98.0, -104.61, 50.44)],
        conduits=[ConduitIn("C1", "J1", "J2", 100.0), ConduitIn("C2", "J2", "O1", 100.0)],
    )


def _sanitary():
    return NetworkIn(
        junctions=[JunctionIn("J1", 97.0, -104.62, 50.441)],   # name COLLIDES with storm J1
        outfalls=[OutfallIn("O1", 96.0, -104.61, 50.441)],
        conduits=[ConduitIn("C1", "J1", "O1", 120.0)],
    )


def _merged():
    return base.merge_secondary_system(_storm(), _sanitary(), prefix="SAN_", system="sanitary")


def test_default_system_is_storm_minor():
    assert JunctionIn("J", 1.0, 0.0, 0.0).system == "storm_minor"
    assert SubcatchmentIn("S", "J", 1.0, 50.0, 100.0, 1.0).system == "storm_minor"


def test_merge_prefixes_tags_and_keeps_primary_untouched():
    m = _merged()
    names = {j.name for j in m.junctions}
    assert names == {"J1", "J2", "SAN_J1"}                      # collision resolved by prefix
    san = [c for c in m.conduits if c.system == "sanitary"]
    assert len(san) == 1 and san[0].from_node == "SAN_J1" and san[0].to_node == "SAN_O1"
    assert all(j.system == "storm_minor" for j in m.junctions if not j.name.startswith("SAN_"))


def test_merged_model_builds_and_datastore_roundtrips_system(tmp_path):
    from swmmcanada.datastore import build_from_datastore, read_datastore, write_datastore

    subs = [SubcatchmentIn("S1", "J1", 1.0, 40.0, 100.0, 1.0)]
    rain = RainfallSeries([datetime(2020, 6, 1, h) for h in range(3)], [1.0, 2.0, 0.0])
    cfg = BuildConfig(out_dir=tmp_path / "x", start=date(2020, 6, 1), end=date(2020, 6, 2))

    write_datastore(tmp_path / "ds", network=_merged(), subcatchments=subs, rain=rain, config=cfg)
    ds = read_datastore(tmp_path / "ds")
    assert {j.name: j.system for j in ds.network.junctions}["SAN_J1"] == "sanitary"

    res = build_from_datastore(tmp_path / "ds", tmp_path / "b")     # one .inp, both systems
    manifest = json.loads(res.manifest_path.read_text())
    assert manifest["systems"]["sanitary"]["conduits"] == 1
    assert "SAN_J1" in res.inp_path.read_text()                     # disconnected subgraph shipped


def test_validation_scopes_node_checks_to_storm():
    aoi = aoi_from_geojson({"type": "Polygon", "coordinates": [[
        [-104.625, 50.435], [-104.605, 50.435], [-104.605, 50.445], [-104.625, 50.445],
        [-104.625, 50.435]]]})
    subs = [SubcatchmentIn("S1", "J1", area_ha=aoi.area_km2 * 100, pct_imperv=50.0,
                           width_m=100.0, pct_slope=1.0,
                           polygon=[(-104.625, 50.435), (-104.605, 50.435),
                                    (-104.605, 50.445), (-104.625, 50.445)])]
    r = validate_model(_merged(), subs, aoi,
                       method=MethodDescriptor("junction_voronoi", "x", "low"))
    node_cov = {c.id: c for c in r.checks}["node_coverage"]
    assert not any("SAN_" in n for n in node_cov.metrics.get("sample", []))
    d = r.to_dict()
    assert d["systems"]["sanitary"]["nodes"] == 2                   # counts surfaced
    assert d["systems"]["storm_minor"]["conduits"] == 2


def test_mikeplus_export_is_storm_only(tmp_path):
    from swmmcanada.datastore import ModelReadyDatastore
    from swmmcanada.export.mikeplus import MikePlusExporter
    import geopandas as gpd

    ds = ModelReadyDatastore(
        network=_merged(),
        subcatchments=[SubcatchmentIn("S1", "J1", 1.0, 40.0, 100.0, 1.0,
                                      polygon=[(-104.62, 50.439), (-104.615, 50.439),
                                               (-104.615, 50.441), (-104.62, 50.441)])],
        rain=RainfallSeries([datetime(2020, 6, 1, h) for h in range(3)], [1.0, 2.0, 0.0]),
        config={"start": "2020-06-01", "end": "2020-06-02", "coordinate_crs": "EPSG:32613"},
        provenance={},
    )
    MikePlusExporter().export(ds, tmp_path)
    nodes = gpd.read_file(tmp_path / "nodes.shp")
    assert not nodes["MUID"].str.startswith("SAN_").any()           # sanitary omitted (v1)
    links = gpd.read_file(tmp_path / "links.shp")
    assert len(links) == 2                                          # storm conduits only


# --- the Regina tracer, offline from the recorded fixture --------------------------


def test_regina_sanitary_skeleton_assembles_from_fixture():
    feats = json.loads((FIX / "domestic_pipes.geojson").read_text())["features"]
    res = build_regina_network({"pipes": feats})
    net = res.network
    assert len(net.junctions) > 100 and len(net.conduits) > 150     # 204 fixture lines
    assert len(net.outfalls) >= 1                                   # per-component sinks exist
    node_names = {j.name for j in net.junctions} | {o.name for o in net.outfalls}
    assert all(c.from_node in node_names and c.to_node in node_names for c in net.conduits)


def test_regina_storm_plus_sanitary_one_inp(tmp_path):
    storm = build_regina_network({
        "pipes": json.loads((FIX / "storm_pipes.geojson").read_text())["features"],
        "outfalls": json.loads((FIX / "outfalls.geojson").read_text())["features"],
    }).network
    san = build_regina_network({
        "pipes": json.loads((FIX / "domestic_pipes.geojson").read_text())["features"],
    }).network
    merged = base.merge_secondary_system(storm, san, prefix="SAN_", system="sanitary")

    rain = RainfallSeries([datetime(2020, 6, 1, h) for h in range(3)], [1.0, 2.0, 0.0])
    sub = SubcatchmentIn("S1", storm.junctions[0].name, 1.0, 50.0, 100.0, 1.0)
    res = build_model(network=merged, subcatchments=[sub], rain=rain,
                      config=BuildConfig(out_dir=tmp_path, start=date(2020, 6, 1), end=date(2020, 6, 2)))
    manifest = json.loads(res.manifest_path.read_text())
    assert manifest["systems"]["sanitary"]["conduits"] > 150        # both systems, one model
    assert manifest["systems"]["storm_minor"]["conduits"] > 150
