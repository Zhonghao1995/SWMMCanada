"""FastAPI app for the async tasks-api (integration spec §2.1):

  POST   /api/v1/aoi/preview        -> 200 {geometry, bbox, area_km2, mode, city, in_canada}  (parse only, no build)
  POST   /api/v1/tasks              -> 202 {task_id, status}   (json polygon or multipart shp)
  GET    /api/v1/tasks/{id}         -> 200 {state, progress_pct, stage, error}
  GET    /api/v1/tasks/{id}/result  -> 200 zip | 409 not ready | 404
  GET    /api/v1/coverage           -> 200 {real_network_cities, synthesis}
  GET    /api/v1/healthz            -> 200

Inline pre-checks (AOI parse, max-AOI cap, date sanity) fail fast as 4xx before a task is
created. The pipeline is injected (default = the live build_from_aoi) so the contract is
testable against a fast fake pipeline.
"""
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from functools import partial
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from shapely.geometry import mapping as shp_mapping

from swmmcanada.acquire.design_storm import DesignStormChoice
from swmmcanada.api.tasks import TaskStore, run_task
from swmmcanada.build.config import InfiltrationModel
from swmmcanada.geo import aoi_from_geojson, aoi_from_shapefile
from swmmcanada.geo.errors import AOIOversizeError, GeoError
from swmmcanada.pipeline import pipeline_for_aoi
from swmmcanada.sources.cities.registry import city_for_point, coverage_summary, in_canada_coarse
from swmmcanada.sources.idf_eccc import IDF_RETURN_PERIODS


def create_app(*, pipeline=None, workdir=None, run_inline: bool = False) -> FastAPI:
    app = FastAPI(title="SWMMCanada API")
    # CORS origins come from $ALLOWED_ORIGINS (comma-separated); default "*" (no credentials used).
    _origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_methods=["*"], allow_headers=["*"])
    store = TaskStore()
    work_root = Path(workdir or tempfile.mkdtemp(prefix="swmmcanada_"))
    executor = ThreadPoolExecutor(max_workers=2)

    @app.get("/api/v1/coverage")
    def coverage():
        """What this deployment can build: discovery for integrating
        clients (aiswmm answers "which cities are supported" from here
        instead of a hardcoded list that drifts, as its Regina-missing
        hint once did)."""
        return {
            "real_network_cities": coverage_summary(),
            "synthesis": "anywhere in Canada from open data",
        }

    @app.get("/api/v1/healthz")
    def healthz():
        # Round-2 F-023: artifacts and services must identify their revision.
        import os
        try:
            from importlib.metadata import version as _pkg_version
            ver = _pkg_version("swmmcanada")
        except Exception:  # noqa: BLE001 — health must never 500 over metadata
            ver = "unknown"
        return {"status": "ok", "version": ver,
                "revision": os.environ.get("SOURCE_REVISION", "unknown")}

    async def _aoi_from_request(polygon: Optional[str], file: Optional[UploadFile]):
        """The ONE upload-parsing path (shared by submit + preview): geojson text, .geojson
        file, or zipped shapefile → canonical AOI. Geo errors map to the same HTTP codes
        everywhere, so the preview endpoint fails exactly like a real submit would."""
        try:
            if file is not None:
                raw = await file.read()
                name = (file.filename or "").lower()
                if name.endswith((".geojson", ".json")):
                    return aoi_from_geojson(raw.decode("utf-8"))
                return aoi_from_shapefile(raw, filename=file.filename)
            if polygon:
                return aoi_from_geojson(polygon)
            raise HTTPException(422, "Provide a drawn polygon or a shapefile.")
        except HTTPException:
            raise
        except AOIOversizeError as exc:
            raise HTTPException(413, str(exc))
        except GeoError as exc:
            raise HTTPException(422, str(exc))

    @app.post("/api/v1/aoi/preview")
    async def aoi_preview(
        polygon: Optional[str] = Form(None),
        file: Optional[UploadFile] = File(None),
    ):
        """Parse an uploaded boundary WITHOUT starting a build: the frontend shows the
        geometry on the map, its true area, and any parse error (bad CRS, oversize AOI)
        the moment the user picks the file instead of minutes into a build."""
        aoi = await _aoi_from_request(polygon, file)
        # Pre-flight routing facts (aiswmm integration spec follow-up): which
        # pathway a submit of this AOI would take, before any build starts.
        # ``mode`` comes from the SAME dispatcher submit uses, so preview and
        # submit can never disagree; ``in_canada`` is the coarse envelope
        # (generous by design; the build is the real authority).
        min_lon, min_lat, max_lon, max_lat = aoi.bbox
        centre_lon = (min_lon + max_lon) / 2
        centre_lat = (min_lat + max_lat) / 2
        spec = city_for_point(centre_lon, centre_lat)
        _, mode = pipeline_for_aoi(aoi)
        return {
            "geometry": shp_mapping(aoi.geometry),
            "bbox": list(aoi.bbox),
            "area_km2": aoi.area_km2,
            "source": aoi.source,
            "mode": mode,
            "city": spec.key if spec is not None else None,
            "in_canada": in_canada_coarse(centre_lon, centre_lat),
        }

    @app.post("/api/v1/tasks", status_code=202)
    async def submit(
        start_date: str = Form(...),
        end_date: str = Form(...),
        polygon: Optional[str] = Form(None),
        file: Optional[UploadFile] = File(None),
        infiltration: Optional[str] = Form(None),
        design_storm_yr: Optional[int] = Form(None),
        design_storm_h: int = Form(24),
    ):
        aoi = await _aoi_from_request(polygon, file)
        try:
            start = date.fromisoformat(start_date)
            end = date.fromisoformat(end_date)
        except ValueError as exc:
            raise HTTPException(422, f"Bad date: {exc}")
        if end < start:
            raise HTTPException(422, "end_date is before start_date.")
        # F-014 (ADR 0024): minimal input contract — bound the window, refuse degenerate
        # geometry, keep the service Canadian (both data sources and the honesty story
        # assume it).
        if (end - start).days > 731:
            raise HTTPException(422, "Date span exceeds the 2-year cap (731 days).")
        import math as _math
        if not _math.isfinite(aoi.area_km2) or aoi.area_km2 <= 0:
            raise HTTPException(422, "AOI area is not a finite positive number — "
                                     "check the polygon's coordinates.")
        _cx = (aoi.bbox[0] + aoi.bbox[2]) / 2
        _cy = (aoi.bbox[1] + aoi.bbox[3]) / 2
        if not in_canada_coarse(_cx, _cy):
            raise HTTPException(422, "AOI is outside Canada — every data source this "
                                     "service builds from is Canadian.")
        if infiltration is not None:               # ADR 0013: build-time method choice
            try:
                infiltration = InfiltrationModel(infiltration.upper()).value
            except ValueError:
                raise HTTPException(
                    422, f"Unknown infiltration method {infiltration!r} — "
                         f"one of: {', '.join(m.value for m in InfiltrationModel)}")
        design_storm = None
        if design_storm_yr is not None:            # ADR 0018: presence of a return period
            if design_storm_yr not in IDF_RETURN_PERIODS:   # IS the mode selection
                raise HTTPException(
                    422, f"Unknown design-storm return period {design_storm_yr} — "
                         f"one of: {', '.join(str(t) for t in IDF_RETURN_PERIODS)} (yr)")
            if not 1 <= design_storm_h <= 24:
                raise HTTPException(
                    422, f"Design-storm duration {design_storm_h} h out of range (1–24).")
            design_storm = DesignStormChoice(return_period_yr=design_storm_yr,
                                             duration_h=design_storm_h)

        task_id = store.create()
        if pipeline is None:                       # auto-select the pathway by AOI location
            build_fn, mode = pipeline_for_aoi(aoi)
        else:                                      # explicit pipeline (tests / override)
            build_fn, mode = pipeline, "injected"
        if infiltration is not None:               # bind only when asked, so injected test
            build_fn = partial(build_fn, infiltration=infiltration)  # pipelines stay as-is
        if design_storm is not None:               # same contract as infiltration (ADR 0018)
            build_fn = partial(build_fn, design_storm=design_storm)
        args = (task_id, aoi, start, end, store, work_root, build_fn, mode)
        if run_inline:
            run_task(*args)
        else:
            executor.submit(run_task, *args)
        return {"task_id": task_id, "status": "QUEUED", "mode": mode}

    @app.get("/api/v1/tasks/{task_id}")
    def status(task_id: str):
        task = store.get(task_id)
        if task is None:
            raise HTTPException(404, "Unknown task.")
        return {
            "state": task.state,
            "progress_pct": task.progress_pct,
            "stage": task.stage,
            "mode": task.mode,
            "error": ({"message": task.error} if task.error else None),
        }

    @app.get("/api/v1/tasks/{task_id}/result")
    def result(task_id: str):
        task = store.get(task_id)
        if task is None:
            raise HTTPException(404, "Unknown task.")
        if task.state != "SUCCEEDED" or not task.result_zip:
            raise HTTPException(409, "Result not ready.")
        return FileResponse(str(task.result_zip), media_type="application/zip", filename="model_package.zip")

    @app.get("/api/v1/tasks/{task_id}/preview")
    def preview(task_id: str):
        task = store.get(task_id)
        if task is None:
            raise HTTPException(404, "Unknown task.")
        if task.state != "SUCCEEDED" or not task.preview:
            raise HTTPException(409, "Preview not ready.")
        return FileResponse(str(task.preview), media_type="application/geo+json")

    @app.get("/api/v1/tasks/{task_id}/validation")
    def validation(task_id: str):
        # Available whenever the model reached validation — including FAILED tasks, so the
        # user can see WHY a model was rejected (the subcatchment acceptance report).
        task = store.get(task_id)
        if task is None:
            raise HTTPException(404, "Unknown task.")
        if not task.validation:
            raise HTTPException(409, "Validation not ready.")
        return FileResponse(str(task.validation), media_type="application/json", filename="validation.json")

    return app
