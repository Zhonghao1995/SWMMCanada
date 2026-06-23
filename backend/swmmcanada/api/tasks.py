"""Async task state + worker (integration spec §2). State machine:
QUEUED → RUNNING → {SUCCEEDED | FAILED}. Local mode = an in-memory store + a thread;
the same run_task is what a hosted worker would call (deployment seam)."""
import threading
import uuid
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional


@dataclass
class Task:
    id: str
    state: str = "QUEUED"          # QUEUED | RUNNING | SUCCEEDED | FAILED | CANCELLED
    progress_pct: int = 0
    stage: Optional[str] = None
    error: Optional[str] = None
    result_zip: Optional[Path] = None
    preview: Optional[Path] = None
    validation: Optional[Path] = None   # validation.json (set on success AND failure)
    mode: Optional[str] = None          # which build pathway (real-network city vs synthesized)


class TaskStore:
    def __init__(self) -> None:
        self._tasks: dict = {}
        self._lock = threading.Lock()

    def create(self) -> str:
        tid = uuid.uuid4().hex[:12]
        with self._lock:
            self._tasks[tid] = Task(id=tid)
        return tid

    def get(self, tid: str) -> Optional[Task]:
        with self._lock:
            return self._tasks.get(tid)

    def update(self, tid: str, **changes) -> None:
        with self._lock:
            task = self._tasks.get(tid)
            if task:
                for key, value in changes.items():
                    setattr(task, key, value)


def run_task(task_id: str, aoi, start: date, end: date, store: TaskStore, workdir: Path,
             pipeline, mode: Optional[str] = None) -> None:
    """Run the pipeline for one task, updating the store. Never raises — failures become
    FAILED state. This is the single worker entrypoint (local thread or hosted container)."""
    store.update(task_id, state="RUNNING", progress_pct=5, stage="VALIDATING", mode=mode)
    ws = Path(workdir) / task_id

    def _validation() -> Optional[Path]:
        v = ws / "validation.json"      # written by the pipeline before the build, even on error
        return v if v.exists() else None

    try:
        def report(stage: str, pct: int) -> None:
            store.update(task_id, stage=stage, progress_pct=int(pct))

        result = pipeline(aoi, start, end, ws, report=report)
        zip_path = _zip_package(result, ws)
        preview = Path(getattr(result, "package_dir", ws)) / "preview" / "network.geojson"
        store.update(
            task_id, state="SUCCEEDED", progress_pct=100, stage="DONE",
            result_zip=zip_path, preview=(preview if preview.exists() else None),
            validation=_validation(),
        )
    except Exception as exc:  # noqa: BLE001 — surface any failure as FAILED state
        store.update(task_id, state="FAILED", error=str(exc), validation=_validation())


def _zip_package(result, ws: Path) -> Path:
    pkg = Path(getattr(result, "package_dir", ws))
    zip_path = Path(ws) / "swmm_model.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in pkg.rglob("*"):
            if f.is_file() and f.name != zip_path.name:
                zf.write(f, f.relative_to(pkg))
    return zip_path
