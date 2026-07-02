"""TDD for the tasks-api async contract (integration spec §2), against a fast fake
pipeline (no network). run_inline=True makes the worker synchronous for determinism."""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from swmmcanada.api import create_app
from swmmcanada.build import BuildResult

OTTAWA = {
    "type": "Polygon",
    "coordinates": [
        [[-75.70, 45.41], [-75.68, 45.41], [-75.68, 45.42], [-75.70, 45.42], [-75.70, 45.41]]
    ],
}
OVERSIZE = {
    "type": "Polygon",
    "coordinates": [
        [[-75.74, 45.36], [-75.66, 45.36], [-75.66, 45.41], [-75.74, 45.41], [-75.74, 45.36]]
    ],
}


def fake_pipeline(aoi, start, end, ws, *, report=None, **kwargs):
    ws = Path(ws)
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "model.inp").write_text("[TITLE]\nfake model\n")
    (ws / "manifest.json").write_text("{}")
    (ws / "validation.json").write_text(json.dumps({"ok": True, "subcatchment_method": "junction_voronoi"}))
    # The worker refuses to ship an incomplete result package (ADR 0009) — a fake must
    # produce every required path too (empty carriers are fine, only presence is checked).
    from swmmcanada import result_package
    for rel in result_package.REQUIRED:
        p = ws / rel
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"")
    if report:
        report("BUILDING", 90)
    return BuildResult(
        inp_path=ws / "model.inp", package_dir=ws, manifest_path=ws / "manifest.json",
        sections_written=["TITLE"], warnings=[],
    )


def _client(tmp_path):
    return TestClient(create_app(pipeline=fake_pipeline, workdir=tmp_path, run_inline=True))


def test_incomplete_package_fails_the_task(tmp_path):
    """ADR 0009: a structurally incomplete package must FAIL loudly here, not ship a zip
    that breaks downstream (aiswmm) at runtime."""
    def broken_pipeline(aoi, start, end, ws, *, report=None, **kwargs):
        ws = Path(ws)
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "model.inp").write_text("[TITLE]\n")      # no datastore/, no preview/
        (ws / "manifest.json").write_text("{}")
        (ws / "validation.json").write_text('{"ok": true}')
        return BuildResult(inp_path=ws / "model.inp", package_dir=ws,
                           manifest_path=ws / "manifest.json", sections_written=[], warnings=[])

    client = TestClient(create_app(pipeline=broken_pipeline, workdir=tmp_path, run_inline=True))
    task_id = _submit(client, OTTAWA).json()["task_id"]
    body = client.get(f"/api/v1/tasks/{task_id}").json()
    assert body["state"] == "FAILED"
    msg = body["error"]["message"]
    assert "incomplete" in msg and "datastore" in msg


def _submit(client, geojson, start="2022-06-01", end="2022-06-07"):
    return client.post(
        "/api/v1/tasks",
        data={"start_date": start, "end_date": end, "polygon": json.dumps(geojson)},
    )


def test_submit_poll_download(tmp_path):
    client = _client(tmp_path)
    r = _submit(client, OTTAWA)
    assert r.status_code == 202
    task_id = r.json()["task_id"]

    s = client.get(f"/api/v1/tasks/{task_id}")
    assert s.status_code == 200
    body = s.json()
    assert body["state"] == "SUCCEEDED" and body["progress_pct"] == 100 and body["error"] is None

    res = client.get(f"/api/v1/tasks/{task_id}/result")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/zip"
    assert len(res.content) > 0


def test_submit_geojson_file_upload(tmp_path):
    # A single self-contained .geojson upload (not a zipped shapefile) is accepted.
    client = _client(tmp_path)
    feature = {"type": "Feature", "properties": {}, "geometry": OTTAWA}
    r = client.post(
        "/api/v1/tasks",
        data={"start_date": "2022-06-01", "end_date": "2022-06-07"},
        files={"file": ("aoi.geojson", json.dumps(feature), "application/geo+json")},
    )
    assert r.status_code == 202
    task_id = r.json()["task_id"]
    assert client.get(f"/api/v1/tasks/{task_id}").json()["state"] == "SUCCEEDED"


def test_oversize_aoi_413(tmp_path):
    assert _submit(_client(tmp_path), OVERSIZE).status_code == 413


def test_bad_date_order_422(tmp_path):
    assert _submit(_client(tmp_path), OTTAWA, start="2022-06-07", end="2022-06-01").status_code == 422


def test_missing_aoi_422(tmp_path):
    client = _client(tmp_path)
    r = client.post("/api/v1/tasks", data={"start_date": "2022-06-01", "end_date": "2022-06-07"})
    assert r.status_code == 422


def test_unknown_task_404(tmp_path):
    client = _client(tmp_path)
    assert client.get("/api/v1/tasks/nope").status_code == 404
    assert client.get("/api/v1/tasks/nope/result").status_code == 404


def test_validation_endpoint_serves_report(tmp_path):
    client = _client(tmp_path)
    task_id = _submit(client, OTTAWA).json()["task_id"]
    r = client.get(f"/api/v1/tasks/{task_id}/validation")
    assert r.status_code == 200
    assert r.json()["ok"] is True and r.json()["subcatchment_method"] == "junction_voronoi"


def test_healthz(tmp_path):
    assert _client(tmp_path).get("/api/v1/healthz").json() == {"status": "ok"}
