"""Regression baselines for subcatchment generation (issue #2).

Runs the *real, unchanged* delineation on the recorded downtown fixtures (Victoria,
Ottawa) and locks today's validation verdict — counts, zero errors, and exactly which
warning checks fail — so a future fidelity upgrade (DEM delineation, outlet rerouting)
shows up as a visible, reviewable diff instead of a silent degradation.

Baselines are fixture-scale: the checked-in downtown extracts (74 / 400 cells), the
CI-runnable subset of the full downtown runs recorded in RESULTS.md (732 / 2,461).
"""
import json
from pathlib import Path

import pytest

from swmmcanada.geo import aoi_from_geojson
from swmmcanada.network.synth import NetworkConfig, _build_subcatchments
from swmmcanada.sources.cities import base
from swmmcanada.sources.cities.ottawa import build_ottawa_network
from swmmcanada.sources.cities.victoria import build_victoria_network
from swmmcanada.validate import MethodDescriptor, validate_model

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

JUNCTION_VORONOI = MethodDescriptor("junction_voronoi", "nearest node service area", "low")
CATCHBASIN_VORONOI = MethodDescriptor("catchbasin_voronoi", "nearest inlet service area", "low")

# --- the locked baseline (today's behaviour; a legit change is a one-line diff) ---
VIC_N_JUNCTIONS = 74            # downtown-Victoria fixture -> one Voronoi cell per junction
OTT_N_CATCHBASINS = 400         # downtown-Ottawa fixture  -> one cell per catch basin
OTT_N_JUNCTIONS = 442           # Ottawa fallback          -> one cell per junction
VIC_WARNINGS = {"shape_plausibility"}                       # 3 extreme cells today
# outlet_distance/shape = the documented outlet-mapping risk; invert_consistency joined
# 2026-07-16 (F-007): the check now compares TRUE pipe-end elevations (node invert +
# conduit offset, #130), which exposes adverse as-built pipe ends in Ottawa's published
# inverts that min-of-ends node inverts used to smooth away. A more honest warning, not
# a regression.
OTT_WARNINGS = {"outlet_distance", "shape_plausibility", "invert_consistency"}


def _load(city: str, name: str) -> list:
    return json.loads((FIXTURES / city / f"{name}.geojson").read_text())["features"]


def _aoi_around(*features_lists, pad=0.001):
    """A padded bbox AOI around the fixture geometries (Point / LineString / MultiLineString)."""
    xs, ys = [], []
    for feats in features_lists:
        for f in feats:
            g = f.get("geometry") or {}
            cs, t = g.get("coordinates"), g.get("type")
            if t == "Point":
                xs.append(cs[0]); ys.append(cs[1])
            elif t == "LineString":
                xs += [x for x, _ in cs]; ys += [y for _, y in cs]
            elif t == "MultiLineString":
                for part in cs:
                    xs += [x for x, _ in part]; ys += [y for _, y in part]
    lo, hi, la, ha = min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad
    return aoi_from_geojson({"type": "Polygon", "coordinates": [[
        [lo, la], [hi, la], [hi, ha], [lo, ha], [lo, la]]]})


def _failed_warning_ids(report):
    return {c.id for c in report.warnings}


# --- Victoria: real network -> junction-Voronoi fallback ------------------------


@pytest.fixture(scope="module")
def victoria():
    inputs = {k: _load("victoria", k) for k in ("mains", "manholes", "fittings", "outfalls")}
    net = build_victoria_network(**inputs).network
    aoi = _aoi_around(inputs["mains"], inputs["manholes"])
    return net, aoi


def test_victoria_baseline_junction_voronoi(victoria):
    net, aoi = victoria
    assert len(net.junctions) == VIC_N_JUNCTIONS
    jxy = {j.name: (j.x, j.y) for j in net.junctions}
    subs = _build_subcatchments(jxy, aoi, NetworkConfig())

    assert len(subs) == VIC_N_JUNCTIONS                     # one cell per junction
    r = validate_model(net, subs, aoi, method=JUNCTION_VORONOI)
    assert r.ok and r.errors == []                          # structurally trustworthy today
    assert _failed_warning_ids(r) == VIC_WARNINGS           # and exactly these warnings
    ids = {c.id: c for c in r.checks}
    assert ids["aoi_coverage"].passed                       # no blank holes at fixture scale
    assert ids["overlap"].passed                            # no double-counted runoff


def test_victoria_delineation_is_deterministic(victoria):
    net, aoi = victoria
    jxy = {j.name: (j.x, j.y) for j in net.junctions}
    a = _build_subcatchments(jxy, aoi, NetworkConfig())
    b = _build_subcatchments(jxy, aoi, NetworkConfig())
    assert [(s.name, s.outlet_node, s.area_ha, s.polygon) for s in a] == \
           [(s.name, s.outlet_node, s.area_ha, s.polygon) for s in b]


# --- Ottawa: real network + real catch basins (no parcels/buildings) ------------


@pytest.fixture(scope="module")
def ottawa():
    pipes, cbs = _load("ottawa", "storm_pipes"), _load("ottawa", "catchbasins")
    net = build_ottawa_network({"pipes": pipes, "outfalls": _load("ottawa", "outfalls")}).network
    return net, cbs, _aoi_around(pipes)


def test_ottawa_baseline_catchbasin_voronoi(ottawa):
    net, cbs, aoi = ottawa
    assert len(cbs) == OTT_N_CATCHBASINS
    subs, _, diag = base.delineate_catchbasin_subcatchments(net, cbs, [], [], aoi, crs="EPSG:32618")

    assert len(subs) == OTT_N_CATCHBASINS                   # one cell per catch basin
    assert diag["n_dropped_invalid"] == 0
    r = validate_model(net, subs, aoi, method=CATCHBASIN_VORONOI)
    assert r.ok and r.errors == []
    assert _failed_warning_ids(r) == OTT_WARNINGS
    ids = {c.id: c for c in r.checks}
    assert ids["aoi_coverage"].passed and ids["overlap"].passed
    # The outlet-mapping risk stays surfaced (bounded, not exact: metres jitter across libs).
    m = ids["outlet_distance"].metrics
    assert m["n_gt_50m"] > 100 and m["max_m"] > 300


def test_ottawa_delineation_is_deterministic(ottawa):
    net, cbs, aoi = ottawa
    a, _, _ = base.delineate_catchbasin_subcatchments(net, cbs, [], [], aoi, crs="EPSG:32618")
    b, _, _ = base.delineate_catchbasin_subcatchments(net, cbs, [], [], aoi, crs="EPSG:32618")
    assert [(s.name, s.outlet_node, s.area_ha) for s in a] == \
           [(s.name, s.outlet_node, s.area_ha) for s in b]


# --- fallback: no catch-basin data -> junction-Voronoi, without error -----------


def test_no_catchbasins_falls_back_to_junction_voronoi(ottawa):
    net, _, aoi = ottawa
    subs, _, diag = base.delineate_catchbasin_subcatchments(net, [], [], [], aoi, crs="EPSG:32618")
    assert subs == [] and diag["reason"] == "insufficient catch basins"   # signals, not raises

    # ...and the pipeline's junction-Voronoi fallback yields a valid model.
    jxy = {j.name: (j.x, j.y) for j in net.junctions}
    fb = _build_subcatchments(jxy, aoi, NetworkConfig())
    assert len(fb) == OTT_N_JUNCTIONS
    r = validate_model(net, fb, aoi, method=JUNCTION_VORONOI)
    assert r.ok and r.errors == []


def test_junction_delineator_records_honest_fallback_without_dem(ottawa):
    """Delineation v2 (ADR 0010): with no DEM available the gated delineator must produce
    exactly the locked junction-Voronoi baseline AND say why in its diagnostics."""
    from swmmcanada.network.delineate_dem import delineate_junction_subcatchments

    net, _, aoi = ottawa
    jxy = {j.name: (j.x, j.y) for j in net.junctions}
    subs, diag = delineate_junction_subcatchments(jxy, aoi, dem_path=None)

    assert diag["method"] == "junction_voronoi"
    assert diag["gate"]["decision"] == "no_dem"          # the honesty trail
    assert len(subs) == OTT_N_JUNCTIONS                  # identical to the locked baseline
    baseline = _build_subcatchments(jxy, aoi, NetworkConfig())
    assert [(s.name, s.area_ha) for s in subs] == [(s.name, s.area_ha) for s in baseline]
