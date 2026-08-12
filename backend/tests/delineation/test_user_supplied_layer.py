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
        p = resolve(Evidence(n_user_units=120, n_catchbasins=773, n_parcels=4749,
                             n_kerbs=2189, dem_available=True, dem_resolution_m=1.0))
        assert p.method == schema.METHOD_USER_SUPPLIED

    def test_it_wins_over_an_official_municipal_layer(self):
        p = resolve(Evidence(n_user_units=120, official_basin_level="level_1"))
        assert p.method == schema.METHOD_USER_SUPPLIED

    def test_an_empty_upload_is_not_an_upload(self):
        """Nothing to use is not a choice to use nothing."""
        p = resolve(Evidence(n_user_units=0, n_catchbasins=773, n_parcels=4749))
        assert p.method == "catchbasin_parcel"


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
