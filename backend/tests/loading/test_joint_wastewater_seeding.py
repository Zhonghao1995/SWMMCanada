"""Joint wastewater seeding in mixed AOIs (ticket 16 — the DWF double-count fix).

After the combined DWF work, sanitary and combined each derived service areas by
tessellating the WHOLE AOI (voronoi fills the boundary). Where one AOI carries both kinds
of pipe, the same land loaded both systems and total DWF came out at roughly twice the
population's wastewater. The fix: ONE derivation over the joint wastewater view
(``filter_system(network, ("sanitary", "combined"))``), each area attributed to the system
of the node it loads. Pure-sanitary and pure-combined AOIs are single-system views, so
their behaviour is unchanged by construction.

Synthetic fixtures throughout: hand-made networks whose sanitary half sits west and
combined half east of one AOI, so the joint tessellation has an obvious right answer.
"""
from datetime import date

import pytest

from swmmcanada import pipeline
from swmmcanada.build.models import (ConduitIn, JunctionIn, NetworkIn, OutfallIn,
                                     filter_system)
from swmmcanada.delineation.service_area import derive_service_areas
from swmmcanada.geo import aoi_from_geojson
from swmmcanada.loading import DwfAssumptions, load_service_areas
from swmmcanada.sources.cities import base
from swmmcanada.sources.cities.registry import CitySpec

RING = [(-123.370, 48.420), (-123.360, 48.420), (-123.360, 48.430), (-123.370, 48.430),
        (-123.370, 48.420)]


def _aoi():
    return aoi_from_geojson({"type": "Polygon", "coordinates": [[list(p) for p in RING]]})


def _aoi_ha():
    return _aoi().area_km2 * 100.0


def _expected_total_dwf_lps():
    """The whole AOI's population, counted ONCE: area x assumed density x per-capita rate.
    The pre-fix double derivation produced twice this."""
    a = DwfAssumptions()
    return _aoi_ha() * a.persons_per_hectare * a.litres_per_capita_day / 86400.0


def _mixed_wastewater_network() -> NetworkIn:
    """Sanitary west (SAN_M1/M2 -> SAN_WWTP), combined east (K1 -> K2 -> storm J1), one
    storm leg — the post-graft shape build_city produces for a mixed AOI."""
    return NetworkIn(
        junctions=[
            JunctionIn("J1", 8.0, -123.365, 48.425),
            JunctionIn("K1", 12.0, -123.3625, 48.4225, system="combined"),
            JunctionIn("K2", 11.0, -123.3625, 48.4275, system="combined"),
            JunctionIn("SAN_M1", 10.0, -123.3675, 48.4225, system="sanitary"),
            JunctionIn("SAN_M2", 9.0, -123.3675, 48.4275, system="sanitary"),
        ],
        outfalls=[OutfallIn("OUT", 6.5, -123.361, 48.428),
                  OutfallIn("SAN_WWTP", 5.0, -123.3675, 48.429, system="sanitary")],
        conduits=[ConduitIn("C1", "J1", "OUT", 120.0, diameter_m=0.6),
                  ConduitIn("CK1", "K1", "K2", 90.0, diameter_m=0.45, system="combined"),
                  ConduitIn("CK2", "K2", "J1", 90.0, diameter_m=0.45, system="combined"),
                  ConduitIn("SM1", "SAN_M1", "SAN_M2", 90.0, system="sanitary"),
                  ConduitIn("SM2", "SAN_M2", "SAN_WWTP", 90.0, system="sanitary")])


def _node_system(network: NetworkIn) -> dict:
    return {n.name: n.system for n in list(network.junctions) + list(network.outfalls)}


class TestJointViewAttribution:
    """derive_service_areas over the joint wastewater view: each area belongs to the
    system of the node it loads, not to a caller-supplied stamp."""

    def test_the_joint_view_carries_both_systems(self):
        view = filter_system(_mixed_wastewater_network(), ("sanitary", "combined"))
        assert {j.system for j in view.junctions} == {"sanitary", "combined"}
        assert not any(j.name == "J1" for j in view.junctions), "storm stays out"

    def test_each_area_takes_its_nodes_system(self):
        net = _mixed_wastewater_network()
        view = filter_system(net, ("sanitary", "combined"))
        areas, _ = derive_service_areas(view, [], _aoi(), crs="EPSG:32610")
        assert areas
        by_node = _node_system(net)
        assert {a.system for a in areas} == {"sanitary", "combined"}
        for a in areas:
            assert a.system == by_node[a.node], (a.name, a.node, a.system)

    def test_one_tessellation_serves_the_aoi_once(self):
        view = filter_system(_mixed_wastewater_network(), ("sanitary", "combined"))
        areas, _ = derive_service_areas(view, [], _aoi(), crs="EPSG:32610")
        assert sum(a.area_ha for a in areas) == pytest.approx(_aoi_ha(), rel=0.02)

    def test_loading_the_joint_areas_counts_the_population_once(self):
        view = filter_system(_mixed_wastewater_network(), ("sanitary", "combined"))
        areas, _ = derive_service_areas(view, [], _aoi(), crs="EPSG:32610")
        res = load_service_areas(areas)
        assert res.diagnostics["total_dwf_lps"] == pytest.approx(
            _expected_total_dwf_lps(), rel=0.02)

    def test_the_split_is_reported_per_system(self):
        view = filter_system(_mixed_wastewater_network(), ("sanitary", "combined"))
        _areas, diag = derive_service_areas(view, [], _aoi(), crs="EPSG:32610")
        counts = diag["n_areas_by_system"]
        assert counts.get("sanitary", 0) > 0 and counts.get("combined", 0) > 0

    def test_an_explicit_stamp_still_overrides(self):
        """The legacy single-system callers (and their tests) stamp the view's system on
        every area; that escape hatch must survive the per-node default."""
        view = filter_system(_mixed_wastewater_network(), ("sanitary", "combined"))
        areas, _ = derive_service_areas(view, [], _aoi(), crs="EPSG:32610",
                                        system="combined")
        assert all(a.system == "combined" for a in areas)


# ---------------------------------------------------------------------------------------
# build_city: ONE wastewater branch instead of two full-AOI derivations
# ---------------------------------------------------------------------------------------

def _storm_with_combined() -> NetworkIn:
    """The storm fetch of a mixed city: storm leg + combined mains riding in tagged."""
    return NetworkIn(
        junctions=[JunctionIn("J1", 8.0, -123.365, 48.425),
                   JunctionIn("K1", 12.0, -123.3625, 48.4225, system="combined"),
                   JunctionIn("K2", 11.0, -123.3625, 48.4275, system="combined")],
        outfalls=[OutfallIn("OUT", 6.5, -123.361, 48.428)],
        conduits=[ConduitIn("C1", "J1", "OUT", 120.0, diameter_m=0.6),
                  ConduitIn("CK1", "K1", "K2", 90.0, diameter_m=0.45, system="combined"),
                  ConduitIn("CK2", "K2", "J1", 90.0, diameter_m=0.45, system="combined")])


def _storm_only() -> NetworkIn:
    return NetworkIn(
        junctions=[JunctionIn("J1", 8.0, -123.365, 48.425),
                   JunctionIn("J2", 7.5, -123.3625, 48.4275)],
        outfalls=[OutfallIn("OUT", 6.5, -123.361, 48.428)],
        conduits=[ConduitIn("C1", "J1", "OUT", 120.0),
                  ConduitIn("C2", "J2", "J1", 120.0)])


def _sanitary_published() -> NetworkIn:
    """What a city's sanitary fetch returns (unprefixed; build_city grafts it as SAN_*
    and invents the treatment boundary)."""
    return NetworkIn(
        junctions=[JunctionIn("M1", 10.0, -123.3675, 48.4225, system="sanitary"),
                   JunctionIn("M2", 9.0, -123.3675, 48.4275, system="sanitary")],
        outfalls=[],
        conduits=[ConduitIn("S1", "M1", "M2", 90.0, system="sanitary")])


def _spec(storm_net, sanitary_net=None, sanitary_calls=None):
    def _sanitary(bbox, client):
        if sanitary_calls is not None:
            sanitary_calls.append(bbox)
        return base.NetworkResult(network=sanitary_net)

    return CitySpec(
        key="mixedville", label="Mixedville fixture",
        coverage=(-123.37, 48.42, -123.36, 48.43), sub_crs="EPSG:32610",
        network_source="synthetic fixture",
        storm=lambda bbox, client: base.NetworkResult(network=storm_net),
        land=lambda bbox, client: {},
        sanitary=None if sanitary_net is None else _sanitary)


def _build(tmp_path, monkeypatch, spec, **kw):
    """build_city offline up to the build spine, capturing what reaches it (the same
    harness shape test_follow_municipal_practice uses)."""
    captured = {}

    def fake_finish(ws, aoi, network, subcatchments, **k):
        captured.update(k)
        captured["network"] = network
        return "BUILT"

    monkeypatch.setattr(pipeline, "_finish_build", fake_finish)
    monkeypatch.setattr(pipeline, "fetch_street_graph",
                        lambda bbox: (_ for _ in ()).throw(RuntimeError("offline")))
    result = pipeline.build_city(
        spec, _aoi(), date(2020, 6, 1), date(2020, 6, 2), tmp_path / "ws",
        derive=False, subcatchment_method="junction", **kw)
    assert result == "BUILT"
    return captured


class TestMixedAoiCountsThePopulationOnce:
    """The acceptance number: a mixed AOI's total DWF is population x rate x ONE."""

    def test_total_dwf_is_not_doubled(self, tmp_path, monkeypatch):
        cap = _build(tmp_path, monkeypatch,
                     _spec(_storm_with_combined(), _sanitary_published()))
        total = sum(a.dwf_lps for a in cap["service_areas"])
        assert total == pytest.approx(_expected_total_dwf_lps(), rel=0.02)

    def test_the_land_is_tiled_once_not_once_per_system(self, tmp_path, monkeypatch):
        cap = _build(tmp_path, monkeypatch,
                     _spec(_storm_with_combined(), _sanitary_published()))
        assert sum(a.area_ha for a in cap["service_areas"]) == pytest.approx(
            _aoi_ha(), rel=0.02)

    def test_each_node_is_loaded_by_its_own_system(self, tmp_path, monkeypatch):
        cap = _build(tmp_path, monkeypatch,
                     _spec(_storm_with_combined(), _sanitary_published()))
        by_node = _node_system(cap["network"])
        assert {a.system for a in cap["service_areas"]} == {"sanitary", "combined"}
        for a in cap["service_areas"]:
            assert a.system == by_node[a.node], (a.name, a.node, a.system)

    def test_the_two_halves_split_where_the_pipes_sit(self, tmp_path, monkeypatch):
        """Sanitary west, combined east, symmetric seeds: each side serves half."""
        cap = _build(tmp_path, monkeypatch,
                     _spec(_storm_with_combined(), _sanitary_published()))
        per = {"sanitary": 0.0, "combined": 0.0}
        for a in cap["service_areas"]:
            per[a.system] += a.dwf_lps
        assert per["sanitary"] == pytest.approx(_expected_total_dwf_lps() / 2, rel=0.05)
        assert per["combined"] == pytest.approx(_expected_total_dwf_lps() / 2, rel=0.05)

    def test_provenance_splits_the_diagnostics_per_system(self, tmp_path, monkeypatch):
        prov = _build(tmp_path, monkeypatch,
                      _spec(_storm_with_combined(),
                            _sanitary_published()))["extra_provenance"]
        san, comb = prov["sanitary"], prov["combined"]
        assert san["included"] is True and comb["included"] is True
        assert "terminal_outlet" in san, "the graft record stays on the sanitary key"
        total = (san["service_areas"]["total_dwf_lps"]
                 + comb["service_areas"]["total_dwf_lps"])
        assert total == pytest.approx(_expected_total_dwf_lps(), rel=0.02)
        for block in (san, comb):
            assert block["service_areas"]["joint_seeding"]["systems"] == [
                "sanitary", "combined"]

    def test_build_city_derives_wastewater_service_areas_once(self):
        """The branch merge itself: one derivation call, not one per system."""
        import inspect

        src = inspect.getsource(pipeline.build_city)
        assert src.count("derive_service_areas(") == 1


class TestSelectionGatesTheTiling:
    """Multi-select still gates: an unselected system gets no DWF, and the tessellation
    runs over the SELECTED wastewater set (ADR 0029 Q3 honoured at build time)."""

    def test_only_sanitary_leaves_combined_dry(self, tmp_path, monkeypatch):
        cap = _build(tmp_path, monkeypatch,
                     _spec(_storm_with_combined(), _sanitary_published()),
                     systems=["storm", "sanitary"])
        assert cap["extra_provenance"]["combined"] == {
            "included": False, "reason": "not selected"}
        assert {a.system for a in cap["service_areas"]} == {"sanitary"}
        assert not any(a.node in ("K1", "K2") for a in cap["service_areas"])
        # The selected system tiles the AOI exactly as it did before the joint branch.
        assert sum(a.area_ha for a in cap["service_areas"]) == pytest.approx(
            _aoi_ha(), rel=0.02)

    def test_only_combined_leaves_sanitary_dry_and_unfetched(self, tmp_path, monkeypatch):
        calls: list = []
        cap = _build(tmp_path, monkeypatch,
                     _spec(_storm_with_combined(), _sanitary_published(),
                           sanitary_calls=calls),
                     systems=["storm", "combined"])
        assert calls == [], "an unselected sanitary system must not be fetched"
        assert cap["extra_provenance"]["sanitary"] == {
            "included": False, "reason": "not selected"}
        assert {a.system for a in cap["service_areas"]} == {"combined"}
        assert {a.node for a in cap["service_areas"]} <= {"K1", "K2"}


class TestPureAoisAreUntouched:
    """Single-system AOIs pass through the joint branch unchanged: the joint view of one
    system IS that system's view."""

    def test_pure_separated_city(self, tmp_path, monkeypatch):
        cap = _build(tmp_path, monkeypatch, _spec(_storm_only(), _sanitary_published()))
        prov = cap["extra_provenance"]
        assert prov["combined"] == {"included": False,
                                    "reason": "no combined mains in this model"}
        assert prov["sanitary"]["included"] is True
        assert {a.system for a in cap["service_areas"]} == {"sanitary"}
        assert sum(a.dwf_lps for a in cap["service_areas"]) == pytest.approx(
            _expected_total_dwf_lps(), rel=0.02)

    def test_pure_combined_city(self, tmp_path, monkeypatch):
        cap = _build(tmp_path, monkeypatch, _spec(_storm_with_combined()))
        prov = cap["extra_provenance"]
        assert prov["sanitary"] == {"included": False, "reason": "not_published"}
        assert prov["combined"]["included"] is True
        assert prov["combined"]["service_areas"]["seed_source"] == "manhole"
        assert {a.system for a in cap["service_areas"]} == {"combined"}
        assert sum(a.dwf_lps for a in cap["service_areas"]) == pytest.approx(
            _expected_total_dwf_lps(), rel=0.02)
