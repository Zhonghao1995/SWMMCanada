"""Follow municipal practice, consumed for real (spec §G2/§V4) — and the standalone
Green-Ampt antecedent option (spec §G4).

The build chain under test: registered practice row -> ``follow_municipal_practice``
resolves the build knobs -> derive computes GA parameters under the stated antecedent ->
the followed Ksat/surface transforms land on the subcatchments -> the writer emits them
into ``[INFILTRATION]``/``[SUBAREAS]``. Everything runs offline on synthetic fixtures:
a hand-made one-junction city spec (keyed "vancouver" so the REAL registered row drives
the build — the values asserted here are the registry's aggregate conventions, nothing
municipal beyond them), tiny synthetic rasters, and the acquisition seams stubbed. The
capture point is ``_finish_build`` (the build spine); its network/subcatchments/config
then feed the real writer, so the .inp assertions see exactly what a build would ship.
"""
import re
from datetime import date, datetime
from types import SimpleNamespace

import numpy as np
import pytest
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin

from swmmcanada import pipeline
from swmmcanada.acquire.landcover import (
    DEFAULT_NALCMS_IMPERVIOUS, DEFAULT_NALCMS_LEGEND, LandcoverResult,
)
from swmmcanada.acquire.soil import DEFAULT_HSG_TO_CN, SoilResult
from swmmcanada.build.assemble import build_model
from swmmcanada.build.config import InfiltrationModel
from swmmcanada.build.models import (
    ConduitIn, JunctionIn, NetworkIn, OutfallIn, RainfallSeries,
)
from swmmcanada.geo import aoi_from_geojson
from swmmcanada.sources.cities import base
from swmmcanada.sources.cities.registry import CitySpec

RING = [(-123.370, 48.420), (-123.360, 48.420), (-123.360, 48.430),
        (-123.370, 48.430), (-123.370, 48.420)]
RES_M = 30.0
URBAN_CLASS = 17     # NALCMS built-up; no water class, so water_union answers None
HSG_B_CODE = 2       # HYSOGs "B" -> representative texture loam (no texture raster)

_to_3979 = Transformer.from_crs("EPSG:4326", "EPSG:3979", always_xy=True).transform


def _aoi():
    return aoi_from_geojson(
        {"type": "Polygon", "coordinates": [[list(p) for p in RING]]})


def _write_raster(path, data, transform, dtype, nodata=None):
    with rasterio.open(
        path, "w", driver="GTiff", height=data.shape[0], width=data.shape[1],
        count=1, dtype=dtype, crs="EPSG:3979", transform=transform, nodata=nodata,
    ) as dst:
        dst.write(data, 1)


def _fixture_layers(tmp_path):
    """(dem, landcover, soil) over the RING bbox: flat-ish ramp DEM, all-urban
    landcover, all-B soils — the same tiny-raster form the derive tests use."""
    xs, ys = zip(*(_to_3979(lon, lat) for lon, lat in RING))
    left, bottom, right, top = min(xs), min(ys), max(xs), max(ys)
    width = max(8, int((right - left) // RES_M))
    height = max(8, int((top - bottom) // RES_M))
    transform = from_origin(left, top, RES_M, RES_M)

    landcover = np.full((height, width), URBAN_CLASS, dtype="uint8")
    hsg = np.full((height, width), HSG_B_CODE, dtype="uint8")
    ramp = left + (np.arange(width) + 0.5) * RES_M
    dem = np.tile((0.05 * (ramp - left)).astype("float32"), (height, 1))

    _write_raster(tmp_path / "landcover.tif", landcover, transform, "uint8")
    _write_raster(tmp_path / "hsg.tif", hsg, transform, "uint8", nodata=255)
    _write_raster(tmp_path / "dem.tif", dem, transform, "float32")

    dem_result = SimpleNamespace(path=tmp_path / "dem.tif", resolution_m=RES_M,
                                 source="fixture", coverage=1.0)
    landcover_result = LandcoverResult(
        raster_path=tmp_path / "landcover.tif", crs="EPSG:3979",
        legend=dict(DEFAULT_NALCMS_LEGEND), impervious=dict(DEFAULT_NALCMS_IMPERVIOUS))
    soil_result = SoilResult(hsg_raster=tmp_path / "hsg.tif", crs="EPSG:3979",
                             hsg_to_cn=dict(DEFAULT_HSG_TO_CN))
    return dem_result, landcover_result, soil_result


def _network():
    """Four junctions spread across the AOI, so the junction delineator has a tiling to
    hand out (a single node earns only a nominal, polygon-less subcatchment)."""
    return NetworkIn(
        junctions=[JunctionIn("J1", 9.0, -123.3675, 48.4225),
                   JunctionIn("J2", 8.5, -123.3625, 48.4225),
                   JunctionIn("J3", 8.0, -123.3675, 48.4275),
                   JunctionIn("J4", 7.5, -123.3625, 48.4275)],
        outfalls=[OutfallIn("OUT", 6.0, -123.361, 48.4295)],
        conduits=[ConduitIn("C1", "J1", "J2", 120.0),
                  ConduitIn("C2", "J3", "J4", 120.0),
                  ConduitIn("C3", "J2", "J4", 120.0),
                  ConduitIn("C4", "J4", "OUT", 120.0)])


def _vancouver_stub_spec():
    """A synthetic-fixture city spec KEYED "vancouver": the practice lookup sees the real
    registered row while the network/land stay hand-made."""
    net = _network()
    return CitySpec(
        key="vancouver", label="Vancouver fixture",
        coverage=(-123.37, 48.42, -123.36, 48.43), sub_crs="EPSG:32610",
        network_source="synthetic fixture",
        storm=lambda bbox, client: base.NetworkResult(network=net),
        land=lambda bbox, client: {})


def _captured_city_build(tmp_path, monkeypatch, **build_kw):
    """Run build_city offline up to the build spine and capture what reaches it."""
    dem, landcover, soil = _fixture_layers(tmp_path)
    captured = {}

    def fake_finish(ws, aoi, network, subcatchments, **kw):
        captured.update(kw)
        captured["network"] = network
        captured["subcatchments"] = subcatchments
        return "BUILT"

    monkeypatch.setattr(pipeline, "_finish_build", fake_finish)
    monkeypatch.setattr(pipeline, "fetch_street_graph",
                        lambda bbox: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr(pipeline, "acquire_dem", lambda *a, **k: dem)
    monkeypatch.setattr(pipeline, "acquire_landcover", lambda *a, **k: landcover)
    monkeypatch.setattr(pipeline, "_acquire_soil_auto", lambda *a, **k: soil)

    result = pipeline.build_city(
        _vancouver_stub_spec(), _aoi(), date(2020, 6, 1), date(2020, 6, 2),
        tmp_path / "ws", subcatchment_method="junction", **build_kw)
    assert result == "BUILT"
    assert captured["subcatchments"], "fixture build produced no subcatchments"
    return captured


LOAM_FC_IMD = 0.434 - 0.270      # theta_e - theta_fc for loam (Rawls tables)


class TestFollowedPracticeBuild:
    """follow_municipal_practice=True + the registered Vancouver row = the stated
    conventions generate the parameters (acceptance for tickets 08+09 wiring)."""

    def test_follow_switches_method_conventions_and_surface_set(self, tmp_path, monkeypatch):
        captured = _captured_city_build(tmp_path, monkeypatch, derive=True,
                                        follow_municipal_practice=True)
        sub = captured["subcatchments"][0]
        # GA conventions: stated-unhalved Ksat = table x2; IMD drained to field capacity.
        assert sub.ga_ksat_mm_h == pytest.approx(2 * 6.6)
        assert sub.ga_imd == pytest.approx(LOAM_FC_IMD)
        assert sub.ga_psi_mm == 88.9                       # psi has no stated convention
        # The registered surface set replaces the fleet SUBAREAS defaults.
        assert (sub.n_imperv, sub.n_perv, sub.s_imperv_mm, sub.s_perv_mm,
                sub.pct_zero) == (0.018, 0.41, 1.25, 2.5, 0.0)
        # The registered method wins the [OPTIONS] switch.
        assert captured["config"].infiltration is InfiltrationModel.GREEN_AMPT

    def test_the_inp_sections_reflect_the_followed_practice(self, tmp_path, monkeypatch):
        captured = _captured_city_build(tmp_path, monkeypatch, derive=True,
                                        follow_municipal_practice=True)
        rain = RainfallSeries([datetime(2020, 6, 1, h) for h in range(3)],
                              [1.0, 2.0, 0.0])
        res = build_model(network=captured["network"],
                          subcatchments=captured["subcatchments"], rain=rain,
                          config=captured["config"])
        text = res.inp_path.read_text()
        name = captured["subcatchments"][0].name

        assert re.search(r"(?m)^INFILTRATION\s+GREEN_AMPT", text)
        infil = text.split("[INFILTRATION]")[1].split("[")[0]
        row = next(l for l in infil.splitlines() if l.split() and l.split()[0] == name)
        psi, ksat, imd = (float(v) for v in row.split()[1:4])
        assert psi == pytest.approx(88.9)
        assert ksat == pytest.approx(13.2)          # source-table (unhalved) value
        assert imd == pytest.approx(LOAM_FC_IMD)    # field-capacity antecedent

        subareas = text.split("[SUBAREAS]")[1].split("[")[0]
        row = next(l for l in subareas.splitlines() if l.split() and l.split()[0] == name)
        assert [float(v) for v in row.split()[1:6]] == pytest.approx(
            [0.018, 0.41, 1.25, 2.5, 0.0])

    def test_provenance_lists_consumed_and_information_only(self, tmp_path, monkeypatch):
        captured = _captured_city_build(tmp_path, monkeypatch, derive=True,
                                        follow_municipal_practice=True)
        prov = captured["extra_provenance"]
        assert prov["ga_antecedent"] == "field_capacity"
        block = prov["municipal_practice"]
        assert block["follow_municipal_practice"] is True
        assert block["consumed"] == ["infiltration_method", "ga_ksat_halved",
                                     "ga_imd_antecedent", "surface_parameters",
                                     "dwf_pattern_structure"]
        assert block["information_only"] == ["modelling_platform",
                                             "design_imperviousness"]


class TestDefaultsUntouched:
    def test_not_following_keeps_every_fleet_default(self, tmp_path, monkeypatch):
        """A registered city WITHOUT follow builds exactly the fleet way — the
        pipeline-level face of the byte-identical guardrail."""
        captured = _captured_city_build(tmp_path, monkeypatch, derive=True)
        sub = captured["subcatchments"][0]
        assert sub.ga_ksat_mm_h == 6.6 and sub.ga_imd == 0.434
        assert (sub.n_imperv, sub.n_perv, sub.s_imperv_mm, sub.s_perv_mm,
                sub.pct_zero) == (0.01, 0.10, 1.5, 5.0, 25.0)
        assert captured["config"].infiltration is InfiltrationModel.HORTON
        assert captured["extra_provenance"]["ga_antecedent"] == "dry"
        assert captured["extra_provenance"]["municipal_practice"]["consumed"] == []


class TestDwfPatternStructureFollowsThePractice:
    """Ticket 10: the registered DWF pattern structure configures the loading layer's
    pattern group under follow; a build that does not follow keeps the single hourly
    default. The sanitary fetch is a fixture and service-area derivation is stubbed —
    the loading call and its stamps/diagnostics are the real thing under test."""

    def _sanitary_spec(self):
        san = NetworkIn(
            junctions=[JunctionIn("M1", 5.0, -123.3670, 48.4230, system="sanitary")],
            outfalls=[OutfallIn("WWTP", 4.0, -123.3665, 48.4240, system="sanitary")],
            conduits=[ConduitIn("SC1", "M1", "WWTP", 80.0, system="sanitary")])
        net = _network()
        return CitySpec(
            key="vancouver", label="Vancouver fixture",
            coverage=(-123.37, 48.42, -123.36, 48.43), sub_crs="EPSG:32610",
            network_source="synthetic fixture",
            storm=lambda bbox, client: base.NetworkResult(network=net),
            land=lambda bbox, client: {},
            sanitary=lambda bbox, client: base.NetworkResult(network=san))

    @pytest.mark.parametrize("follow,stamp,structure", [
        (True, "DWF_MONTHLY DWF_DIURNAL DWF_WEEKEND", ["monthly", "hourly", "weekend"]),
        (False, "DWF_DIURNAL", ["hourly"]),
    ])
    def test_the_loaded_areas_carry_the_structure(self, tmp_path, monkeypatch,
                                                  follow, stamp, structure):
        from swmmcanada import pipeline
        from swmmcanada.build.models import SewerServiceArea

        dem, landcover, soil = _fixture_layers(tmp_path)
        captured = {}

        def fake_finish(ws, aoi, network, subcatchments, **kw):
            captured.update(kw)
            return "BUILT"

        monkeypatch.setattr(pipeline, "_finish_build", fake_finish)
        monkeypatch.setattr(pipeline, "fetch_street_graph",
                            lambda bbox: (_ for _ in ()).throw(RuntimeError("offline")))
        monkeypatch.setattr(pipeline, "acquire_dem", lambda *a, **k: dem)
        monkeypatch.setattr(pipeline, "acquire_landcover", lambda *a, **k: landcover)
        monkeypatch.setattr(pipeline, "_acquire_soil_auto", lambda *a, **k: soil)
        monkeypatch.setattr(
            pipeline, "derive_service_areas",
            lambda *a, **k: ([SewerServiceArea("SSA_1", "SAN_M1", 2.0, population=100.0)],
                             {"seeded_on": "stub"}))

        result = pipeline.build_city(
            self._sanitary_spec(), _aoi(), date(2020, 6, 1), date(2020, 6, 2),
            tmp_path / "ws", subcatchment_method="junction", derive=True,
            follow_municipal_practice=follow)
        assert result == "BUILT"

        areas = captured["service_areas"]
        assert areas and all(a.dwf_pattern == stamp for a in areas)
        # The per-build record (provenance/ASSUMPTIONS): which structure THIS build used.
        san_block = captured["extra_provenance"]["sanitary"]
        assert san_block["service_areas"]["dwf_pattern_structure"] == structure


class TestGaAntecedentStandsAlone:
    """The §G4 option is its own knob: usable without follow, beaten by follow."""

    def test_field_capacity_without_follow_changes_imd_only(self, tmp_path, monkeypatch):
        captured = _captured_city_build(tmp_path, monkeypatch, derive=True,
                                        ga_antecedent="field_capacity")
        sub = captured["subcatchments"][0]
        assert sub.ga_imd == pytest.approx(LOAM_FC_IMD)
        assert sub.ga_ksat_mm_h == 6.6                 # no follow -> no Ksat convention
        assert sub.n_imperv == 0.01                    # fleet surface set stands
        assert captured["config"].infiltration is InfiltrationModel.HORTON
        assert captured["extra_provenance"]["ga_antecedent"] == "field_capacity"

    def test_follow_wins_the_stated_knobs(self, tmp_path, monkeypatch):
        """Follow means "build it the way this city models it": where the record states
        a convention it wins the knob. The frontend always posts an infiltration choice,
        so any weaker precedence would leave follow permanently dead through the UI."""
        captured = _captured_city_build(tmp_path, monkeypatch, derive=True,
                                        follow_municipal_practice=True,
                                        ga_antecedent="dry", infiltration="HORTON")
        assert captured["config"].infiltration is InfiltrationModel.GREEN_AMPT
        assert captured["extra_provenance"]["ga_antecedent"] == "field_capacity"


class TestSynthesisPathwayCarriesTheOption:
    @pytest.mark.parametrize("entry", ["build_city", "build_from_aoi"])
    def test_both_entry_points_accept_ga_antecedent(self, entry):
        import inspect

        assert "ga_antecedent" in inspect.signature(getattr(pipeline, entry)).parameters

    def test_the_synthesis_build_records_the_convention(self, tmp_path, monkeypatch):
        """build_from_aoi forwards the option into provenance (and the same local feeds
        its derive call); the acquisition/synthesis seams are stubbed, the delineation
        and sizing stages run for real."""
        dem, _landcover, _soil = _fixture_layers(tmp_path)
        net = _network()
        captured = {}

        def fake_finish(ws, aoi, network, subcatchments, **kw):
            captured.update(kw)
            return "BUILT"

        monkeypatch.setattr(pipeline, "_finish_build", fake_finish)
        monkeypatch.setattr(pipeline, "fetch_street_graph", lambda bbox: None)
        monkeypatch.setattr(pipeline, "sample_elevations", lambda streets, dem_path: None)
        monkeypatch.setattr(pipeline, "acquire_dem", lambda *a, **k: dem)
        monkeypatch.setattr(
            pipeline, "synthesise_network",
            lambda streets, aoi=None, water=None: SimpleNamespace(network=net,
                                                                  diagnostics={}))
        monkeypatch.setattr(pipeline, "_design_intensity_fn",
                            lambda aoi: ((lambda tc_min: 30.0), {"idf": "stub"}))
        import swmmcanada.sources.parcels_bc as parcels_bc

        monkeypatch.setattr(parcels_bc, "fetch_bc_parcels",
                            lambda bbox: ([], {"status": "stubbed offline"}))

        result = pipeline.build_from_aoi(
            _aoi(), date(2020, 6, 1), date(2020, 6, 2), tmp_path / "ws",
            derive=False, ga_antecedent="field_capacity")
        assert result == "BUILT"
        assert captured["extra_provenance"]["ga_antecedent"] == "field_capacity"

        result = pipeline.build_from_aoi(
            _aoi(), date(2020, 6, 1), date(2020, 6, 2), tmp_path / "ws2", derive=False)
        assert result == "BUILT"
        assert captured["extra_provenance"]["ga_antecedent"] == "dry"
