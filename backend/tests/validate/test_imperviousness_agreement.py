"""Imperviousness cross-check (ticket 13): physical pct_imperv vs the land-cover
built-up share vs the municipal design table, measured and REPORTED every build.

The blind spot this closes: the physical figure (roofs + roads) and the municipal
planning table can disagree by a category in low-density areas, and the land-cover
raster every build already pulls was never asked. The check only measures — the
physical figure itself is untouched (that is Phase 3) — and the numbers (medians of
each signal, the deviation distribution, the over-threshold count) must land in
validation.json, not just an ok/fail bit.
"""
import json

import pytest

from swmmcanada.build.models import JunctionIn, NetworkIn, SurfaceCatchment
from swmmcanada.geo import aoi_from_geojson
from swmmcanada.validate import MethodDescriptor, validate_model
from swmmcanada.validate import schema
from swmmcanada.validate.checks import check_imperviousness_agreement

AOI = aoi_from_geojson({"type": "Polygon", "coordinates": [[
    [-123.372, 48.418], [-123.368, 48.418], [-123.368, 48.422], [-123.372, 48.422],
    [-123.372, 48.418]]]})
NET = NetworkIn(junctions=[JunctionIn("J1", 10.0, -123.371, 48.420)],
                outfalls=[], conduits=[])
METHOD = MethodDescriptor(schema.METHOD_JUNCTION_PARCEL, "parcel-shaped node areas",
                          "medium")


def _cell(name, imperv, built, area_ha=1.0):
    return SurfaceCatchment(name=name, outlet_node="J1", area_ha=area_ha,
                            pct_imperv=imperv, width_m=100.0, pct_slope=1.0,
                            landcover_built_pct=built)


#: A known-deviation trio: A claims far more imperviousness than the raster sees built
#: land for (+50 pts), B is a built cell where the physical pass found almost nothing
#: (90% built, 1% impervious), C agrees. Two of three disagree -> the check fails.
DEVIANT = [_cell("A", 60.0, 10.0), _cell("B", 1.0, 90.0), _cell("C", 45.0, 90.0, area_ha=2.0)]


class TestPerCellDeviation:
    def test_known_deviant_cells_fail_the_check_with_the_numbers_reported(self):
        res = check_imperviousness_agreement(DEVIANT, city_key=None)
        assert res.id == "imperviousness_agreement"
        assert res.severity == schema.WARNING
        assert not res.passed
        m = res.metrics
        assert m["n_judged"] == 3
        # The three-signal medians and the deviation distribution — the report numbers.
        assert m["median_pct_imperv"] == pytest.approx(45.0)
        assert m["median_built_pct"] == pytest.approx(90.0)
        assert m["deviation_pts_median"] == pytest.approx(-45.0)
        assert m["deviation_pts_p05"] == pytest.approx(-89.0)
        assert m["deviation_pts_p95"] == pytest.approx(-45.0)
        assert m["n_over_threshold"] == 2
        assert m["n_excess"] == 1 and m["n_built_low_imperv"] == 1
        assert m["over_threshold_fraction"] == pytest.approx(2 / 3, abs=1e-3)
        assert set(m["sample"]) == {"A", "B"}

    def test_agreeing_cells_pass(self):
        res = check_imperviousness_agreement([_cell("C", 45.0, 90.0)], city_key=None)
        assert res.passed
        assert res.metrics["n_over_threshold"] == 0

    def test_cells_without_the_signal_are_counted_not_judged(self):
        cells = [_cell("C", 45.0, 90.0),
                 SurfaceCatchment("D", "J1", 1.0, 50.0, 100.0, 1.0)]  # no landcover signal
        res = check_imperviousness_agreement(cells, city_key=None)
        assert res.metrics["n_judged"] == 1 and res.metrics["n_no_signal"] == 1


class TestDegradation:
    def test_no_landcover_signal_at_all_declares_a_skip_not_a_verdict(self):
        cells = [SurfaceCatchment("D", "J1", 1.0, 50.0, 100.0, 1.0)]
        res = check_imperviousness_agreement(cells, city_key=None)
        assert res.passed
        assert "no land-cover built-up signal" in res.message
        assert res.metrics["n_judged"] == 0

    def test_an_unregistered_city_declares_the_missing_table(self):
        res = check_imperviousness_agreement([_cell("C", 45.0, 90.0)], city_key="atlantis")
        assert res.passed                       # the missing table is not a defect
        muni = res.metrics["municipal"]
        assert muni["available"] is False
        assert "no design-imperviousness table" in muni["reason"]

    def test_no_city_at_all_declares_the_same(self):
        muni = check_imperviousness_agreement([_cell("C", 45.0, 90.0)],
                                              city_key=None).metrics["municipal"]
        assert muni["available"] is False


class TestMunicipalLeg:
    """Aggregate only, and it says so: no per-cell land-use classification exists, so
    the area-weighted mean over predominantly built cells is compared against the
    registered residential band, with the basis stated in the report."""

    def test_the_registered_table_is_compared_and_reported(self):
        res = check_imperviousness_agreement(DEVIANT, city_key="vancouver")
        muni = res.metrics["municipal"]
        assert muni["available"] is True
        assert muni["residential_band"] == [55.0, 70.0]
        assert muni["table_median"] == pytest.approx(70.0)
        # (1*1 + 45*2) / 3 over the two >=50%-built cells (B and C).
        assert muni["built_weighted_mean_imperv"] == pytest.approx(30.3, abs=0.1)
        assert muni["gap_pts"] == pytest.approx(24.7, abs=0.1)
        assert muni["n_built_cells"] == 2
        assert "aggregate" in muni["basis"]

    def test_an_aggregate_far_outside_the_band_fails_even_when_cells_agree(self):
        # Every cell agrees per-cell (fully built, low-but-plausible imperviousness),
        # but the aggregate sits far below the registered residential band.
        cells = [_cell("A", 20.0, 95.0), _cell("B", 22.0, 95.0)]
        res = check_imperviousness_agreement(cells, city_key="vancouver")
        assert res.metrics["n_over_threshold"] == 0
        assert res.metrics["municipal"]["gap_pts"] > schema.IMPERV_MUNICIPAL_BAND_TOL_PTS
        assert not res.passed

    def test_no_predominantly_built_cells_declares_the_aggregate_uncomputable(self):
        res = check_imperviousness_agreement([_cell("A", 30.0, 20.0)],
                                             city_key="vancouver")
        muni = res.metrics["municipal"]
        assert muni["available"] is True and muni["built_weighted_mean_imperv"] is None
        assert res.passed                       # nothing to compare is not a defect


class TestWiredIntoValidateModel:
    def test_the_report_carries_the_check_and_its_numbers(self):
        report = validate_model(NET, DEVIANT, AOI, method=METHOD, city_key="vancouver")
        d = report.to_dict()
        check = next(c for c in d["checks"] if c["id"] == "imperviousness_agreement")
        assert check["severity"] == schema.WARNING and check["passed"] is False
        assert check["metrics"]["median_built_pct"] == pytest.approx(90.0)
        assert check["metrics"]["municipal"]["available"] is True
        assert report.ok                        # WARNING never blocks the build

    def test_it_runs_without_a_city_too(self):
        report = validate_model(NET, [_cell("C", 45.0, 90.0)], AOI, method=METHOD)
        check = next(c for c in report.checks if c.id == "imperviousness_agreement")
        assert check.passed
        assert check.metrics["municipal"]["available"] is False


class TestPipelineThreadsTheCity:
    def test_validate_or_raise_writes_the_municipal_numbers(self, tmp_path):
        from swmmcanada.pipeline import _validate_or_raise

        _validate_or_raise(NET, DEVIANT, AOI, METHOD, tmp_path, city_key="vancouver")
        report = json.loads((tmp_path / schema.VALIDATION_JSON).read_text())
        check = next(c for c in report["checks"] if c["id"] == "imperviousness_agreement")
        assert check["metrics"]["municipal"]["available"] is True
        assert check["metrics"]["n_over_threshold"] == 2
