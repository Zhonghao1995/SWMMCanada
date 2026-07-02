"""Rational-method pipe sizing (#56): accumulation, Manning, ladder, no-shrink — all
against an injected intensity function (IDF integration is tested in its own module)."""
import pytest

from swmmcanada.build.models import ConduitIn, JunctionIn, NetworkIn, OutfallIn, SubcatchmentIn
from swmmcanada.network.sizing import COMMERCIAL_DIAMETERS_M, SizingConfig, size_conduits


def _net():
    """A: two branches (J1, J2) meet at J3 → outfall. Falling inverts, 100 m pipes."""
    return NetworkIn(
        junctions=[JunctionIn("J1", 100.0, 0.0, 0.0), JunctionIn("J2", 100.0, 1.0, 0.0),
                   JunctionIn("J3", 99.0, 0.5, 0.5)],
        outfalls=[OutfallIn("O", 98.0, 0.5, 1.0)],
        conduits=[ConduitIn("C1", "J1", "J3", 100.0), ConduitIn("C2", "J2", "J3", 100.0),
                  ConduitIn("C3", "J3", "O", 100.0)],
    )


def _subs(area_ha=2.0):
    return [SubcatchmentIn("S1", "J1", area_ha, 50.0, 100.0, 1.0),
            SubcatchmentIn("S2", "J2", area_ha, 50.0, 100.0, 1.0),
            SubcatchmentIn("S3", "J3", area_ha, 50.0, 100.0, 1.0)]


FLAT_30 = lambda tc: 30.0   # constant 30 mm/h regardless of tc


def test_downstream_pipe_carries_accumulated_flow():
    net, diag = size_conduits(_net(), _subs(), FLAT_30)
    d = {c.name: c.diameter_m for c in net.conduits}
    assert d["C3"] >= d["C1"] and d["C3"] >= d["C2"]   # trunk ≥ branches
    assert d["C3"] > COMMERCIAL_DIAMETERS_M[0]          # 6 ha at 30 mm/h needs > 300 mm
    assert diag["n_conduits_sized"] == 3


def test_diameters_come_from_the_commercial_ladder():
    net, _ = size_conduits(_net(), _subs(), FLAT_30)
    for c in net.conduits:
        assert c.diameter_m in COMMERCIAL_DIAMETERS_M


def test_no_downstream_shrinkage_even_when_flow_says_smaller():
    """A steep trunk could compute smaller than its flat branch — the ladder walk must
    forbid shrinking."""
    net = NetworkIn(
        junctions=[JunctionIn("J1", 100.0, 0.0, 0.0), JunctionIn("J2", 99.9, 0.0, 1.0)],
        outfalls=[OutfallIn("O", 90.0, 0.0, 2.0)],     # huge drop on the trunk
        conduits=[ConduitIn("C1", "J1", "J2", 100.0), ConduitIn("C2", "J2", "O", 100.0)],
    )
    subs = [SubcatchmentIn("S1", "J1", 5.0, 80.0, 100.0, 1.0)]
    sized, _ = size_conduits(net, subs, FLAT_30)
    d = {c.name: c.diameter_m for c in sized.conduits}
    assert d["C2"] >= d["C1"]


def test_higher_intensity_bigger_pipes():
    d30 = {c.name: c.diameter_m for c in size_conduits(_net(), _subs(), FLAT_30)[0].conduits}
    d90 = {c.name: c.diameter_m for c in size_conduits(_net(), _subs(), lambda tc: 90.0)[0].conduits}
    assert d90["C3"] > d30["C3"]


def test_tc_grows_downstream_and_reaches_intensity_fn():
    seen = []

    def probe(tc):
        seen.append(tc)
        return 30.0

    size_conduits(_net(), _subs(), probe, SizingConfig(inlet_time_min=10.0, travel_velocity_ms=1.0))
    assert min(seen) >= 10.0                            # inlet-time floor
    assert max(seen) > min(seen)                        # trunk tc > branch tc


def test_dry_pipe_gets_ladder_floor():
    net = NetworkIn(
        junctions=[JunctionIn("J1", 100.0, 0.0, 0.0)],
        outfalls=[OutfallIn("O", 99.0, 0.0, 1.0)],
        conduits=[ConduitIn("C1", "J1", "O", 100.0)],
    )
    sized, _ = size_conduits(net, [], FLAT_30)          # no subcatchments at all
    assert sized.conduits[0].diameter_m == COMMERCIAL_DIAMETERS_M[0]


def test_original_network_object_is_not_mutated():
    net = _net()
    before = [c.diameter_m for c in net.conduits]
    size_conduits(net, _subs(), FLAT_30)
    assert [c.diameter_m for c in net.conduits] == before


def test_pipeline_intensity_fn_degrades_to_constant(monkeypatch):
    """IDF unreachable → the documented 30 mm/h constant with a provenance note (#56)."""
    import swmmcanada.sources.idf_eccc as idf
    from swmmcanada.pipeline import _design_intensity_fn

    def boom(lat, lon, **kw):
        raise idf.IdfUnavailableError("server down")

    monkeypatch.setattr(idf, "nearest_idf_station", boom)

    class AOI:
        bbox = (-75.70, 45.41, -75.68, 45.42)

    fn, diag = _design_intensity_fn(AOI())
    assert fn(10) == 30.0
    assert diag["intensity_source"] == "fallback-constant"
    assert diag["reason"] == "idf_unavailable"


def test_pipeline_intensity_fn_uses_fixture_station(monkeypatch, tmp_path):
    """With the recorded Ottawa fixture pre-seeded as the station cache, the pipeline
    helper returns a real IDF-backed curve and names the station in its diagnostics."""
    import shutil
    from pathlib import Path

    import swmmcanada.sources.idf_eccc as idf
    from swmmcanada.pipeline import _design_intensity_fn

    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "idf"
    txt = next(fixture.glob("*6106000*"))
    station = idf.nearest_idf_station(45.41, -75.69)          # Ottawa-ish → 610x station
    shutil.copy(txt, tmp_path / f"{station.station_id}.txt")  # seed the cache: no network

    real_fetch = idf.fetch_idf_table
    monkeypatch.setattr(idf, "fetch_idf_table",
                        lambda st, **kw: real_fetch(st, cache_dir=tmp_path))

    fn, diag = _design_intensity_fn(type("A", (), {"bbox": (-75.70, 45.41, -75.68, 45.42)})())
    assert diag["intensity_source"].startswith("eccc-idf:")
    assert 20.0 < fn(10) < 200.0                              # physically sensible mm/h
