"""Graph Job Worker - FastAPI application.

Provides the durable job API for graph building (ground / drill) and
legacy compatibility endpoints.  All heavy work is delegated to the
:mod:`graphrag_api.pipeline.GraphPipeline`; no subprocess calls happen
inside route handlers.

Public entry points:
  - ``create_app(store, pipeline, paths)`` - factory, safe for testing.
  - ``app`` - module-level singleton for ASGI servers.

ASCII only - no non-ASCII characters in code or comments.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, HTTPException, Path as PathParam
from fastapi.responses import FileResponse

from graphrag_api.job_store import ActiveJobError, JobStore
from graphrag_api.pipeline import GraphPipeline
from graphrag_api.schemas import JobCreate, JobResponse


# ---------------------------------------------------------------------------
# Default production paths
# ---------------------------------------------------------------------------

_DEFAULT_INDEX_ROOT = Path("/app/indexing")
_DEFAULT_INDEX_DRILL_ROOT = Path("/app/indexing_drill")
_DEFAULT_INPUT_DIR = _DEFAULT_INDEX_ROOT / "input"
_DEFAULT_DRILL_INPUT_DIR = _DEFAULT_INDEX_DRILL_ROOT / "input"
_DEFAULT_GROUND_DOWNLOAD_DIR = _DEFAULT_INDEX_ROOT / "ground_graph_fill"
_DEFAULT_DRILL_DOWNLOAD_DIR = _DEFAULT_INDEX_DRILL_ROOT / "drill_graph_fill"


# ---------------------------------------------------------------------------
# Safe filename validation
# ---------------------------------------------------------------------------

def _safe_filename(name: str) -> str:
    """Validate that *name* is a safe basename with no path traversal.

    Returns the name on success.  Raises HTTPException(400) on violation.
    """
    if name in (".", ".."):
        raise HTTPException(status_code=400, detail="Unsafe file name")
    if "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail="Unsafe file name")
    candidate = Path(name).name
    if not candidate or candidate != name:
        raise HTTPException(status_code=400, detail="Unsafe file name")
    return candidate


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(
    store: Optional[JobStore] = None,
    pipeline: Optional[GraphPipeline] = None,
    paths: Optional[Dict[str, Path]] = None,
) -> FastAPI:
    """Build and return a configured FastAPI application.

    Parameters
    ----------
    store:
        A :class:`JobStore` instance.  When ``None`` a new store is created
        from ``GRAPH_JOB_DB`` env var (default ``/app/jobs/jobs.db``).
    pipeline:
        A :class:`GraphPipeline` instance.  When ``None`` a default
        pipeline is constructed (no finalizer - jobs will end up *failed*
        after the build subprocess succeeds, as expected before Task 10).
    paths:
        Override directory paths used by file-serving endpoints.  Keys:
        ``input_dir``, ``drill_input_dir``, ``ground_download_dir``,
        ``drill_download_dir``.  When ``None`` the production defaults
        (``/app/indexing/...``) are used.
    """

    # -- resolve dependencies -----------------------------------------------
    if store is None:
        db_path = os.environ.get("GRAPH_JOB_DB", "/app/jobs/jobs.db")
        store = JobStore(Path(db_path))

    if pipeline is None:
        pipeline = GraphPipeline(store=store)

    if paths is None:
        paths = {
            "input_dir": _DEFAULT_INPUT_DIR,
            "drill_input_dir": _DEFAULT_DRILL_INPUT_DIR,
            "ground_download_dir": _DEFAULT_GROUND_DOWNLOAD_DIR,
            "drill_download_dir": _DEFAULT_DRILL_DOWNLOAD_DIR,
        }

    input_dir: Path = paths["input_dir"]
    drill_input_dir: Path = paths["drill_input_dir"]
    ground_download_dir: Path = paths["ground_download_dir"]
    drill_download_dir: Path = paths["drill_download_dir"]

    # -- lifespan -----------------------------------------------------------

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Startup: mark any jobs left active from a previous crash as
        # interrupted BEFORE starting the pipeline worker.
        store.mark_running_interrupted()
        pipeline.start()
        try:
            yield
        finally:
            # Shutdown: stop the pipeline worker gracefully.
            pipeline.stop()

    # -- build app ----------------------------------------------------------

    app = FastAPI(
        title="Graph Job Worker",
        description=(
            "Durable graph build API.  All heavy work runs in a single "
            "background worker managed by GraphPipeline."
        ),
        lifespan=lifespan,
    )

    # ======================================================================
    # Job API endpoints
    # ======================================================================

    @app.post(
        "/jobs",
        status_code=202,
        response_model=JobResponse,
        summary="Create a new graph build job",
    )
    def create_job(body: JobCreate):
        try:
            record = pipeline.submit(body.graph_type)
        except ActiveJobError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return JobResponse.model_validate(record).model_dump()

    @app.get(
        "/jobs/{task_id}",
        response_model=JobResponse,
        summary="Get job status",
    )
    def get_job(task_id: str):
        record = store.get(task_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return JobResponse.model_validate(record).model_dump()

    @app.post(
        "/jobs/{task_id}/cancel",
        response_model=JobResponse,
        summary="Request cancellation of an active job",
    )
    def cancel_job(task_id: str):
        try:
            record = pipeline.cancel(task_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Job not found")
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return JobResponse.model_validate(record).model_dump()

    @app.post(
        "/jobs/{task_id}/retry",
        response_model=JobResponse,
        summary="Retry a terminal (failed/cancelled) job",
    )
    def retry_job(task_id: str):
        try:
            record = pipeline.retry(task_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Job not found")
        except ActiveJobError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return JobResponse.model_validate(record).model_dump()

    # ======================================================================
    # Health
    # ======================================================================

    @app.get("/health", summary="Health check")
    def health():
        return {"status": "ok"}

    # ======================================================================
    # Legacy compatibility -- deprecated, thin adapters
    # ======================================================================

    @app.post(
        "/build_graph",
        status_code=202,
        response_model=JobResponse,
        deprecated=True,
        summary="[deprecated] Submit a ground graph build job",
    )
    def build_graph_legacy():
        try:
            record = pipeline.submit("ground")
        except ActiveJobError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return JobResponse.model_validate(record).model_dump()

    @app.post(
        "/build_drillgraph",
        status_code=202,
        response_model=JobResponse,
        deprecated=True,
        summary="[deprecated] Submit a drill graph build job",
    )
    def build_drillgraph_legacy():
        try:
            record = pipeline.submit("drill")
        except ActiveJobError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return JobResponse.model_validate(record).model_dump()

    # ======================================================================
    # /init_index -- deprecated 410
    # ======================================================================

    @app.post(
        "/init_index",
        status_code=410,
        deprecated=True,
        summary="[removed] Init is now managed by the job pipeline",
    )
    def init_index():
        raise HTTPException(
            status_code=410,
            detail=(
                "Direct init_index is no longer supported. "
                "Initialization is managed by the job pipeline. "
                "Submit a job via POST /jobs instead."
            ),
        )

    # ======================================================================
    # File-serving endpoints (legacy compatibility)
    # ======================================================================

    def _resolve_input_dir(directory_type: str) -> Path:
        """Map directory_type to the corresponding input directory."""
        if directory_type == "ground":
            return input_dir
        if directory_type == "drill":
            return drill_input_dir
        raise HTTPException(
            status_code=422,
            detail="directory_type must be 'ground' or 'drill'",
        )

    def _resolve_download_dir(directory_type: str) -> Path:
        """Map directory_type to the corresponding download directory."""
        if directory_type == "ground":
            return ground_download_dir
        if directory_type == "drill":
            return drill_download_dir
        raise HTTPException(
            status_code=422,
            detail="directory_type must be 'ground' or 'drill'",
        )

    @app.get(
        "/get_file_list/{directory_type}",
        summary="List files in the input directory",
    )
    def get_file_list(
        directory_type: str = PathParam(
            ..., pattern="^(drill|ground)$", description="Directory type"
        ),
    ):
        target_dir = _resolve_input_dir(directory_type)
        if not target_dir.exists() or not target_dir.is_dir():
            return {
                "status": "success",
                "directory": str(target_dir),
                "file_count": 0,
                "files": [],
            }
        entries = sorted(
            (
                {"file_name": f.name, "size_bytes": f.stat().st_size}
                for f in target_dir.iterdir()
                if f.is_file()
            ),
            key=lambda e: e["file_name"],
        )
        return {
            "status": "success",
            "directory": str(target_dir),
            "file_count": len(entries),
            "files": entries,
        }

    @app.get(
        "/get_downloadable_files/{directory_type}",
        summary="List downloadable artifact files",
    )
    def get_downloadable_files(
        directory_type: str = PathParam(
            ..., pattern="^(drill|ground)$", description="Directory type"
        ),
    ):
        target_dir = _resolve_download_dir(directory_type)
        if not target_dir.exists() or not target_dir.is_dir():
            return {
                "status": "success",
                "directory": str(target_dir),
                "file_count": 0,
                "files": [],
            }
        entries = sorted(
            (
                {"file_name": f.name, "size_bytes": f.stat().st_size}
                for f in target_dir.iterdir()
                if f.is_file()
            ),
            key=lambda e: e["file_name"],
        )
        return {
            "status": "success",
            "directory": str(target_dir),
            "file_count": len(entries),
            "files": entries,
        }

    @app.delete(
        "/delete_specific_file/{directory_type}/{file_name}",
        summary="Delete a specific file from an input directory",
    )
    def delete_specific_file(
        directory_type: str = PathParam(
            ..., pattern="^(drill|ground)$", description="Directory type"
        ),
        file_name: str = PathParam(..., description="File name to delete"),
    ):
        safe_name = _safe_filename(file_name)
        target_dir = _resolve_input_dir(directory_type)
        target = (target_dir / safe_name).resolve()
        if target.parent != target_dir.resolve():
            raise HTTPException(status_code=400, detail="Unsafe file path")
        if not target.exists() or not target.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        target.unlink()
        return {
            "status": "success",
            "detail": (
                "File '{0}' deleted from '{1}' directory.".format(
                    safe_name, directory_type
                )
            ),
        }

    @app.get(
        "/download_file/{directory_type}/{file_name}",
        summary="Download a specific artifact file",
    )
    def download_file(
        directory_type: str = PathParam(
            ..., pattern="^(drill|ground)$", description="Directory type"
        ),
        file_name: str = PathParam(..., description="File name to download"),
    ):
        safe_name = _safe_filename(file_name)
        target_dir = _resolve_download_dir(directory_type)
        target = (target_dir / safe_name).resolve()
        if target.parent != target_dir.resolve():
            raise HTTPException(status_code=400, detail="Unsafe file path")
        if not target.exists():
            raise HTTPException(status_code=404, detail="File not found")
        if not target.is_file():
            raise HTTPException(status_code=400, detail="Not a regular file")
        return FileResponse(
            path=str(target),
            filename=safe_name,
            media_type="application/octet-stream",
        )

    return app


# ---------------------------------------------------------------------------
# Module-level singleton -- used by ``uvicorn graphrag_api.main:app``.
# ---------------------------------------------------------------------------

app = create_app()
