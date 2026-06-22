"""TDD for build: a tiny hand-made model assembles into a runnable, round-trippable
.inp (spec 09; integration-spec build-order step 2 — freeze the .inp contract early)."""
from datetime import date, datetime

import pytest

from swmmcanada.build import (
    BuildConfig,
    ConduitIn,
    JunctionIn,
    NetworkIn,
    OutfallIn,
    RainfallSeries,
    SubcatchmentIn,
    build_model,
)


def _tiny_network():
    return NetworkIn(
        junctions=[
            JunctionIn("J1", invert_m=99.0, x=0.0, y=0.0),
            JunctionIn("J2", invert_m=98.5, x=100.0, y=0.0),
        ],
        outfalls=[OutfallIn("O1", invert_m=98.0, x=200.0, y=0.0)],
        conduits=[
            ConduitIn("C1", "J1", "J2", length_m=100.0),
            ConduitIn("C2", "J2", "O1", length_m=100.0),
        ],
    )


def _tiny_subs():
    return [
        SubcatchmentIn("S1", outlet_node="J1", area_ha=1.0, pct_imperv=40.0, width_m=100.0, pct_slope=1.0)
    ]


def _tiny_rain():
    ts = [datetime(2020, 6, 1, h) for h in range(3)]
    return RainfallSeries(timestamps=ts, precip_mm=[1.2, 3.4, 0.0])


def _config(tmp_path):
    return BuildConfig(out_dir=tmp_path, start=date(2020, 6, 1), end=date(2020, 6, 2))


def test_build_produces_roundtrippable_inp(tmp_path):
    res = build_model(
        network=_tiny_network(), subcatchments=_tiny_subs(), rain=_tiny_rain(), config=_config(tmp_path)
    )
    assert res.inp_path.exists()
    assert res.manifest_path.exists()
    # validate_inp already round-tripped through swmm-api + swmmio (no raise == success).
    for sec in ("SUBCATCHMENTS", "JUNCTIONS", "OUTFALLS", "CONDUITS", "RAINGAGES", "TIMESERIES"):
        assert sec in res.sections_written


def test_referential_integrity(tmp_path):
    """Re-read the .inp and assert every cross-reference resolves."""
    from swmm_api import read_inp_file
    from swmm_api.input_file import SEC

    res = build_model(
        network=_tiny_network(), subcatchments=_tiny_subs(), rain=_tiny_rain(), config=_config(tmp_path)
    )
    inp = read_inp_file(str(res.inp_path))
    nodes = set(inp[SEC.JUNCTIONS].keys()) | set(inp[SEC.OUTFALLS].keys())
    for c in inp[SEC.CONDUITS].values():
        assert c.from_node in nodes and c.to_node in nodes
    for s in inp[SEC.SUBCATCHMENTS].values():
        assert s.outlet in nodes                       # subcatchment drains to a real node
        assert s.rain_gage in inp[SEC.RAINGAGES].keys()  # raingage exists
    for g in inp[SEC.RAINGAGES].values():
        assert g.timeseries in inp[SEC.TIMESERIES].keys()  # raingage series exists


def test_oversized_is_not_this_modules_concern(tmp_path):
    """build trusts upstream; it does not re-validate AOI size. Smoke: a 2nd build in a
    fresh dir is independent and deterministic in structure."""
    a = build_model(network=_tiny_network(), subcatchments=_tiny_subs(), rain=_tiny_rain(), config=_config(tmp_path / "a"))
    b = build_model(network=_tiny_network(), subcatchments=_tiny_subs(), rain=_tiny_rain(), config=_config(tmp_path / "b"))
    assert a.sections_written == b.sections_written
