"""FastAPI app for the async tasks-api (integration spec §2.1):

  POST   /api/v1/tasks              -> 202 {task_id, status}   (json polygon or multipart shp)
  GET    /api/v1/tasks/{id}         -> 200 {state, progress_pct, stage, error}
  GET    /api/v1/tasks/{id}/result  -> 200 zip | 409 not ready | 404
  GET    /api/v1/healthz            -> 200

Inline pre-checks (AOI parse, max-AOI cap, date sanity) fail fast as 4xx before a task is
created. The pipeline is injected (default = the live build_from_aoi) so the contract is
testable against a fast fake pipeline.
"""
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from swmmcanada.api.tasks import TaskStore, run_task
from swmmcanada.geo import aoi_from_geojson, aoi_from_shapefile
from swmmcanada.geo.errors import AOIOversizeError, GeoError
from swmmcanada.pipeline import pipeline_for_aoi


def create_app(*, pipeline=None, workdir=None, run_inline: bool = False) -> FastAPI:
    app = FastAPI(title="SWMMCanada API")
    # CORS origins come from $ALLOWED_ORIGINS (comma-separated); default "*" (no credentials used).
    _origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_methods=["*"], allow_headers=["*"])
    store = TaskStore()
    work_root = Path(workdir or tempfile.mkdtemp(prefix="swmmcanada_"))
    executor = ThreadPoolExecutor(max_workers=2)

    @app.get("/api/v1/healthz")
    def healthz():
        return {"status": "ok"}

    @app.post("/api/v1/tasks", status_code=202)
    async def submit(
        start_date: str = Form(...),
        end_date: str = Form(...),
        polygon: Optional[str] = Form(None),
        file: Optional[UploadFile] = File(None),
    ):
        try:
            if file is not None:
                raw = await file.read()
                name = (file.filename or "").lower()
                if name.endswith((".geojson", ".json")):
                    aoi = aoi_from_geojson(raw.decode("utf-8"))
                else:
                    aoi = aoi_from_shapefile(raw, filename=file.filename)
            elif polygon:
                aoi = aoi_from_geojson(polygon)
            else:
                raise HTTPException(422, "Provide a drawn polygon or a shapefile.")
            start = date.fromisoformat(start_date)
            end = date.fromisoformat(end_date)
        except HTTPException:
            raise
        except AOIOversizeError as exc:
            raise HTTPException(413, str(exc))
        except GeoError as exc:
            raise HTTPException(422, str(exc))
        except ValueError as exc:
            raise HTTPException(422, f"Bad date: {exc}")
        if end < start:
            raise HTTPException(422, "end_date is before start_date.")

        task_id = store.create()
        if pipeline is None:                       # auto-select the pathway by AOI location
            build_fn, mode = pipeline_for_aoi(aoi)
        else:                                      # explicit pipeline (tests / override)
            build_fn, mode = pipeline, "injected"
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
