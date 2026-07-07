"""Design-storm fallback forcing (ADR 0015): an alternating-block hyetograph built
directly from an ECCC IDF table — the third rainfall tier behind hourly and daily gauges.

Construction (textbook alternating block, no curve fitting): log-log interpolate the IDF
depths to every whole hour 1..duration_h, difference into incremental blocks, then arrange
the blocks with the peak at the centre and the remainder alternating right/left. Honest
naming: this is NOT a Chicago/Keifer-Chu storm (that requires a fitted i = a/(t+b)^c
curve); the alternating block uses the published table values as-is.

Single-event synthetic rain: fine for structural/design checks, not continuous hydrology —
the forcing record and validation report carry that label (ADR 0015 §5).
"""
import math
from datetime import date, datetime, timedelta
from typing import Dict, List, Tuple

from swmmcanada.build.models import RainfallSeries

DEFAULT_RETURN_PERIOD_YR = 5      # matches rational-method pipe sizing (#56)
DEFAULT_DURATION_H = 24
DEFAULT_TIMESTEP_MIN = 60         # same resolution as the hourly gauge tier (ADR 0014)


def _depth_curve(intensities_mm_h: Dict[int, float]) -> List[Tuple[float, float]]:
    """(duration_min, depth_mm) points from an IDF row, sorted, made monotone non-decreasing
    (a published table can wobble within rounding; depth must not decrease with duration)."""
    pts = sorted((float(d), float(i) * float(d) / 60.0)
                 for d, i in intensities_mm_h.items() if i and i > 0)
    mono: List[Tuple[float, float]] = []
    for d, depth in pts:
        if mono and depth < mono[-1][1]:
            depth = mono[-1][1]
        mono.append((d, depth))
    return mono


def _depth_at(duration_min: float, curve: List[Tuple[float, float]]) -> float:
    """Depth (mm) at an arbitrary duration by log-log linear interpolation; clamped to the
    curve's ends (no extrapolation beyond the published durations)."""
    if duration_min <= curve[0][0]:
        return curve[0][1]
    if duration_min >= curve[-1][0]:
        return curve[-1][1]
    for (d0, v0), (d1, v1) in zip(curve, curve[1:]):
        if d0 <= duration_min <= d1:
            if v0 <= 0:                      # degenerate leading zeros: fall back to linear
                return v0 + (v1 - v0) * (duration_min - d0) / (d1 - d0)
            f = (math.log(duration_min) - math.log(d0)) / (math.log(d1) - math.log(d0))
            return math.exp(math.log(v0) + f * (math.log(v1) - math.log(v0)))
    return curve[-1][1]


def alternating_block_series(
    idf_table, start: date, *,
    return_period: int = DEFAULT_RETURN_PERIOD_YR,
    duration_h: int = DEFAULT_DURATION_H,
    gage_name: str = "RG1", ts_name: str = "rain",
) -> RainfallSeries:
    """Alternating-block design storm as an hourly RainfallSeries starting ``start`` 00:00.
    Total depth equals the IDF table's ``duration_h`` depth at ``return_period``."""
    row = idf_table.intensities_mm_h.get(return_period)
    if not row:
        raise ValueError(f"IDF table has no return period {return_period} yr "
                         f"(has: {sorted(idf_table.intensities_mm_h)})")
    curve = _depth_curve(row)
    if len(curve) < 2:
        raise ValueError("IDF table too sparse to build a design storm")

    depths = [_depth_at(h * 60.0, curve) for h in range(1, duration_h + 1)]
    blocks = [depths[0]] + [max(0.0, b - a) for a, b in zip(depths, depths[1:])]

    # Peak block at the centre, remainder alternating right, then left.
    blocks.sort(reverse=True)
    ordered = [0.0] * duration_h
    centre = duration_h // 2
    ordered[centre] = blocks[0]
    right, left, place_right = centre + 1, centre - 1, True
    for b in blocks[1:]:
        if place_right and right < duration_h:
            ordered[right] = b
            right += 1
        elif left >= 0:
            ordered[left] = b
            left -= 1
        else:
            ordered[right] = b
            right += 1
        place_right = not place_right

    t0 = datetime(start.year, start.month, start.day)
    timestamps = [t0 + timedelta(hours=h) for h in range(duration_h)]
    return RainfallSeries(timestamps=timestamps, precip_mm=ordered,
                          gage_name=gage_name, ts_name=ts_name)
