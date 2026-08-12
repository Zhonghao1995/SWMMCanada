"""Outlet agreement reaches the build's validation output (#129).

A metric that only exists in a script is a one-off. The acceptance criterion is that a
build reports it, so a reader of a result package sees how well that model's outlets match
what the city declares — beside the tier badge and the topology figure, not in a report
nobody reads twice.
"""
import pytest

from swmmcanada.sources.cities.registry import CITIES, city_spec


class TestTheRegistryCanSupplyTheYardstick:
    def test_cityspec_has_a_slot_for_official_catchments(self):
        from swmmcanada.sources.cities.registry import CitySpec

        assert "official_catchments" in CitySpec.__dataclass_fields__

    def test_victoria_supplies_them(self):
        """The only city publishing both the polygons and a joinable outlet key."""
        assert city_spec("victoria").official_catchments is not None

    def test_cities_without_the_layer_declare_none_rather_than_erroring(self):
        assert city_spec("ottawa").official_catchments is None

    def test_the_slot_is_optional_for_every_city(self):
        for spec in CITIES:
            assert hasattr(spec, "official_catchments")


class TestPipelineReportsIt:
    def test_the_build_records_agreement_when_a_yardstick_exists(self, monkeypatch):
        """Wired at the seam so it is testable without a live build."""
        from swmmcanada.pipeline import _outlet_agreement_provenance
        from swmmcanada.build.models import (ConduitIn, JunctionIn, NetworkIn, OutfallIn,
                                             SurfaceCatchment)

        ring = [(-123.370, 48.420), (-123.360, 48.420), (-123.360, 48.430),
                (-123.370, 48.430), (-123.370, 48.420)]
        net = NetworkIn(junctions=[JunctionIn("J1", 9.0, -123.365, 48.425)],
                        outfalls=[OutfallIn("DOF1", 5.0, -123.361, 48.428)],
                        conduits=[ConduitIn("C1", "J1", "DOF1", 100.0)])
        subs = [SurfaceCatchment("S1", "J1", 1.0, 50.0, 100.0, 1.0, polygon=ring)]
        official = [{"type": "Feature",
                     "geometry": {"type": "Polygon",
                                  "coordinates": [[list(p) for p in ring]]},
                     "properties": {"OUTLET": "DOF1"}}]

        class Spec:
            key = "victoria"
            official_catchments = staticmethod(lambda bbox, client: official)

        out = _outlet_agreement_provenance(Spec(), (0, 0, 1, 1), None, subs, net)
        assert out["rate_pct"] == 100.0 and out["n_comparable"] == 1

    def test_a_city_without_a_yardstick_says_so_rather_than_omitting_it(self):
        """Silence would be indistinguishable from 'not measured yet'."""
        from swmmcanada.pipeline import _outlet_agreement_provenance

        class Spec:
            key = "ottawa"
            official_catchments = None

        out = _outlet_agreement_provenance(Spec(), (0, 0, 1, 1), None, [], None)
        assert out["rate_pct"] is None and "publishes no" in out["reason"]

    def test_a_failing_fetch_degrades_without_blocking_the_build(self):
        """The yardstick is additive. A municipal server being down must not fail a model."""
        from swmmcanada.pipeline import _outlet_agreement_provenance

        def boom(bbox, client):
            raise RuntimeError("503")

        class Spec:
            key = "victoria"
            official_catchments = staticmethod(boom)

        out = _outlet_agreement_provenance(Spec(), (0, 0, 1, 1), None, [], None)
        assert out["rate_pct"] is None and "RuntimeError" in out["reason"]
