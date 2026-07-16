"""The result-package contract (ADR 0009): the schema is the single source of truth for
what a shippable package contains; missing_required is the guard api/tasks enforces."""
from swmmcanada import result_package as rp


def _touch_all(root):
    for rel in rp.REQUIRED:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"")


def test_complete_package_has_nothing_missing(tmp_path):
    _touch_all(tmp_path)
    assert rp.missing_required(tmp_path) == []


def test_missing_paths_are_named(tmp_path):
    _touch_all(tmp_path)
    (tmp_path / rp.MODEL_INP).unlink()
    (tmp_path / rp.PREVIEW_GEOJSON).unlink()
    assert rp.missing_required(tmp_path) == [rp.MODEL_INP, rp.PREVIEW_GEOJSON]


def test_contract_covers_the_handoff_essentials():
    # The .inp, its manifest, the validation verdict, all three datastore carriers, preview.
    assert rp.MODEL_INP in rp.REQUIRED and rp.MANIFEST_JSON in rp.REQUIRED
    assert rp.VALIDATION_JSON in rp.REQUIRED
    assert sum(1 for r in rp.REQUIRED if r.startswith(f"{rp.DATASTORE_DIR}/")) == 3
    assert rp.PREVIEW_GEOJSON in rp.REQUIRED
    # 2D-overland raw materials are promised deliverables, not workspace leftovers
    assert rp.DEM_DTM in rp.REQUIRED and rp.LANDCOVER in rp.REQUIRED
    # mikeplus/ is deliberately NOT required (ADR 0008 graceful degradation).
    assert not any(r.startswith(rp.MIKEPLUS_DIR) for r in rp.REQUIRED)


def test_observed_flow_exports_when_hydat_present(tmp_path, monkeypatch):
    """North star: the promised calibration target ships when HYDAT + a station exist,
    and its absence is a note, never a failure."""
    import sys
    from datetime import date
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent / "acquire"))
    from test_hydro import _make_hydat  # reuse the recorded-fixture builder

    from swmmcanada.geo import aoi_from_geojson
    from swmmcanada.pipeline import _export_observed_safe

    aoi = aoi_from_geojson({"type": "Polygon", "coordinates": [[
        [-75.70, 45.41], [-75.68, 45.41], [-75.68, 45.43], [-75.70, 45.43], [-75.70, 45.41]]]})
    hydat = tmp_path / "hydat.sqlite"
    _make_hydat(hydat)

    ws = tmp_path / "ws"; ws.mkdir()
    monkeypatch.setenv("SWMMCANADA_HYDAT_PATH", str(hydat))
    _export_observed_safe(ws, aoi, date(2022, 6, 1), date(2022, 6, 30))
    assert (ws / "observed_flow.csv").exists()
    head = (ws / "observed_flow.csv").read_text().splitlines()[0]
    assert "station_number" in head and "discharge" in head

    ws2 = tmp_path / "ws2"; ws2.mkdir()
    monkeypatch.delenv("SWMMCANADA_HYDAT_PATH")
    _export_observed_safe(ws2, aoi, date(2022, 6, 1), date(2022, 6, 30))
    assert not list(ws2.iterdir())                      # no HYDAT → silent no-op


def test_record_terrain_stamps_the_manifest(tmp_path):
    """The first question a 2D modeller asks is "1 m LiDAR or 30 m national?" — the
    manifest answers it for every build."""
    import json

    (tmp_path / rp.MANIFEST_JSON).write_text(json.dumps({"title": "x"}))
    rp.record_terrain(tmp_path, source="hrdem-lidar:proj-1m", resolution_m=1.0, coverage="full")
    data = json.loads((tmp_path / rp.MANIFEST_JSON).read_text())
    assert data["title"] == "x"                       # existing keys survive
    t = data["terrain"]
    assert t["dem"] == rp.DEM_DTM and t["landcover"] == rp.LANDCOVER
    assert t["source"] == "hrdem-lidar:proj-1m" and t["resolution_m"] == 1.0
    assert t["coverage"] == "full"


def test_record_forcing_stamps_the_manifest(tmp_path):
    import json

    (tmp_path / rp.MANIFEST_JSON).write_text(json.dumps({"title": "x"}))
    rp.record_forcing(tmp_path, {"rainfall_resolution": "hourly", "station": "1014820",
                                 "coverage_pct": 98.2, "mismatch_warning": "internal-only"})
    data = json.loads((tmp_path / rp.MANIFEST_JSON).read_text())
    assert data["forcing"]["rainfall_resolution"] == "hourly"
    assert data["forcing"]["coverage_pct"] == 98.2
    assert "mismatch_warning" not in data["forcing"]     # warning text lives in validation


# --- F-019 (ADR 0024): required = regular files inside the root; integrity block -------

def test_symlink_and_directory_do_not_satisfy_required(tmp_path):
    from swmmcanada import result_package as rp

    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / rp.MODEL_INP).mkdir()                       # a DIRECTORY named model.inp
    missing = rp.missing_required(pkg)
    assert rp.MODEL_INP in missing


def test_member_checksums_cover_regular_files(tmp_path):
    import hashlib, json
    from swmmcanada import result_package as rp

    pkg = tmp_path / "pkg"
    (pkg / "sub").mkdir(parents=True)
    (pkg / "a.txt").write_text("hello")
    (pkg / "sub" / "b.bin").write_bytes(b"\x00\x01")
    rp.record_checksums(pkg)
    data = json.loads((pkg / rp.MANIFEST_JSON).read_text())
    members = data["integrity"]["members"]
    assert members["a.txt"]["sha256"] == hashlib.sha256(b"hello").hexdigest()
    assert members["a.txt"]["bytes"] == 5
    assert "sub/b.bin" in members
    assert rp.MANIFEST_JSON not in members             # the manifest can't checksum itself
