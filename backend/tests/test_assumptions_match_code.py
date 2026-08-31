"""ASSUMPTIONS.md states numbers a reader is invited to check. They must be the numbers the
code actually uses.

A public honesty document that drifts from the code is worse than no document: it invites
trust in a figure nothing enforces. This is the same rule the capability table follows —
one source of truth, and the derived copy is checked rather than maintained by hand.
"""
import re
from pathlib import Path

import pytest

ASSUMPTIONS = Path(__file__).resolve().parents[2] / "ASSUMPTIONS.md"


@pytest.fixture(scope="module")
def doc():
    return ASSUMPTIONS.read_text()


class TestDryWeatherFlow:
    def test_per_capita_flow(self, doc):
        from swmmcanada.loading import DwfAssumptions

        assert f"{DwfAssumptions().litres_per_capita_day:.0f} L per person per day" in doc

    def test_household_size(self, doc):
        from swmmcanada.loading import DwfAssumptions

        assert f"{DwfAssumptions().persons_per_dwelling} persons per dwelling" in doc

    def test_assumed_density(self, doc):
        from swmmcanada.loading import DwfAssumptions

        assert f"{DwfAssumptions().persons_per_hectare:.0f} persons per hectare" in doc

    def test_diurnal_extremes(self, doc):
        from swmmcanada.loading import diurnal_pattern

        _, factors = diurnal_pattern()
        assert f"{min(factors):.2f}" in doc, "documented overnight minimum has drifted"
        assert f"{max(factors):.2f}" in doc, "documented morning peak has drifted"

    def test_the_pattern_really_is_volume_neutral_as_documented(self, doc):
        from swmmcanada.loading import diurnal_pattern

        _, factors = diurnal_pattern()
        assert "mean exactly 1.0" in doc
        assert abs(sum(factors) / len(factors) - 1.0) < 1e-3


class TestServiceAreas:
    def test_lateral_snap_distance(self, doc):
        from swmmcanada.delineation.service_area import MAX_LATERAL_SNAP_M

        assert f"{MAX_LATERAL_SNAP_M:.0f} m from any node" in doc


class TestTerminalOutlets:
    def test_boundary_drop(self, doc):
        from swmmcanada.delineation.outlet import BOUNDARY_DROP_M

        assert f"{BOUNDARY_DROP_M} m below the lowest node" in doc


class TestFleetFacts:
    """Numbers quoted from the capability scan. If a rescan changes them the document must
    change too — an out-of-date measurement reads as a current one."""

    def test_ottawa_outfall_count_is_quoted_correctly(self, doc):
        assert "13 outfalls" in doc

    def test_the_cso_finding_is_stated(self, doc):
        assert re.search(r"no supported city publishes a CSO", doc, re.I)

    def test_the_lateral_city_count_is_quoted(self, doc):
        assert "16 of the fleet" in doc


class TestOutletAgreementResult:
    """The published rate is a measurement, so the document must carry the sample size with
    it. A rate without its coverage reads as a whole-model figure."""

    def test_the_rate_and_its_coverage_are_both_stated(self, doc):
        assert "80.3% agreement" in doc
        assert "1,185 comparable units" in doc
        assert "62.9% of the model" in doc

    def test_the_misleading_first_reading_is_recorded(self, doc):
        """Keeping the 3.8% in the document is the point: it shows why the exclusion exists
        rather than leaving it as an unexplained rule."""
        assert "3.8%" in doc and "artefact of the clip" in doc

    def test_it_is_scoped_to_one_city(self, doc):
        # Whitespace-normalised: the sentence wraps, and a line break must not be able to
        # make a claim about scope silently untestable.
        flat = " ".join(doc.split())
        assert "one city's number, not the fleet's" in flat


class TestWidthMethod:
    def test_the_measured_width_change_is_stated(self, doc):
        """A change that moves every hydrograph must say how far it moved them."""
        flat = " ".join(doc.split())
        assert "median width is **1.58x**" in flat
        assert "3,387 cells" in flat

    def test_the_consequence_is_stated_not_just_the_ratio(self, doc):
        assert "responds faster" in doc


class TestDelineationMethodLabels:
    def test_the_reproduction_mode_label_is_documented(self, doc):
        """`junction_parcel_row` travels into provenance like any other label; the public
        method-label table must say what it means and that it only runs on request."""
        from swmmcanada.validate import schema

        assert f"`{schema.METHOD_JUNCTION_PARCEL_ROW}`" in doc
