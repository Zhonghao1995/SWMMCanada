"""ADR 0015 — alternating-block design storm from an IDF table: total depth preserved,
peak centred, flanks decay, honest failures."""
from datetime import date

import pytest

from swmmcanada.acquire.design_storm import alternating_block_series, _depth_at, _depth_curve
from swmmcanada.sources.idf_eccc import IdfTable

# A small but realistic T=5 row (mm/h at duration minutes) — depths grow with duration.
TABLE = IdfTable(station_id="X", intensities_mm_h={
    5: {5: 120.0, 10: 90.0, 15: 75.0, 30: 50.0, 60: 32.0, 120: 20.0,
        360: 8.5, 720: 5.0, 1440: 2.9},
    10: {60: 40.0, 1440: 3.5},
}, coefficients={})


def test_total_depth_equals_24h_idf_depth():
    s = alternating_block_series(TABLE, date(2022, 6, 1))
    assert len(s.timestamps) == 24
    assert s.timestamps[0].hour == 0 and s.timestamps[-1].hour == 23
    assert sum(s.precip_mm) == pytest.approx(2.9 * 1440 / 60, rel=1e-6)   # 69.6 mm


def test_peak_centred_and_flanks_decay():
    s = alternating_block_series(TABLE, date(2022, 6, 1))
    peak_h = s.precip_mm.index(max(s.precip_mm))
    assert peak_h == 12                                     # centre of a 24 h storm
    # moving away from the peak, blocks never increase
    right = s.precip_mm[peak_h:]
    left = s.precip_mm[:peak_h + 1][::-1]
    assert all(a >= b for a, b in zip(right, right[1:]))
    assert all(a >= b for a, b in zip(left, left[1:]))
    # peak block is the 1 h depth
    assert max(s.precip_mm) == pytest.approx(32.0, rel=1e-6)


def test_loglog_interpolation_monotone():
    curve = _depth_curve(TABLE.intensities_mm_h[5])
    d3h = _depth_at(180.0, curve)
    d2h, d6h = 20.0 * 2, 8.5 * 6
    assert d2h < d3h < d6h


def test_missing_return_period_raises():
    with pytest.raises(ValueError, match="no return period 25"):
        alternating_block_series(TABLE, date(2022, 6, 1), return_period=25)


def test_pipeline_fallback_builds_series_and_forcing(monkeypatch):
    from swmmcanada import pipeline as P
    from swmmcanada.sources import idf_eccc

    class _St:
        station_id = "ONT-X"
        name = "FAKE IDF"

    monkeypatch.setattr(idf_eccc, "nearest_idf_station", lambda lat, lon: _St())
    monkeypatch.setattr(idf_eccc, "fetch_idf_table", lambda st: TABLE)

    class _Aoi:
        bbox = (-86.0, 50.0, -85.9, 50.1)

    rain, forcing = P._design_storm_event(_Aoi(), date(2022, 6, 1))
    assert len(rain.precip_mm) == 24 and sum(rain.precip_mm) > 0
    assert forcing["rainfall_resolution"] == "design_storm"
    assert forcing["idf_station"] == "ONT-X" and forcing["return_period_yr"] == 5
    assert "not for continuous hydrology" in forcing["fallback_reason"]
    assert "requested" not in forcing                       # fallback ≠ user choice (ADR 0018)


def test_pipeline_user_choice_builds_requested_event(monkeypatch):
    """ADR 0018: a DesignStormChoice routes the SAME machinery but records the user's
    T × duration and a `requested` label instead of a fallback_reason."""
    from swmmcanada import pipeline as P
    from swmmcanada.acquire.design_storm import DesignStormChoice
    from swmmcanada.sources import idf_eccc

    class _St:
        station_id = "ONT-X"
        name = "FAKE IDF"

    monkeypatch.setattr(idf_eccc, "nearest_idf_station", lambda lat, lon: _St())
    monkeypatch.setattr(idf_eccc, "fetch_idf_table", lambda st: TABLE)

    class _Aoi:
        bbox = (-86.0, 50.0, -85.9, 50.1)

    choice = DesignStormChoice(return_period_yr=10, duration_h=6)
    rain, forcing = P._design_storm_event(_Aoi(), date(2022, 6, 1), choice=choice)
    assert len(rain.precip_mm) == 6                         # the chosen duration, not 24
    assert forcing["return_period_yr"] == 10 and forcing["duration_h"] == 6
    assert forcing["requested"] is True and "fallback_reason" not in forcing
    assert "not for continuous hydrology" in forcing["note"]


def test_pipeline_fallback_honest_failure_when_idf_unreachable(monkeypatch):
    from swmmcanada import pipeline as P
    from swmmcanada.sources import idf_eccc

    def _boom(lat, lon):
        raise ConnectionError("idf down")

    monkeypatch.setattr(idf_eccc, "nearest_idf_station", _boom)

    class _Aoi:
        bbox = (-86.0, 50.0, -85.9, 50.1)

    with pytest.raises(RuntimeError, match="design-storm fallback is unreachable"):
        P._design_storm_event(_Aoi(), date(2022, 6, 1))
