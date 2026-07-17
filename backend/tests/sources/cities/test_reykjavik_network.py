"""Reykjavík fráveita -> SWMM NetworkIn, via the shared cities.base assembler.

Reykjavík has no node ids and no inverts ON the pipe — inverts (BOTNKODI) and rims (HAED) live on
the structure points. The adapter snaps each pipe endpoint to its nearest structure and lifts those
onto the network. Run against the synthetic fitjuskrá-schema fixtures in tests/fixtures/reykjavik/.
"""
import json
from datetime import date, datetime
from pathlib import Path

from swmmcanada.build.assemble import build_model
from swmmcanada.build.config import BuildConfig
from swmmcanada.build.models import RainfallSeries, SubcatchmentIn
from swmmcanada.sources.cities.reykjavik import (
    _is_outfall, _roughness, _safe_name, _system_of, build_reykjavik_network,
)

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "reykjavik"


def _load(name):
    return json.load(open(FIX / name))["features"]


def _storm():
    return {"pipes": _load("storm_pipes.geojson"), "structures": _load("structures.geojson")}


def test_reykjavik_network_from_fixtures(tmp_path):
    res = build_reykjavik_network(_storm())
    net = res.network
    assert len(net.junctions) > 0 and len(net.conduits) == 2 and len(net.outfalls) >= 1

    names = [j.name for j in net.junctions] + [o.name for o in net.outfalls]
    assert all(n and str(n).strip() for n in names), "no empty node names"
    assert len(names) == len(set(names)), "unique node names"
    node_set = set(names)
    for c in net.conduits:
        assert c.from_node in node_set and c.to_node in node_set
    assert all(c.from_node != c.to_node for c in net.conduits)   # no self-loops

    # build-compatibility: the inferred network is genuinely SWMM-valid
    sub = SubcatchmentIn(name="S1", outlet_node=net.junctions[0].name, area_ha=1.0,
                         pct_imperv=60.0, width_m=100.0, pct_slope=1.0)
    rain = RainfallSeries(timestamps=[datetime(2022, 6, 1), datetime(2022, 6, 2)], precip_mm=[8.0, 0.0])
    out = build_model(network=net, subcatchments=[sub], rain=rain,
                      config=BuildConfig(out_dir=tmp_path, start=date(2022, 6, 1), end=date(2022, 6, 2)))
    assert out.inp_path.exists()
    assert res.diagnostics["city"] == "reykjavik"


def test_botnkodi_inverts_and_haed_rims_are_snapped_from_structures():
    """The defining Reykjavík behaviour: structure BOTNKODI becomes the node invert and HAED sets
    the max depth, even though neither is published on the pipe."""
    res = build_reykjavik_network(_storm())
    inverts = {round(j.invert_m, 3) for j in res.network.junctions}
    inverts |= {round(o.invert_m, 3) for o in res.network.outfalls}
    assert 10.0 in inverts and 9.5 in inverts   # MH-001 / MH-002 BOTNKODI lifted onto the nodes
    assert res.diagnostics["n_ends_snapped"] == 4 and res.diagnostics["n_ends_no_struct"] == 0
    # HAED (rim) - BOTNKODI (invert) => a real max depth, not the assembler default of 2.0 m
    mh1 = next(j for j in res.network.junctions if j.name == "RVK-MH-001")
    assert abs(mh1.max_depth_m - 3.0) < 1e-6


def test_audkenni_becomes_the_node_id_and_endi_becomes_the_outfall():
    res = build_reykjavik_network(_storm())
    names = {j.name for j in res.network.junctions} | {o.name for o in res.network.outfalls}
    assert "RVK-MH-001" in names            # AUDKENNI carried through as the SWMM node id
    assert any(o.name == "RVK-OUT-001" for o in res.network.outfalls)   # HLUTUR='Endi' -> outfall


def test_invert_published_above_rim_is_rejected():
    """Real LÚKK has occasional 'upside-down' rows (BOTNKODI above HAED — impossible). The bad
    invert must be dropped and gap-filled from neighbours, not seated as the node bottom."""
    A0, A1 = (-21.9400, 64.1300), (-21.9398, 64.1300)
    data = {"pipes": [_pipe(1, A0, A1)],
            "structures": [{"properties": {"AUDKENNI": "BAD", "HLUTUR": "brunnur",
                                           "HAED": 10.0, "BOTNKODI": 12.0},   # invert 2 m above rim
                            "geometry": {"type": "Point", "coordinates": list(A0)}},
                           _struct("GOOD", A1)]}                              # HAED 13 / BOTNKODI 10
    res = build_reykjavik_network(data)
    assert res.diagnostics["n_inverts_above_rim_rejected"] == 1
    bad = next(j for j in res.network.junctions if j.name == "BAD")
    assert abs(bad.invert_m - 10.0) < 1e-6                # gap-filled from GOOD, not the bogus 12.0


def test_innihald_tags_storm_combined_sanitary():
    def feat(v):
        return {"properties": {"INNIHALD": v}}
    assert _system_of(feat("ofanvatn")) == "storm"
    assert _system_of(feat("blandað")) == "combined"     # combined joins storm at fetch time
    assert _system_of(feat("skólp")) == "sanitary"
    assert _system_of(feat("")) == "storm"               # unknown/blank -> storm (counted, not dropped)


def test_sanitary_skeleton_assembles_from_fixture():
    """The separated sanitary main (INNIHALD=skólp) assembles into a routable skeleton via the
    unchanged builder — per-component sinks stand in for the treatment-bound exits (ADR 0011)."""
    res = build_reykjavik_network({"pipes": _load("sanitary_pipes.geojson"),
                                   "structures": _load("structures.geojson")})
    net = res.network
    # 1 real main + a dedicated sink link at the lowest node (neither end is an 'Endi' outfall),
    # so the treatment-bound skeleton still drains (ADR 0011).
    assert len(net.junctions) > 0 and len(net.conduits) >= 1 and len(net.outfalls) >= 1
    node_names = {j.name for j in net.junctions} | {o.name for o in net.outfalls}
    assert all(c.from_node in node_names and c.to_node in node_names for c in net.conduits)


def test_icelandic_material_roughness():
    assert _roughness("steypa") == 0.013     # concrete
    assert _roughness("plast") == 0.010      # plastic
    assert _roughness("leir") == 0.013       # clay
    assert _roughness("stál") == 0.012       # steel (audit found in real data)
    assert _roughness("óþekkt") == 0.013     # unknown -> default
    assert _roughness(None) == 0.013         # default


def test_freetext_outfall_detection():
    """Real HLUTUR is free-text: outfalls arrive both clean ('endi') and embedded
    ('Nidurflogn 6 Endi'). Inlets/manholes must NOT be mistaken for outfalls."""
    assert _is_outfall("endi") and _is_outfall("Nidurflogn 6 Endi") and _is_outfall("útrás")
    assert not _is_outfall("brunnur") and not _is_outfall("niðurfall") and not _is_outfall("tengi")


def test_safe_name_strips_swmm_illegal_chars():
    """SWMM names can't hold spaces/quotes; real AUDKENNI do ('Sk logn steinn 150', 'Nf2 ')."""
    assert _safe_name("Sk logn steinn 150") == "Sklognsteinn150"
    assert _safe_name('RB"12') == "RB12"
    assert _safe_name("Nf2 ") == "Nf2"
    assert _safe_name("R.132") == "R.132" and _safe_name("BR-14_1") == "BR-14_1"
    assert _safe_name("   ") == ""           # nothing survives -> caller uses a generated id


def test_ids_with_spaces_and_quotes_build_a_valid_swmm_model():
    """End-to-end: space/quote-laden ids must not crash or produce duplicate/invalid node names
    (real Kópavogur ERROR 207 on 'S_RB / RB from a quoted, space-containing id)."""
    A0, A1 = (-21.9400, 64.1300), (-21.9398, 64.1300)
    data = {"pipes": [_pipe(1, A0, A1)],
            "structures": [{"properties": {"AUDKENNI": "Sk logn steinn 150", "HLUTUR": "brunnur",
                                           "HAED": 13.0, "BOTNKODI": 10.0},
                            "geometry": {"type": "Point", "coordinates": list(A0)}},
                           _struct("R.7", A1)]}
    res = build_reykjavik_network(data)
    names = [j.name for j in res.network.junctions] + [o.name for o in res.network.outfalls]
    assert all(" " not in n and '"' not in n for n in names)     # SWMM-legal names
    assert "Sklognsteinn150" in names                            # sanitised id kept
    assert len(names) == len(set(n.lower() for n in names))


# --- real-data edge cases (found running the adapter on live Kópavogur data) ---------------

def _pipe(oid, a, b, innihald="regnvatn"):
    return {"properties": {"OBJECTID": oid, "EFNISGERD": "steypa", "TVERMAL": 300,
                           "INNIHALD": innihald, "Shape__Length": 20.0},
            "geometry": {"type": "LineString", "coordinates": [list(a), list(b)]}}


def _struct(audkenni, xy, hlutur="brunnur"):
    return {"properties": {"AUDKENNI": audkenni, "HLUTUR": hlutur, "HAED": 13.0, "BOTNKODI": 10.0},
            "geometry": {"type": "Point", "coordinates": list(xy)}}


def test_nonunique_audkenni_falls_back_to_generated_ids():
    """Icelandic asset ids are NOT globally unique across a fetch window (R./S. series restart).
    An id seen at two distinct nodes must NOT collapse them into one SWMM node."""
    A0, A1 = (-21.9400, 64.1300), (-21.9398, 64.1300)
    B0, B1 = (-21.9000, 64.1000), (-21.8998, 64.1000)   # far away
    data = {"pipes": [_pipe(1, A0, A1), _pipe(2, B0, B1)],
            "structures": [_struct("DUP", A0), _struct("A1", A1),
                           _struct("DUP", B0), _struct("B1", B1)]}   # "DUP" at two distinct nodes
    res = build_reykjavik_network(data)
    names = [j.name for j in res.network.junctions] + [o.name for o in res.network.outfalls]
    assert len(names) == len(set(names))                 # no duplicate node names -> no crash
    assert "DUP" not in names                            # the colliding id was dropped
    assert res.diagnostics["n_labels_dropped_nonunique"] == 2


def test_reserved_generated_namespace_id_is_dropped():
    """The assembler names unlabelled nodes N1, N2, …; a real 'N5' must not collide with a
    generated 'N5'. A leading-zero 'N05' is NOT a generated form, so it survives."""
    A0, A1 = (-21.9400, 64.1300), (-21.9398, 64.1300)
    B0, B1 = (-21.9000, 64.1000), (-21.8998, 64.1000)
    data = {"pipes": [_pipe(1, A0, A1), _pipe(2, B0, B1)],
            "structures": [_struct("N5", A0), _struct("R.7", A1),
                           _struct("N05", B0), _struct("R.8", B1)]}
    res = build_reykjavik_network(data)
    names = [j.name for j in res.network.junctions] + [o.name for o in res.network.outfalls]
    assert len(names) == len(set(names))
    assert res.diagnostics["n_labels_dropped_reserved"] == 1     # only 'N5', not 'N05'
    assert "R.7" in names and "N05" in names                     # safe real ids kept


def test_case_insensitive_id_collision_is_dropped():
    """SWMM folds node names to one case, so 'NF10' and 'nf10' at two distinct nodes are ONE name
    to the engine (real Kópavogur ERROR 207). Both must drop to generated ids."""
    A0, A1 = (-21.9400, 64.1300), (-21.9398, 64.1300)
    B0, B1 = (-21.9000, 64.1000), (-21.8998, 64.1000)
    data = {"pipes": [_pipe(1, A0, A1), _pipe(2, B0, B1)],
            "structures": [_struct("NF10", A0), _struct("R.7", A1),
                           _struct("nf10", B0), _struct("R.8", B1)]}   # case-variant at a far node
    res = build_reykjavik_network(data)
    names = [j.name for j in res.network.junctions] + [o.name for o in res.network.outfalls]
    lowered = [n.lower() for n in names]
    assert len(lowered) == len(set(lowered)), "case-folded node names must be unique (SWMM rule)"
    assert res.diagnostics["n_labels_dropped_nonunique"] == 2       # both NF10 and nf10 dropped
