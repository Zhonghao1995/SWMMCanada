"""Service areas round-trip through the datastore (ADR 0007 parity, ADR 0029/0031).

The datastore is the primary build path: the `.inp` is produced *from* it, not alongside it.
So a field that does not survive the round trip does not reach the model, however carefully
it was computed upstream. ADR 0011 made this explicit for the `system` tag; the same
invariant now covers service areas.
"""
from datetime import date, datetime, timedelta

import pytest

from swmmcanada.build import BuildConfig
from swmmcanada.build.models import (ConduitIn, JunctionIn, NetworkIn, OutfallIn,
                                     RainfallSeries, SewerServiceArea, SurfaceCatchment)
from swmmcanada.datastore import build_from_datastore, read_datastore, write_datastore
from swmmcanada.loading import load_service_areas

RING = [(-123.37, 48.42), (-123.36, 48.42), (-123.36, 48.43), (-123.37, 48.43),
        (-123.37, 48.42)]


def _network():
    return NetworkIn(
        junctions=[JunctionIn("J1", 10.0, -123.365, 48.425),
                   JunctionIn("SAN_M1", 9.0, -123.366, 48.424, system="sanitary")],
        outfalls=[OutfallIn("O1", 8.0, -123.361, 48.428),
                  OutfallIn("SAN_WWTP", 7.0, -123.362, 48.429, system="sanitary")],
        conduits=[ConduitIn("C1", "J1", "O1", 50.0),
                  ConduitIn("SC1", "SAN_M1", "SAN_WWTP", 60.0, system="sanitary")])


def _write(tmp_path, service_areas):
    t0 = datetime(2024, 6, 1)
    rain = RainfallSeries([t0 + timedelta(hours=i) for i in range(6)], [0.0] * 6)
    cfg = BuildConfig(out_dir=tmp_path / "build", start=date(2024, 6, 1), end=date(2024, 6, 2))
    subs = [SurfaceCatchment("S1", "J1", 1.0, 50.0, 100.0, 1.0, polygon=RING)]
    write_datastore(tmp_path / "ds", network=_network(), subcatchments=subs, rain=rain,
                    config=cfg, service_areas=service_areas)
    return tmp_path / "ds"


AREA = SewerServiceArea(
    name="SSA_1", node="SAN_M1", area_ha=2.5, system="sanitary", polygon=RING,
    population=240.0, dwelling_units=95, dwf_lps=0.7778, dwf_pattern="DWF_DIURNAL",
    geometry_source="official", loading_source="calibrated")


class TestRoundTrip:
    def test_every_field_survives(self, tmp_path):
        back = read_datastore(_write(tmp_path, [AREA])).service_areas
        assert len(back) == 1
        got = back[0]
        for f in ("name", "node", "system", "population", "dwelling_units", "dwf_lps",
                  "dwf_pattern", "geometry_source", "loading_source"):
            assert getattr(got, f) == getattr(AREA, f), f
        assert got.area_ha == pytest.approx(AREA.area_ha)

    def test_the_two_provenance_dimensions_stay_independent(self):
        """An official boundary must not arrive with its loading upgraded to match."""
        assert AREA.geometry_source == "official" and AREA.loading_source == "calibrated"

    def test_geometry_survives(self, tmp_path):
        back = read_datastore(_write(tmp_path, [AREA])).service_areas[0]
        assert back.polygon and len(back.polygon) >= 4

    def test_unloaded_areas_round_trip_with_null_flow(self, tmp_path):
        bare = SewerServiceArea(name="SSA_2", node="SAN_M1", area_ha=1.0, polygon=RING)
        back = read_datastore(_write(tmp_path, [bare])).service_areas[0]
        assert back.dwf_lps is None and back.population is None
        assert back.loading_source == "synthetic"


class TestBackwardCompatibility:
    def test_a_datastore_without_the_layer_still_reads(self, tmp_path):
        """Every datastore written before ADR 0031 lacks the layer; absence is normal."""
        ds = read_datastore(_write(tmp_path, []))
        assert ds.service_areas == []
        assert ds.subcatchments, "the rest of the datastore is unaffected"


class TestReachesTheModel:
    def test_dwf_appears_in_the_inp_built_from_the_datastore(self, tmp_path):
        """The whole point of the parity invariant: what survives the datastore is what
        reaches the model."""
        loaded = load_service_areas([SewerServiceArea(
            name="SSA_1", node="SAN_M1", area_ha=2.0, polygon=RING, population=200.0)])
        path = _write(tmp_path, loaded.areas)
        result = build_from_datastore(path, tmp_path / "out")
        txt = (tmp_path / "out" / result.inp_path.name).read_text()
        assert "[DWF]" in txt and "SAN_M1" in txt

    def test_a_storm_only_datastore_gets_no_dwf_section(self, tmp_path):
        path = _write(tmp_path, [])
        result = build_from_datastore(path, tmp_path / "out")
        assert "[DWF]" not in (tmp_path / "out" / result.inp_path.name).read_text()
