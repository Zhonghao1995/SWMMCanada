"""A user's own subcatchment layer outranks everything we would have decided.

Today's work was a long series of judgement calls — which method, what a flow length is,
how much of a road reserve is paved, whether kerbs are usable. That they were judgement
calls is the argument for this: where the answer is uncertain, the person with local
knowledge should be able to hand us theirs instead of arguing with ours.

It slots in as priority 0, above the city's own polygons. A municipal layer is authoritative
about the municipality; an uploaded layer is authoritative about what this user wants
modelled, and that outranks it.
"""
import pytest

from swmmcanada.delineation import Evidence, resolve
from swmmcanada.validate import schema

RING = [(-123.370, 48.420), (-123.365, 48.420), (-123.365, 48.425), (-123.370, 48.425),
        (-123.370, 48.420)]


class TestItOutranksEverything:
    def test_a_user_layer_wins_over_inlets_kerbs_and_terrain(self):
        """Even the best thing we could have produced does not override an explicit choice."""
        p = resolve(Evidence(n_user_units=120, n_junctions=391, n_parcels=4749,
                             n_kerbs=2189, dem_available=True, dem_resolution_m=1.0))
        assert p.method == schema.METHOD_USER_SUPPLIED

    def test_it_wins_over_an_official_municipal_layer(self):
        p = resolve(Evidence(n_user_units=120, official_basin_level="level_1"))
        assert p.method == schema.METHOD_USER_SUPPLIED

    def test_an_empty_upload_is_not_an_upload(self):
        """Nothing to use is not a choice to use nothing."""
        p = resolve(Evidence(n_user_units=0, n_junctions=391, n_parcels=4749))
        assert p.method == schema.METHOD_JUNCTION_PARCEL


class TestItDoesNotInheritOurConfidence:
    def test_the_plan_says_the_boundaries_are_not_ours(self):
        p = resolve(Evidence(n_user_units=120))
        assert p.boundary == "user"
        assert p.shaping == "user"

    def test_the_reason_names_the_source(self):
        p = resolve(Evidence(n_user_units=120))
        assert "supplied" in p.reason.lower() or "uploaded" in p.reason.lower()
        assert "120" in p.reason

    def test_confidence_is_not_a_claim_we_can_make(self):
        """We did not draw these and cannot vouch for them. `unrated` says that; `high`
        would be us endorsing someone else's work, `low` would be dismissing it."""
        p = resolve(Evidence(n_user_units=120))
        assert p.confidence == "unrated"

    def test_the_evidence_records_how_many_units_arrived(self):
        p = resolve(Evidence(n_user_units=120))
        assert p.evidence["n_user_units"] == 120


class TestTheVocabularyHasAWordForIt:
    def test_the_method_label_exists_and_says_user(self):
        assert schema.METHOD_USER_SUPPLIED == "user_supplied"

    def test_it_is_distinct_from_every_derived_method(self):
        derived = {schema.METHOD_CATCHBASIN_PARCEL, schema.METHOD_CATCHBASIN_DEM,
                   schema.METHOD_JUNCTION_DEM, schema.METHOD_JUNCTION_VORONOI,
                   schema.METHOD_CATCHBASIN_VORONOI}
        assert schema.METHOD_USER_SUPPLIED not in derived


class TestTheUploadIsActuallyUsed:
    """A resolver that recognises a layer nothing reads is the same defect three times
    over. See test_every_plan_choice_is_executed.py."""

    def _network(self):
        from swmmcanada.build.models import ConduitIn, JunctionIn, NetworkIn, OutfallIn

        return NetworkIn(
            junctions=[JunctionIn("J1", 9.0, -123.3675, 48.4225)],
            outfalls=[OutfallIn("OUT", 6.0, -123.3660, 48.4235)],
            conduits=[ConduitIn("C1", "J1", "OUT", 120.0)])

    def _feature(self, ring, **props):
        return {"type": "Feature", "properties": props,
                "geometry": {"type": "Polygon", "coordinates": [[list(p) for p in ring]]}}

    def test_the_boundaries_come_through_verbatim(self):
        from swmmcanada.pipeline import _subcatchments_from_user_layer

        subs, diag = _subcatchments_from_user_layer(
            [self._feature(RING, name="MyCatchment")], self._network(), "EPSG:32610")
        assert len(subs) == 1
        assert subs[0].name == "MyCatchment"
        assert len(subs[0].polygon) == len(RING)
        assert diag["method"] == "user_supplied"

    def test_an_outlet_the_user_names_is_honoured(self):
        from swmmcanada.pipeline import _subcatchments_from_user_layer

        subs, diag = _subcatchments_from_user_layer(
            [self._feature(RING, outlet="OUT")], self._network(), "EPSG:32610")
        assert subs[0].outlet_node == "OUT"
        assert diag["n_outlet_declared_by_user"] == 1

    def test_an_outlet_this_network_does_not_have_is_resolved_instead(self):
        """A polygon file rarely carries our node ids; naming one we do not have is not a
        reason to fail the upload."""
        from swmmcanada.pipeline import _subcatchments_from_user_layer

        subs, diag = _subcatchments_from_user_layer(
            [self._feature(RING, outlet="SOMEONE_ELSES_ID")], self._network(), "EPSG:32610")
        assert subs[0].outlet_node in ("J1", "OUT")
        assert diag["n_outlet_declared_by_user"] == 0

    def test_a_broken_ring_is_repaired_and_counted(self):
        from swmmcanada.pipeline import _subcatchments_from_user_layer

        bowtie = [(-123.370, 48.420), (-123.365, 48.425), (-123.370, 48.425),
                  (-123.365, 48.420), (-123.370, 48.420)]
        _subs, diag = _subcatchments_from_user_layer(
            [self._feature(bowtie)], self._network(), "EPSG:32610")
        assert diag["n_geometry_repaired"] == 1

    def test_a_feature_with_no_geometry_is_dropped_and_counted(self):
        from swmmcanada.pipeline import _subcatchments_from_user_layer

        subs, diag = _subcatchments_from_user_layer(
            [{"type": "Feature", "properties": {}, "geometry": None},
             self._feature(RING)], self._network(), "EPSG:32610")
        assert len(subs) == 1 and diag["n_dropped_invalid"] == 1

    def test_the_build_entry_point_accepts_a_layer(self):
        import inspect

        from swmmcanada.pipeline import build_city

        assert "subcatchment_layer" in inspect.signature(build_city).parameters
