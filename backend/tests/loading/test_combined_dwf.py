"""Combined nodes carry dry-weather flow (G1/V1, ADR 0029 Q1).

A combined sewer is dual-source: its pipes already take the storm runoff, and in dry
weather they carry the wastewater of the land they serve. Before this, a combined AOI
built half a system — pipes with rain and no baseflow. These tests run the same
service-area -> loading -> ``[DWF]`` path the sanitary system uses over the combined
subgraph of ONE hydraulic network (the trunk stays wired into the storm graph — ADR 0029
Q3 — no second graph, no SAN_-style namespace wall), and read the resulting ``.inp``.

Synthetic fixture throughout: a hand-made combined trunk joining a storm leg, population
attached by hand. Nothing municipal, nothing recorded.
"""
import re
from datetime import date, datetime, timedelta

import pytest

from swmmcanada.build import BuildConfig
from swmmcanada.build.assemble import build_model
from swmmcanada.build.models import (ConduitIn, JunctionIn, NetworkIn, OutfallIn,
                                     RainfallSeries, SewerServiceArea, SurfaceCatchment,
                                     filter_system)
from swmmcanada.geo import aoi_from_geojson
from swmmcanada.loading import load_service_areas

RING = [(-123.370, 48.420), (-123.360, 48.420), (-123.360, 48.430), (-123.370, 48.430),
        (-123.370, 48.420)]


def _combined_network() -> NetworkIn:
    """A combined trunk (K1 -> K2) draining INTO the storm leg (J1 -> OUT): one connected
    hydraulic component, tags telling the systems apart — the shape the Vancouver adapter
    now produces."""
    return NetworkIn(
        junctions=[JunctionIn("J1", 8.0, -123.365, 48.425, max_depth_m=3.0),
                   JunctionIn("K1", 12.0, -123.368, 48.421, max_depth_m=3.0,
                              system="combined"),
                   JunctionIn("K2", 11.0, -123.367, 48.423, max_depth_m=3.0,
                              system="combined")],
        outfalls=[OutfallIn("OUT", 6.5, -123.361, 48.428)],
        conduits=[ConduitIn("C1", "J1", "OUT", 120.0, diameter_m=0.6),
                  ConduitIn("CK1", "K1", "K2", 90.0, diameter_m=0.45, system="combined"),
                  ConduitIn("CK2", "K2", "J1", 90.0, diameter_m=0.45, system="combined")])


def _aoi():
    return aoi_from_geojson({"type": "Polygon", "coordinates": [[list(p) for p in RING]]})


def _dwf_rows(inp_text: str) -> dict:
    """{node: base_value} out of the .inp's [DWF] section."""
    block = inp_text.split("[DWF]")[1].split("[", 1)[0]
    rows = {}
    for line in block.splitlines():
        parts = line.split(";")[0].split()
        if len(parts) >= 3 and parts[1].upper() == "FLOW":
            rows[parts[0]] = float(parts[2])
    return rows


class TestServiceAreasOverTheCombinedView:
    def test_manhole_seeded_areas_land_on_combined_nodes(self):
        """Vancouver publishes no lateral layer, so seeds fall back to the combined
        manholes — the existing fallback, asked about a combined view instead of a
        sanitary one."""
        from swmmcanada.delineation.service_area import derive_service_areas

        view = filter_system(_combined_network(), "combined")
        areas, diag = derive_service_areas(view, parcels=[], aoi=_aoi(),
                                           crs="EPSG:32610", system="combined")
        assert diag["seed_source"] == "manhole"
        assert areas, "a two-manhole combined view must still serve land"
        assert {a.node for a in areas} <= {"K1", "K2"}
        assert all(a.system == "combined" for a in areas)


class TestDwfReachesTheInp:
    @pytest.fixture(scope="class")
    def built(self, tmp_path_factory):
        tmp = tmp_path_factory.mktemp("combined_dwf")
        t0 = datetime(2024, 6, 1)
        rain = RainfallSeries([t0 + timedelta(hours=i) for i in range(6)], [0.0] * 6)
        loaded = load_service_areas([
            SewerServiceArea("CSA_K1", "K1", 2.0, system="combined", polygon=RING,
                             population=200.0),
            SewerServiceArea("CSA_K2", "K2", 1.0, system="combined", polygon=RING,
                             population=100.0)])
        cfg = BuildConfig(out_dir=tmp, start=date(2024, 6, 1), end=date(2024, 6, 2))
        result = build_model(
            network=_combined_network(),
            subcatchments=[SurfaceCatchment("S1", "J1", 2.0, 55.0, 140.0, 1.2,
                                            polygon=RING)],
            rain=rain, config=cfg, service_areas=loaded.areas)
        return result.inp_path.read_text(), loaded

    def test_dwf_lands_on_the_combined_nodes(self, built):
        txt, _ = built
        rows = _dwf_rows(txt)
        assert set(rows) == {"K1", "K2"}, "DWF belongs on the combined nodes, nowhere else"

    def test_the_amounts_are_the_population_loading(self, built):
        """Population x per-capita rate, converted to the model's CMS — the number the
        loader intended is the number the engine will read."""
        txt, loaded = built
        rows = _dwf_rows(txt)
        by_node = {a.node: a.dwf_lps for a in loaded.areas}
        assert rows["K1"] == pytest.approx(by_node["K1"] * 1e-3, rel=1e-4)
        assert rows["K2"] == pytest.approx(by_node["K2"] * 1e-3, rel=1e-4)
        assert rows["K1"] == pytest.approx(200.0 * 280.0 / 86400.0 * 1e-3, rel=1e-4)

    def test_the_diurnal_pattern_rides_along(self, built):
        txt, _ = built
        assert "[PATTERNS]" in txt
        assert re.search(r"DWF_DIURNAL", txt)

    def test_the_wired_in_trunk_validates(self):
        """One component, one outfall, storm-combined contact legal (ADR 0029 Q5) — the
        dual-source shape must not trip the system-integrity gate."""
        from swmmcanada.validate.checks import check_system_outfalls

        r = check_system_outfalls(_combined_network())
        assert r.passed, r.details


class TestDatastoreSpineCarriesIt:
    def test_dwf_survives_the_datastore_build_path(self, tmp_path):
        """The production spine builds the .inp FROM the datastore (ADR 0007), so the
        combined loading must reach [DWF] through that path too, not only through a
        direct build_model call."""
        from swmmcanada.datastore import build_from_datastore, write_datastore

        t0 = datetime(2024, 6, 1)
        rain = RainfallSeries([t0 + timedelta(hours=i) for i in range(6)], [0.0] * 6)
        cfg = BuildConfig(out_dir=tmp_path / "b", start=date(2024, 6, 1),
                          end=date(2024, 6, 2))
        loaded = load_service_areas([SewerServiceArea(
            "CSA_K1", "K1", 2.0, system="combined", polygon=RING, population=200.0)])
        write_datastore(tmp_path / "ds", network=_combined_network(),
                        subcatchments=[SurfaceCatchment("S1", "J1", 2.0, 55.0, 140.0,
                                                        1.2, polygon=RING)],
                        rain=rain, config=cfg, service_areas=loaded.areas)
        result = build_from_datastore(tmp_path / "ds", tmp_path / "out")
        rows = _dwf_rows((tmp_path / "out" / result.inp_path.name).read_text())
        assert rows == {"K1": pytest.approx(200.0 * 280.0 / 86400.0 * 1e-3, rel=1e-4)}
