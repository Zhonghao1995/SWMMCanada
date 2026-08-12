"""Classification regressions (ADR 0030).

Every case here is a mistake the classifier actually made during Phase 0 development. The
audit's whole value is that a wrong role silently corrupts a measurement — an inflated
anchor denominator turns a Level 1 layer into "review_required" and nobody finds out — so
each fix is pinned.
"""
from swmmcanada.audit.classify import suggest, suggest_role, suggest_system
from swmmcanada.sources.cities.capability import EXPECTED_GEOMETRY, Role


class TestSystemPrecedence:
    """The layer name decides; the service name only fills silence."""

    def test_layer_beats_service_name(self):
        # Ottawa serves storm, sanitary AND combined pipes from one service called
        # "WastewaterInfrastructure". Service-first precedence filed every storm pipe in
        # the city as sanitary.
        assert suggest_system("WastewaterInfrastructure", "Storm Pipes") == "storm"
        assert suggest_system("WastewaterInfrastructure", "Sanitary Pipes") == "sanitary"
        assert suggest_system("WastewaterInfrastructure", "Combined Pipes") == "combined"

    def test_service_fills_silence(self):
        assert suggest_system("OpenData_StormDrain", "Gravity Mains") == "storm"

    def test_combined_outranks_within_one_name(self):
        # "Wastewater" here describes what overflows, not which network.
        assert suggest_system("", "Combined Overflow Wastewater Catchment Areas") == "combined"

    def test_bare_sewer_defers_to_a_human(self):
        # "Sewer" means sanitary in Victoria's catalogue and everything in Toronto's.
        assert suggest_system("OpenData_Sewer", "Sewer SubCatchment Areas") is None

    def test_roleless_layers_have_no_system(self):
        assert suggest_system("OpenData_Planimetry", "Curbs") is None


class TestRolePatterns:
    def test_subcatchment_beats_catchment(self):
        assert suggest_role("Sewer SubCatchment Areas") is Role.SUBCATCHMENT
        assert suggest_role("Sewer Catchment Areas") is Role.CATCHMENT

    def test_specific_assets(self):
        assert suggest_role("Storm Drain Catch Basins") is Role.CATCH_BASIN
        assert suggest_role("Storm Drain Lateral Line") is Role.LATERAL
        assert suggest_role("Sewer Outfall (Discharge)") is Role.OUTFALL
        assert suggest_role("Curbs") is Role.CURB
        assert suggest_role("Parcels (PID based)") is Role.PARCEL


class TestCartographicDerivatives:
    """Layers that are *about* an asset without *being* it. Each one measured non-zero and
    would have been counted as the real thing."""

    def test_flow_arrows_are_not_mains(self):
        # A second copy of Victoria's 4,686 mains, drawn as arrows — double counting.
        assert suggest_role("Storm Drain Flow Arrows - Gravity Mains") is None

    def test_cleanouts_are_not_mains(self):
        # 8,703 access points would have inflated the sanitary anchor denominator ~2.9x.
        assert suggest_role("Sewer Cleanout") is None

    def test_dimension_lines_are_not_parcels(self):
        assert suggest_role("Parcel Dimension Lines") is None

    def test_cartographic_shadows_excluded(self):
        # London publishes "Buildings Shadow" with the identical 193,347 count as
        # "Buildings" — a drawing layer, counted it would double the roof evidence.
        assert suggest_role("Buildings Shadow") is None

    def test_retired_assets_excluded(self):
        assert suggest_role("Abandoned Storm Mains") is None
        assert suggest_role("Proposed Sanitary Sewers") is None


class TestExpectedGeometry:
    """The geometry gate is the backstop for names that slip past the patterns."""

    def test_every_non_raster_role_declares_geometry(self):
        missing = [r for r in Role if r is not Role.DEM and r not in EXPECTED_GEOMETRY]
        assert not missing, f"roles without an expected geometry: {missing}"

    def test_area_roles_require_polygons(self):
        for role in (Role.CATCHMENT, Role.SUBCATCHMENT, Role.PARCEL, Role.BUILDING):
            assert EXPECTED_GEOMETRY[role] == {"esriGeometryPolygon"}

    def test_linear_roles_reject_points(self):
        # "Sewer Fittings"/"Sewer Flow Meter" match \bsewer\b -> gravity_main by name, and
        # are points. The gate is what stops them being counted as pipe segments.
        assert "esriGeometryPoint" not in EXPECTED_GEOMETRY[Role.GRAVITY_MAIN]

    def test_structures_may_be_point_or_footprint(self):
        assert "esriGeometryPolygon" in EXPECTED_GEOMETRY[Role.OUTFALL]


def test_suggest_returns_both_halves():
    role, system = suggest("OpenData_StormDrain", "Storm Drain Catch Basins")
    assert (role, system) == (Role.CATCH_BASIN, "storm")


class TestNonDrainageUtilities:
    """Same asset words, different network. Coquitlam alone publishes 7,172 water mains and
    26,929 water service connections — counted as drainage they would swamp any anchor
    denominator they landed in."""

    def test_potable_water_is_not_drainage(self):
        for name in ("Water Mains", "Water Lateral Lines", "Water Service Connections",
                     "Watermain", "Water Valve"):
            assert suggest_role(name) is None, name

    def test_other_utilities_excluded(self):
        for name in ("Methane Conduit", "Methane Vent Pipe", "Gas Main", "Hydro Duct"):
            assert suggest_role(name) is None, name

    def test_drainage_mains_still_classify(self):
        assert suggest_role("Storm Drain Gravity Mains") is Role.GRAVITY_MAIN
        assert suggest_role("Sewer Gravity Mains") is Role.GRAVITY_MAIN


class TestPressurisedPipes:
    def test_force_mains_are_not_gravity_segments(self):
        """A force main has no contributing area of its own, and the city adapters already
        keep it out of the routable graph. The anchor count must agree."""
        for name in ("Sewer Force Main", "Sewer Force Main CRD", "Pressurized Mains"):
            assert suggest_role(name) is None, name


class TestServiceConnections:
    def test_bare_services_are_laterals(self):
        # Esquimalt publishes 3,087 "Sewer Services" — service connections, not mains.
        assert suggest_role("Sewer Services") is Role.LATERAL
        assert suggest_role("Sanitary Lateral") is Role.LATERAL


def test_land_roles_are_system_agnostic():
    """Asking which system a parcel belongs to is a category error: it drains to storm and
    discharges to sanitary. They must never be reported as awaiting classification."""
    from swmmcanada.sources.cities.capability import SYSTEM_AGNOSTIC_ROLES
    assert SYSTEM_AGNOSTIC_ROLES == {Role.PARCEL, Role.BUILDING, Role.CURB, Role.DEM}
    for role in SYSTEM_AGNOSTIC_ROLES:
        assert role not in {Role.GRAVITY_MAIN, Role.LATERAL, Role.CATCH_BASIN}


class TestCamelCaseNames:
    """Municipal catalogues mix conventions freely: Victoria writes "Storm Drain Gravity
    Mains", Whiterock writes "StormPipe". Word-boundary patterns saw only the first, and a
    missed pipe layer is a missing anchor denominator — 357 drainage-relevant layers across
    the fleet were landing in `unclassified` for this reason alone."""

    def test_pascal_case_assets_classify(self):
        assert suggest_role("StormPipe") is Role.GRAVITY_MAIN
        assert suggest_role("SanitaryPipe") is Role.GRAVITY_MAIN
        assert suggest_role("StormCatchBasin") is Role.CATCH_BASIN
        assert suggest_role("SanitaryManhole") is Role.MANHOLE

    def test_system_inference_is_camel_aware(self):
        assert suggest_system("X", "StormPipe") == "storm"
        assert suggest_system("X", "SanitaryPipe") == "sanitary"

    def test_spaced_names_still_work(self):
        assert suggest_role("Storm Drain Gravity Mains") is Role.GRAVITY_MAIN


class TestMoreAssetWords:
    def test_culverts_are_conduits(self):
        assert suggest_role("StormCulverts") is Role.GRAVITY_MAIN
        assert suggest_role("Drainage Culverts") is Role.GRAVITY_MAIN

    def test_outlets_are_outfalls(self):
        assert suggest_role("Storm Outlets") is Role.OUTFALL

    def test_preliminary_copies_excluded(self):
        """`PrelimStormPipe` duplicates the live layer; counted, it doubles the anchor."""
        assert suggest_role("PrelimStormPipe") is None
        assert suggest_role("PrelimSanitaryPipe") is None
