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
