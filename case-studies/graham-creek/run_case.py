"""Graham Creek case study: build, run and evaluate the uncalibrated Ottawa model
against WSC gauge 02KF015 (see README.md).

Usage:
    ../../backend/.venv/bin/python run_case.py --hydat /path/to/Hydat.sqlite3

Needs the backend venv (Python 3.11) and EPA SWMM (`swmm5`) on PATH. The build takes a
few minutes; the 122-day dynamic-wave run takes roughly 1-2 hours and is cached.
"""
import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "backend"))

STATION = "02KF015"
START, END = date(2024, 6, 1), date(2024, 9, 30)


def build(run_dir: Path):
    from swmmcanada import pipeline
    from swmmcanada.geo.aoi import aoi_from_geojson

    aoi = aoi_from_geojson(json.loads((HERE / "basin_02KF015.geojson").read_text()))
    print(f"AOI: official WSC basin polygon, {aoi.area_km2:.2f} km2", flush=True)
    res = pipeline.build_city(
        "ottawa", aoi, START, END, run_dir,
        report=lambda stage, pct: print(f"[{pct:3d}%] {stage}", flush=True))
    print("built:", res.inp_path, f"({len(res.warnings)} warnings)")
    return res.inp_path


def simulated_daily(inp: Path):
    import pandas as pd
    from swmm_api import SwmmOutput

    out, rpt = inp.with_suffix(".case.out"), inp.with_suffix(".case.rpt")
    if not out.exists():
        print("running swmm5 (about 1-2 h for 122 days) ...", flush=True)
        proc = subprocess.run(["swmm5", str(inp), str(rpt), str(out)],
                              capture_output=True, text=True, timeout=4 * 3600)
        if proc.returncode not in (0, 1):
            raise SystemExit(f"swmm5 failed: {proc.stderr[-400:]}")
    sim = SwmmOutput(str(out)).to_frame()[("system", "", "outflow")]
    return sim.resample("D").mean()


def observed_daily(hydat: Path):
    import pandas as pd

    conn = sqlite3.connect(str(hydat))
    rows = {}
    months = [(y, m) for y in {START.year, END.year}
              for m in range(1, 13)
              if date(y, m, 1) >= date(START.year, START.month, 1)
              and date(y, m, 1) <= date(END.year, END.month, 1)]
    for y, m in months:
        cur = conn.execute(
            "SELECT * FROM DLY_FLOWS WHERE STATION_NUMBER=? AND YEAR=? AND MONTH=?",
            (STATION, y, m))
        cols = [d[0] for d in cur.description]
        for r in cur.fetchall():
            rec = dict(zip(cols, r))
            for day in range(1, 32):
                v = rec.get(f"FLOW{day}")
                if v is not None:
                    try:
                        rows[pd.Timestamp(y, m, day)] = float(v)
                    except ValueError:
                        pass
    conn.close()
    return pd.Series(rows).sort_index().loc[str(START):str(END)]


def main():
    import numpy as np
    import pandas as pd

    ap = argparse.ArgumentParser()
    ap.add_argument("--hydat", required=True, type=Path, help="path to Hydat.sqlite3")
    args = ap.parse_args()

    run_dir = HERE / "run"
    inp = run_dir / "model.inp"
    if not inp.exists():
        inp = build(run_dir)
    sim = simulated_daily(inp).rename("sim_cms")
    obs = observed_daily(args.hydat).rename("obs_cms")
    j = pd.concat([obs, sim], axis=1).dropna()
    o, s = j["obs_cms"].values, j["sim_cms"].values
    nse = 1.0 - float(np.sum((s - o) ** 2) / np.sum((o - o.mean()) ** 2))
    pbias = float(100.0 * np.sum(s - o) / np.sum(o))
    print(f"\nn_days={len(j)}  NSE={nse:.3f}  PBIAS={pbias:+.1f}%")
    print(f"obs mean {o.mean():.3f} max {o.max():.2f} | sim mean {s.mean():.3f} max {s.max():.2f} (m3/s)")
    j.to_csv(HERE / "series.csv")
    print("daily series written to series.csv")


main()
