"""ADR 0014 — the forcing-consistency check: Warning tier, mirrors the delineation
record's ride-along into validation.json."""
from swmmcanada.validate import checks as C


def test_daily_tier_passes():
    r = C.check_forcing_consistency({"rainfall_resolution": "daily", "fallback_reason": "x"})
    assert r.passed and r.severity == "warning"


def test_hourly_within_tolerance_passes():
    r = C.check_forcing_consistency({"rainfall_resolution": "hourly", "mismatch_pct": 3.2})
    assert r.passed and r.metrics["mismatch_pct"] == 3.2


def test_hourly_mismatch_fails_as_warning():
    r = C.check_forcing_consistency(
        {"rainfall_resolution": "hourly", "mismatch_pct": 40.0,
         "mismatch_warning": "hourly rain total differs"})
    assert not r.passed and r.severity == "warning"
