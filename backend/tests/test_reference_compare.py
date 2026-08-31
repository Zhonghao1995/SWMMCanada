"""Reference-model comparison / reproduction harness.

The harness answers two questions about a municipally supplied SWMM `.inp` (a confidential
local file that never enters the repo) versus our build of the same AOI: *how far apart are
the two models* (network/partition comparison) and *can we reproduce their delineation*
(unit-level outlet agreement, per-node load bias, per-node service-area IoU).

Everything here runs on SYNTHETIC fixtures — a hand-written mini `.inp` and a datastore
written by `write_datastore` — because the real reference input is exactly the thing tests
must never contain. Each metric is pinned to a hand-computed answer.
"""
import json
from datetime import date, datetime, timedelta

import pytest
from pyproj import Transformer
from shapely.geometry import Polygon

from swmmcanada.build import BuildConfig
from swmmcanada.build.models import (ConduitIn, JunctionIn, NetworkIn, OutfallIn,
                                     RainfallSeries, SewerServiceArea, SurfaceCatchment)
from swmmcanada.datastore import write_datastore
from swmmcanada.reference_compare import (CompareUnit, ModelSide, aggregate_by_node,
                                          canon_node, compare, load_bias, main,
                                          outlet_agreement, partition_stats,
                                          read_build, read_reference_model, render_text,
                                          service_iou)

# --------------------------------------------------------------------------- #
# the asset-ID join (reference "MH123" <-> our "123", plus exact same-name)
# --------------------------------------------------------------------------- #
class TestCanonNode:
    def test_mh_prefix_maps_to_bare_digits(self):
        assert canon_node("MH123") == "123"

    def test_case_insensitive_prefix(self):
        assert canon_node("mh123") == "123"

    def test_bare_digits_unchanged(self):
        assert canon_node("123") == "123"

    def test_non_mh_names_join_by_exact_equality(self):
        assert canon_node("MHX12") == "MHX12"
        assert canon_node("J5") == "J5"
        assert canon_node("MH") == "MH"

    def test_non_string_labels_are_coerced(self):
        # swmm-api may hand back numeric-looking labels as numbers
        assert canon_node(201) == "201"


# --------------------------------------------------------------------------- #
# pure metric functions, hand-computed answers
# --------------------------------------------------------------------------- #
def _square(x, y, side=10.0):
    return Polygon([(x, y), (x + side, y), (x + side, y + side), (x, y + side)])


def _grid_units(prefix, outlets, **kw):
    """One 10x10 square per outlet entry, spaced 20 apart on the x axis."""
    return [CompareUnit(name=f"{prefix}{i + 1}", outlet=out, area_ha=kw.get("area", 0.2),
                        pct_imperv=kw.get("imperv", 50.0), geometry=_square(20.0 * i, 0.0))
            for i, out in enumerate(outlets)]


class TestOutletAgreement:
    def test_known_answer_six_of_eight_agree(self):
        # 8 reference land units; our identical squares reassign 2 to other nodes
        ref = _grid_units("U", ["MH1", "MH1", "MH2", "MH2", "MH3", "MH3", "MH3", "MH3"])
        ours = _grid_units("V", ["1", "1", "2", "9", "3", "3", "MH3", "8"])
        got = outlet_agreement(ref, ours)
        assert got["n_ref_units"] == 8
        assert got["n_matched"] == 8
        assert got["n_agree"] == 6
        assert got["rate"] == pytest.approx(0.75)
        assert len(got["mismatches"]) == 2
        assert {m["ref_outlet"] for m in got["mismatches"]} == {"MH2", "MH3"}

    def test_both_join_forms_count_as_agreement(self):
        # MH-prefix vs bare digits AND exact same-name, in one partition
        ref = _grid_units("U", ["MH7", "PS1"])
        ours = _grid_units("V", ["7", "PS1"])
        assert outlet_agreement(ref, ours)["rate"] == pytest.approx(1.0)

    def test_units_overlapping_nothing_are_unmatched_not_wrong(self):
        ref = [CompareUnit("U1", "MH1", 0.2, 50.0, _square(0, 0)),
               CompareUnit("U2", "MH2", 0.2, 50.0, _square(1000, 0))]
        ours = [CompareUnit("V1", "1", 0.2, 50.0, _square(0, 0))]
        got = outlet_agreement(ref, ours)
        assert got["n_matched"] == 1 and got["n_agree"] == 1
        assert got["n_unmatched"] == 1
        assert got["rate"] == pytest.approx(1.0)

    def test_majority_overlap_decides_the_match(self):
        # our two units split the reference square 30/70 -> the 70% owner's outlet wins
        ref = [CompareUnit("U1", "MH1", 0.2, 50.0, _square(0, 0, side=10))]
        ours = [CompareUnit("V1", "9", 0.2, 50.0, Polygon([(0, 0), (3, 0), (3, 10), (0, 10)])),
                CompareUnit("V2", "1", 0.2, 50.0, Polygon([(3, 0), (10, 0), (10, 10), (3, 10)]))]
        got = outlet_agreement(ref, ours)
        assert got["n_agree"] == 1 and got["rate"] == pytest.approx(1.0)

    def test_no_geometry_anywhere_returns_skip(self):
        ref = [CompareUnit("U1", "MH1", 0.2, 50.0, None)]
        ours = [CompareUnit("V1", "1", 0.2, 50.0, None)]
        got = outlet_agreement(ref, ours)
        assert got.get("skipped")


class TestNodeLoads:
    def test_aggregate_sums_by_canonical_node(self):
        units = [CompareUnit("a", "MH1", 0.2, 50.0), CompareUnit("b", "1", 0.3, 50.0),
                 CompareUnit("c", "2", 0.4, 50.0)]
        assert aggregate_by_node(units) == pytest.approx({"1": 0.5, "2": 0.4})

    def test_aggregate_with_a_value_function(self):
        units = [CompareUnit("a", "1", 2.0, 25.0), CompareUnit("b", "1", 1.0, 50.0)]
        got = aggregate_by_node(units, value=lambda u: u.area_ha * u.pct_imperv / 100.0)
        assert got == pytest.approx({"1": 1.0})

    def test_load_bias_known_answer(self):
        got = load_bias({"1": 10.0, "2": 20.0, "3": 30.0, "9": 5.0},
                        {"1": 11.0, "2": 18.0, "3": 33.0, "8": 7.0})
        assert got["n_common"] == 3
        assert got["n_ref_only"] == 1 and got["n_ours_only"] == 1
        assert got["pearson_r"] == pytest.approx(0.9786, abs=1e-3)
        assert got["diff"]["median"] == pytest.approx(1.0)
        assert got["ratio_median"] == pytest.approx(1.1)

    def test_load_bias_with_constant_values_has_no_correlation(self):
        got = load_bias({"1": 5.0, "2": 5.0}, {"1": 5.0, "2": 5.0})
        assert got["pearson_r"] is None
        assert got["diff"]["median"] == pytest.approx(0.0)

    def test_load_bias_with_no_common_nodes(self):
        got = load_bias({"1": 5.0}, {"2": 5.0})
        assert got["n_common"] == 0
        assert got["pearson_r"] is None


class TestServiceIoU:
    def test_known_answer_identical_and_shifted(self):
        ref = [CompareUnit("A1", "MH1", 0.2, 0.0, _square(0, 0)),
               CompareUnit("A2", "MH2", 0.2, 0.0, _square(20, 0))]
        ours = [CompareUnit("B1", "1", 0.2, 0.0, _square(0, 0)),
                CompareUnit("B2", "2", 0.2, 0.0, _square(25, 0))]  # half-shifted
        got = service_iou(ref, ours)
        assert got["n_nodes_common"] == 2
        assert got["per_node"]["1"] == pytest.approx(1.0)
        # intersection 5x10=50, union 150 -> exactly 1/3
        assert got["per_node"]["2"] == pytest.approx(1.0 / 3.0)
        assert got["stats"]["median"] == pytest.approx((1.0 + 1.0 / 3.0) / 2.0)

    def test_multiple_units_union_per_node(self):
        ref = [CompareUnit("A1", "MH1", 0.2, 0.0, _square(0, 0)),
               CompareUnit("A2", "MH1", 0.2, 0.0, _square(20, 0))]
        ours = [CompareUnit("B1", "1", 0.2, 0.0, _square(0, 0))]
        got = service_iou(ref, ours)
        assert got["per_node"]["1"] == pytest.approx(0.5)

    def test_nodes_with_geometry_on_one_side_only_are_counted(self):
        ref = [CompareUnit("A1", "MH1", 0.2, 0.0, _square(0, 0))]
        ours = [CompareUnit("B1", "2", 0.2, 0.0, _square(0, 0))]
        got = service_iou(ref, ours)
        assert got["n_nodes_common"] == 0
        assert got["n_ref_only"] == 1 and got["n_ours_only"] == 1


class TestPartitionStats:
    def test_known_answer(self):
        areas = [0.2, 0.2, 0.2, 0.2, 0.4, 0.4, 0.4, 1.0]
        imperv = [42, 42, 42, 42, 76, 76, 76, 8]
        units = [CompareUnit(f"u{i}", "1", a, v) for i, (a, v) in enumerate(zip(areas, imperv))]
        got = partition_stats(units)
        assert got["n"] == 8
        assert got["total_area_ha"] == pytest.approx(3.0)
        assert got["median_area_ha"] == pytest.approx(0.3)
        assert got["imperv_decade_share"] == pytest.approx(
            {"40-50": 0.5, "70-80": 0.375, "0-10": 0.125})

    def test_empty_partition(self):
        got = partition_stats([])
        assert got["n"] == 0 and got["median_area_ha"] is None


# --------------------------------------------------------------------------- #
# synthetic end-to-end fixture: mini .inp + our datastore of "the same AOI"
# --------------------------------------------------------------------------- #
PLANE = "EPSG:26917"  # any projected plane; the fixture is Ottawa-ish like its neighbours
LON0, LAT0 = -75.700, 45.410
_TO_PLANE = Transformer.from_crs("EPSG:4326", PLANE, always_xy=True)


def _ring4326(i, row=0):
    lon, lat = LON0 + 0.002 * i, LAT0 + 0.004 * row
    return [(lon, lat), (lon + 0.001, lat), (lon + 0.001, lat + 0.001), (lon, lat + 0.001)]


def _poly_lines(name, ring):
    return "\n".join("{} {:.3f} {:.3f}".format(name, *_TO_PLANE.transform(x, y))
                     for x, y in ring)


def _reference_inp_text():
    """A dozen pipes, 8 runoff units, 3 zero-area sanitary carriers. Entirely invented."""
    storm = [  # (name, outlet, area, imperv, grid position)
        ("U1", "MH101", 0.2, 42, 0), ("U2", "MH101", 0.2, 42, 1),
        ("U3", "MH102", 0.2, 42, 2), ("U4", "MH102", 0.2, 42, 3),
        ("U5", "MH103", 0.4, 76, 4), ("U6", "MH103", 0.4, 76, 5),
        ("U7", "MH103", 0.4, 76, 6), ("U8", "MH103", 1.0, 8, 7),
    ]
    sanitary = [("W1", "MH101", 0), ("W2", "MH102", 1), ("W3", "MH103", 2)]
    subs = "\n".join(f"{n} RG1 {out} {a} {v} 50 1.0 0" for n, out, a, v, _ in storm)
    subs += "\n" + "\n".join(f"{n} RG1 {out} 0.0 0 50 1.0 0" for n, out, _ in sanitary)
    polys = "\n".join(_poly_lines(n, _ring4326(i)) for n, _, _, _, i in storm)
    polys += "\n" + "\n".join(_poly_lines(n, _ring4326(i, row=1)) for n, _, i in sanitary)
    pipes = "\n".join(f"P{i + 1} {f} {t} 80 0.013 0 0" for i, (f, t) in enumerate(
        [("MH101", "MH102"), ("MH102", "MH103"), ("MH103", "201"), ("MH104", "MH103")] * 3))
    xs = "\n".join(f"P{i} CIRCULAR {d} 0 0 0 1" for i, d in
                   zip(range(1, 12), [0.3] * 5 + [0.45] * 4 + [0.6] * 2))
    xs += "\nP12 RECT_CLOSED 1.0 0.8 0 0 1"
    return f"""\
[TITLE]
synthetic reference fixture

[OPTIONS]
FLOW_UNITS LPS

[JUNCTIONS]
MH101 99.2 3.0 0 0 0
MH102 98.7 3.0 0 0 0
MH103 98.4 3.0 0 0 0
MH104 99.5 3.0 0 0 0

[OUTFALLS]
201 97.9 FREE

[CONDUITS]
{pipes}

[XSECTIONS]
{xs}

[SUBCATCHMENTS]
{subs}

[DWF]
MH101 FLOW 40
MH102 FLOW 25

[POLYGONS]
{polys}
"""


def _our_build(tmp_path, as_package=False):
    """Our side of the same imaginary AOI, written through the real datastore writer."""
    network = NetworkIn(
        junctions=[JunctionIn("101", 99.0, LON0, LAT0), JunctionIn("102", 98.5, LON0, LAT0),
                   JunctionIn("103", 98.2, LON0, LAT0), JunctionIn("109", 98.9, LON0, LAT0)],
        outfalls=[OutfallIn("201", 97.9, LON0, LAT0)],
        conduits=[ConduitIn("C1", "101", "102", 80.0, diameter_m=0.45),
                  ConduitIn("C2", "102", "103", 80.0, diameter_m=0.30),
                  ConduitIn("C3", "103", "201", 80.0, diameter_m=0.60),
                  ConduitIn("C4", "109", "103", 80.0, diameter_m=0.30)])
    outlets = ["101", "101", "102", "109", "103", "103", "103", "101"]
    imperv = [42, 42, 42, 42, 76, 76, 76, 8]
    areas = [0.2, 0.2, 0.2, 0.2, 0.4, 0.4, 0.4, 1.0]
    subs = [SurfaceCatchment(f"S{i + 1}", outlets[i], areas[i], imperv[i], 50.0, 1.0,
                             polygon=_ring4326(i)) for i in range(8)]
    subs.append(SurfaceCatchment("S9", "103", 0.1, 30.0, 50.0, 1.0, polygon=None))
    service = [
        SewerServiceArea("T1", "101", 0.2, polygon=_ring4326(0, row=1), population=40.0),
        SewerServiceArea("T2", "102", 0.2, polygon=_ring4326(1, row=1), population=20.0),
        SewerServiceArea("T3", "109", 0.2, polygon=_ring4326(2, row=1), population=10.0),
    ]
    t0 = datetime(2024, 6, 1)
    rain = RainfallSeries([t0 + timedelta(hours=h) for h in range(6)], [0.0] * 6)
    cfg = BuildConfig(out_dir=tmp_path / "build", start=date(2024, 6, 1), end=date(2024, 6, 2))
    root = tmp_path / ("pkg" if as_package else "ds")
    ds_dir = root / "datastore" if as_package else root
    write_datastore(ds_dir, network=network, subcatchments=subs, rain=rain, config=cfg,
                    service_areas=service)
    return root


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("refcmp")
    inp = tmp / "reference.inp"
    inp.write_text(_reference_inp_text())
    build = _our_build(tmp)
    return inp, build, tmp


# --------------------------------------------------------------------------- #
# the reference-side loader (swmm-api, no hand-written parser)
# --------------------------------------------------------------------------- #
class TestReadReferenceModel:
    def test_network_counts_and_inverts(self, built):
        side = read_reference_model(built[0])
        assert side.n_junctions == 4 and side.n_outfalls == 1 and side.n_conduits == 12
        assert side.node_inverts["MH101"] == pytest.approx(99.2)
        assert side.node_inverts["201"] == pytest.approx(97.9)

    def test_units_split_storm_vs_sanitary_by_declared_area(self, built):
        side = read_reference_model(built[0])
        assert {u.name for u in side.units_storm} == {f"U{i}" for i in range(1, 9)}
        assert {u.name for u in side.units_sanitary} == {"W1", "W2", "W3"}
        u1 = next(u for u in side.units_storm if u.name == "U1")
        assert u1.outlet == "MH101" and u1.area_ha == pytest.approx(0.2)
        assert u1.pct_imperv == pytest.approx(42.0)
        assert u1.geometry is not None and u1.geometry.area > 0

    def test_population_read_from_dwf_flow_base_values(self, built):
        side = read_reference_model(built[0])
        assert side.population_by_node == pytest.approx({"MH101": 40.0, "MH102": 25.0})

    def test_diameters_from_xsections(self, built):
        side = read_reference_model(built[0])
        assert sorted(side.diameters_mm) == [300] * 5 + [450] * 4 + [600] * 2
        assert side.n_noncircular == 1

    def test_missing_sections_degrade_with_a_note(self, tmp_path):
        inp = tmp_path / "bare.inp"
        inp.write_text("[TITLE]\nbare\n\n[JUNCTIONS]\nMH1 10 3 0 0 0\n\n"
                       "[CONDUITS]\nP1 MH1 MH1 10 0.013 0 0\n\n"
                       "[SUBCATCHMENTS]\nU1 RG1 MH1 0.5 55 50 1.0 0\n")
        side = read_reference_model(inp)
        assert side.units_storm and side.units_storm[0].geometry is None
        assert any("POLYGONS" in n for n in side.notes)
        assert any("DWF" in n for n in side.notes)
        assert any("XSECTIONS" in n for n in side.notes)


# --------------------------------------------------------------------------- #
# the our-side loader (datastore dir or result-package root)
# --------------------------------------------------------------------------- #
class TestReadBuild:
    def test_reads_a_datastore_directory(self, built):
        side = read_build(built[1], to_crs=PLANE)
        assert side.n_junctions == 4 and side.n_outfalls == 1 and side.n_conduits == 4
        assert len(side.units_storm) == 9
        assert len(side.units_sanitary) == 3
        assert side.population_by_node == pytest.approx({"101": 40.0, "102": 20.0, "109": 10.0})
        assert sorted(side.diameters_mm) == [300, 300, 450, 600]

    def test_geometry_lands_in_the_reference_plane(self, built):
        side = read_build(built[1], to_crs=PLANE)
        s1 = next(u for u in side.units_storm if u.name == "S1")
        assert s1.geometry.centroid.x > 100_000  # projected metres, not degrees

    def test_polygonless_units_carry_loads_but_no_geometry(self, built):
        side = read_build(built[1], to_crs=PLANE)
        s9 = next(u for u in side.units_storm if u.name == "S9")
        assert s9.geometry is None and s9.area_ha == pytest.approx(0.1)

    def test_without_a_crs_geometry_is_dropped_and_declared(self, built):
        side = read_build(built[1], to_crs=None)
        assert all(u.geometry is None for u in side.units_storm)
        assert any("crs" in n.lower() for n in side.notes)

    def test_reads_a_result_package_root(self, tmp_path):
        root = _our_build(tmp_path, as_package=True)
        side = read_build(root, to_crs=PLANE)
        assert side.n_junctions == 4
        assert side.meta.get("layout") == "result_package"


# --------------------------------------------------------------------------- #
# the full report: every metric present as a number, both systems
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def report(built):
    ref = read_reference_model(built[0])
    ours = read_build(built[1], to_crs=PLANE)
    return compare(ref, ours)


class TestCompareReport:
    def test_storm_outlet_agreement_known_answer(self, report):
        got = report["storm"]["outlet_agreement"]
        assert got["n_ref_units"] == 8 and got["n_agree"] == 6
        assert got["rate"] == pytest.approx(0.75)

    def test_sanitary_outlet_agreement_known_answer(self, report):
        got = report["sanitary"]["outlet_agreement"]
        assert got["n_matched"] == 3 and got["n_agree"] == 2
        assert got["rate"] == pytest.approx(2.0 / 3.0)

    def test_storm_node_area_loads(self, report):
        got = report["storm"]["node_loads"]["area_ha"]
        assert got["n_common"] == 3
        assert got["diff"]["median"] == pytest.approx(-0.2)
        assert got["pearson_r"] == pytest.approx(0.4336, abs=1e-3)

    def test_storm_impervious_weighted_loads_present(self, report):
        got = report["storm"]["node_loads"]["impervious_area_ha"]
        assert got["n_common"] == 3

    def test_sanitary_population_loads(self, report):
        got = report["sanitary"]["node_loads"]["population"]
        assert got["n_common"] == 2
        assert got["diff"]["median"] == pytest.approx(-2.5)
        assert got["pearson_r"] == pytest.approx(1.0)

    def test_storm_service_iou_known_answer(self, report):
        got = report["storm"]["service_iou"]
        assert got["per_node"]["101"] == pytest.approx(2.0 / 3.0, abs=1e-3)
        assert got["per_node"]["102"] == pytest.approx(0.5, abs=1e-3)
        assert got["per_node"]["103"] == pytest.approx(0.75, abs=1e-3)

    def test_sanitary_service_iou(self, report):
        got = report["sanitary"]["service_iou"]
        assert got["n_nodes_common"] == 2
        assert got["per_node"]["101"] == pytest.approx(1.0, abs=1e-6)

    def test_partition_stats_both_sides(self, report):
        ref = report["storm"]["partition"]["reference"]
        ours = report["storm"]["partition"]["ours"]
        assert ref["n"] == 8 and ref["median_area_ha"] == pytest.approx(0.3)
        assert ours["n"] == 9 and ours["total_area_ha"] == pytest.approx(3.1)

    def test_network_counts_and_invert_profile(self, report):
        counts = report["network"]["counts"]
        assert counts["junctions"] == {"reference": 4, "ours": 4}
        assert counts["conduits"] == {"reference": 12, "ours": 4}
        inv = report["network"]["invert_diff_m"]
        assert inv["n_common"] == 4
        assert inv["diff"]["median"] == pytest.approx(-0.2)

    def test_diameter_distribution_lists_both_sides(self, report):
        rows = {r["mm"]: r for r in report["network"]["diameters_mm"]}
        assert rows[300] == {"mm": 300, "reference": 5, "ours": 2}
        assert rows[600] == {"mm": 600, "reference": 2, "ours": 1}

    def test_full_fixture_report_is_json_serialisable(self, report):
        json.dumps(report)


class TestDegradedRun:
    def test_missing_sections_degrade_but_still_report(self, tmp_path):
        inp = tmp_path / "bare.inp"
        inp.write_text("[TITLE]\nbare\n\n[JUNCTIONS]\nMH101 10 3 0 0 0\n\n"
                       "[CONDUITS]\nP1 MH101 MH101 10 0.013 0 0\n\n"
                       "[SUBCATCHMENTS]\nU1 RG1 MH101 0.5 55 50 1.0 0\n")
        build = _our_build(tmp_path)
        rep = compare(read_reference_model(inp), read_build(build, to_crs=PLANE))
        assert any("POLYGONS" in n for n in rep["degraded"])
        assert rep["storm"]["outlet_agreement"].get("skipped")
        assert rep["storm"]["service_iou"].get("skipped")
        assert rep["sanitary"]["node_loads"]["population"].get("skipped")
        # non-geometric metrics still produce numbers
        assert rep["storm"]["node_loads"]["area_ha"]["n_common"] == 1
        render_text(rep)  # must not crash


# --------------------------------------------------------------------------- #
# text report + CLI: numbers, not ok/fail
# --------------------------------------------------------------------------- #
class TestTextAndCli:
    def test_text_report_lists_the_numbers(self, report):
        txt = render_text(report)
        assert "0.75" in txt                    # outlet agreement rate
        assert "outlet agreement" in txt
        assert "-0.200" in txt                  # invert diff median
        assert "300" in txt and "600" in txt    # diameter table
        assert "IoU" in txt
        assert "ok" not in txt.split() and "fail" not in txt.split()

    def test_main_writes_json_and_prints_text(self, built, tmp_path, capsys):
        inp, build, _ = built
        out = tmp_path / "report.json"
        rc = main([str(inp), str(build), "--inp-crs", PLANE, "--json", str(out)])
        assert rc == 0
        rep = json.loads(out.read_text())
        assert rep["storm"]["outlet_agreement"]["rate"] == pytest.approx(0.75)
        assert rep["reference_inp"] == str(inp)
        assert rep["inp_crs"] == PLANE
        stdout = capsys.readouterr().out
        assert "outlet agreement" in stdout and "0.75" in stdout
