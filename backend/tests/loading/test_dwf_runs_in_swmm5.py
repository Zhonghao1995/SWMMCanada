"""The loaded sanitary model runs in the real EPA SWMM engine (#12 acceptance).

Everything else about dry-weather flow is checked against our own code. This checks it
against the thing that will actually be asked to solve it — the sections can be
syntactically fine and still be rejected, or accepted and silently mean something else
(a base flow in the wrong units balances perfectly and is 1000x wrong).

Guarded on `swmm5` being on PATH, matching the other engine smokes, so CI skips it.
"""
import re
import shutil
import subprocess
from datetime import date, datetime, timedelta

import pytest

from swmmcanada.build import BuildConfig
from swmmcanada.build.assemble import build_model
from swmmcanada.build.models import (ConduitIn, JunctionIn, NetworkIn, OutfallIn,
                                     RainfallSeries, SewerServiceArea, SurfaceCatchment)
from swmmcanada.loading import load_service_areas

pytestmark = pytest.mark.skipif(shutil.which("swmm5") is None,
                                reason="EPA SWMM (swmm5) not on PATH")

RING = [(-123.370, 48.420), (-123.360, 48.420), (-123.360, 48.430), (-123.370, 48.430),
        (-123.370, 48.420)]


def _three_system_model(tmp_path, pattern_structure=None):
    """Storm with a subcatchment, sanitary with service areas, wired as a build produces
    them: separate namespaces, each reaching its own destination."""
    net = NetworkIn(
        junctions=[JunctionIn("J1", 8.0, -123.365, 48.425, max_depth_m=3.0),
                   JunctionIn("SAN_M1", 6.0, -123.366, 48.424, max_depth_m=3.0,
                              system="sanitary"),
                   JunctionIn("SAN_M2", 5.5, -123.367, 48.423, max_depth_m=3.0,
                              system="sanitary")],
        outfalls=[OutfallIn("OUT", 6.5, -123.361, 48.428),
                  OutfallIn("SAN_WWTP", 5.0, -123.368, 48.422, system="sanitary",
                            synthesised=True)],
        conduits=[ConduitIn("C1", "J1", "OUT", 120.0, diameter_m=0.6),
                  ConduitIn("SC1", "SAN_M1", "SAN_M2", 90.0, diameter_m=0.3,
                            system="sanitary"),
                  ConduitIn("SC2", "SAN_M2", "SAN_WWTP", 90.0, diameter_m=0.3,
                            system="sanitary")])
    t0 = datetime(2024, 6, 1)
    hours = 24
    rain = RainfallSeries([t0 + timedelta(hours=i) for i in range(hours)],
                          [0.0] * 6 + [4.0] * 3 + [0.0] * (hours - 9))
    areas = load_service_areas([
        SewerServiceArea("SSA_1", "SAN_M1", 3.0, polygon=RING, dwelling_units=140),
        SewerServiceArea("SSA_2", "SAN_M2", 2.0, polygon=RING, population=260.0)],
        pattern_structure=pattern_structure)
    cfg = BuildConfig(out_dir=tmp_path, start=date(2024, 6, 1), end=date(2024, 6, 2))
    result = build_model(
        network=net,
        subcatchments=[SurfaceCatchment("S1", "J1", 2.0, 55.0, 140.0, 1.2, polygon=RING)],
        rain=rain, config=cfg, service_areas=areas.areas)
    return result, areas


def _run(inp_path, tmp_path):
    rpt = tmp_path / "run.rpt"
    proc = subprocess.run(["swmm5", str(inp_path), str(rpt),
                           str(inp_path.with_suffix(".out"))],
                          capture_output=True, text=True, timeout=180)
    return proc, (rpt.read_text(errors="ignore") if rpt.exists() else "")


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("dwf_engine")
    result, areas = _three_system_model(tmp)
    proc, report = _run(result.inp_path, tmp)
    return proc, report, areas


def test_the_engine_accepts_the_model(run):
    proc, report, _ = run
    assert proc.returncode == 0, proc.stdout[-2000:]
    # SWMM numbers its faults ("ERROR 211: ..."). A bare search for the word matches the
    # "Continuity Error (%)" label, which is a normal line in a healthy report.
    faults = re.findall(r"^\s*ERROR\s+\d+.*$", report, re.M)
    assert not faults, faults[:5]

    warnings = re.findall(r"^\s*WARNING\s+\d+.*$", report, re.M)
    assert not warnings, warnings[:5]


def test_the_dry_weather_inflow_actually_enters_the_model(run):
    """The section can be syntactically valid and contribute nothing. The engine's own
    continuity table is the check that it did not."""
    _, report, _ = run
    m = re.search(r"Dry Weather Inflow\s*\.+\s*([\d.]+)\s+([\d.]+)", report)
    assert m, "no Dry Weather Inflow line in the continuity table"
    assert float(m.group(2)) > 0, "dry weather inflow reached the model as zero"


def test_the_volume_matches_what_we_intended(run):
    """A base flow in the wrong units balances perfectly and is 1000x wrong. The engine
    reports mega-litres; our own total is litres per second over the run."""
    _, report, areas = run
    m = re.search(r"Dry Weather Inflow\s*\.+\s*([\d.]+)\s+([\d.]+)", report)
    reported_ml = float(m.group(2))
    expected_ml = areas.diagnostics["total_dwf_lps"] * 86400 / 1e6  # L/s -> ML/day
    assert reported_ml == pytest.approx(expected_ml, rel=0.05), (
        f"engine saw {reported_ml} ML, we intended {expected_ml} ML")


def test_continuity_error_is_sane(run):
    _, report, _ = run
    errors = [float(x) for x in re.findall(r"Continuity Error \(%\)\s*\.+\s*(-?[\d.]+)",
                                           report)]
    assert errors, "no continuity error reported"
    assert all(abs(e) < 5.0 for e in errors), errors


# --- the three-pattern structure through the real engine (ticket 10) -----------------

@pytest.fixture(scope="module")
def run_three_patterns(tmp_path_factory):
    """The same model with the monthly + hourly + weekend group. The run starts on a
    Saturday (2024-06-01), so the WEEKEND slot actually drives the first day."""
    tmp = tmp_path_factory.mktemp("dwf_engine_three")
    result, areas = _three_system_model(tmp, pattern_structure=("monthly", "hourly",
                                                                "weekend"))
    proc, report = _run(result.inp_path, tmp)
    return proc, report, areas


def test_the_engine_accepts_the_three_pattern_model(run_three_patterns):
    proc, report, _ = run_three_patterns
    assert proc.returncode == 0, proc.stdout[-2000:]
    faults = re.findall(r"^\s*ERROR\s+\d+.*$", report, re.M)
    assert not faults, faults[:5]
    warnings = re.findall(r"^\s*WARNING\s+\d+.*$", report, re.M)
    assert not warnings, warnings[:5]


def test_three_patterns_leave_the_volume_and_continuity_where_they_were(run_three_patterns):
    """The structure-first placeholders (weekend = weekday shape, monthly = 1.0) must not
    scale the volume the loading layer intended, and the continuity error must stay in
    the same magnitude band as the single-pattern run (<5%)."""
    _, report, areas = run_three_patterns
    m = re.search(r"Dry Weather Inflow\s*\.+\s*([\d.]+)\s+([\d.]+)", report)
    assert m, "no Dry Weather Inflow line in the continuity table"
    reported_ml = float(m.group(2))
    expected_ml = areas.diagnostics["total_dwf_lps"] * 86400 / 1e6
    assert reported_ml == pytest.approx(expected_ml, rel=0.05), (
        f"engine saw {reported_ml} ML, we intended {expected_ml} ML")
    errors = [float(x) for x in re.findall(r"Continuity Error \(%\)\s*\.+\s*(-?[\d.]+)",
                                           report)]
    assert errors and all(abs(e) < 5.0 for e in errors), errors
