"""A method label must not let a fallback pass for a delineation (规划书 §3 Level 5).

The label travels into provenance and the result package, where someone decides how much to
trust the model. "voronoi" reads to a hydrologist as a tessellation method — a technique.
It is not: assigning land to whichever node is nearest is what we do when we have nothing
to delineate with, and the name has to say so.
"""
import pytest

from swmmcanada.validate import schema


class TestTheVocabularyNamesItAsAFallback:
    def test_the_junction_fallback_says_fallback(self):
        assert schema.METHOD_JUNCTION_VORONOI == "fallback_voronoi_junction"

    def test_the_inlet_seeded_fallback_says_fallback(self):
        """Real inlets, geometric division: the seeds are evidence, the boundaries are not."""
        assert schema.METHOD_CATCHBASIN_VORONOI == "fallback_voronoi_catchbasin"

    def test_the_delineated_methods_are_not_renamed(self):
        """Only the fallbacks were misleading; renaming the rest would churn provenance for
        nothing."""
        assert schema.METHOD_CATCHBASIN_PARCEL == "catchbasin_parcel"
        assert schema.METHOD_JUNCTION_DEM == "junction_dem"

    @pytest.mark.parametrize("name", ["METHOD_JUNCTION_VORONOI",
                                      "METHOD_CATCHBASIN_VORONOI"])
    def test_every_fallback_label_carries_the_word(self, name):
        assert "fallback" in getattr(schema, name)


class TestTheLabelIsConsistentEverywhere:
    """Three modules mint this label. They must not drift, or a package says one thing and
    its validation report another."""

    def test_the_dem_module_uses_the_shared_constant(self):
        from swmmcanada.network import delineate_dem

        assert delineate_dem.METHOD_VORONOI == schema.METHOD_JUNCTION_VORONOI

    def test_the_resolver_uses_the_shared_constant(self):
        from swmmcanada.delineation import Evidence, resolve

        assert resolve(Evidence(n_junctions=5)).method == schema.METHOD_JUNCTION_VORONOI
        assert resolve(Evidence(n_catchbasins=5)).method == schema.METHOD_CATCHBASIN_VORONOI

    def test_the_pipeline_descriptor_uses_the_shared_constant(self):
        from swmmcanada.pipeline import _method_descriptor

        assert _method_descriptor({"method": "x"}).method == schema.METHOD_JUNCTION_VORONOI
        assert _method_descriptor(
            {"method": "catchbasin+parcel/building (voronoi-shaped)"}
        ).method == schema.METHOD_CATCHBASIN_VORONOI


def test_the_confidence_of_a_fallback_stays_low():
    """Renaming must not be mistaken for promoting it."""
    from swmmcanada.delineation import Evidence, resolve

    assert resolve(Evidence(n_junctions=5)).confidence == "low"
    assert resolve(Evidence(n_catchbasins=5)).confidence == "low"
