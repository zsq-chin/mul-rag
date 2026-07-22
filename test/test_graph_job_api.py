"""Tests for the Graph Job FastAPI layer (Task 9B-2A).

Coverage targets:
  - POST /jobs create 202, GET /jobs/{id}, active collision 409, 404
  - POST /jobs/{id}/cancel, POST /jobs/{id}/retry
  - POST /build_graph, POST /build_drillgraph legacy 202 adapters
  - lifespan interrupted-before-start ordering and shutdown stop
  - import / create_app does NOT start worker or subprocess
  - GET /health
  - File list / download / delete with traversal rejection
  - /init_index returns 410
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, List

from fastapi.testclient import TestClient

from graphrag_api.job_store import ActiveJobError, JobStore
from graphrag_api.schemas import JobRecord


# ---------------------------------------------------------------------------
# Stub pipeline -- deterministic, no threads, no subprocesses
# ---------------------------------------------------------------------------


class StubPipeline:
    """Minimal pipeline double for API-layer testing.

    Delegates create/retry/cancel to the real store so the API gets proper
    JobRecord objects, but never starts a worker thread.
    """

    def __init__(self, store: JobStore) -> None:
        self._store = store
        self._started = False
        self._stopped = False
        self._call_log: List[str] = []

    def submit(self, graph_type: str) -> JobRecord:
        return self._store.create(graph_type)

    def start(self) -> None:
        self._started = True
        self._call_log.append("start")

    def stop(self, timeout: float = 10.0) -> None:
        self._stopped = True
        self._call_log.append("stop")

    def cancel(self, job_id: str) -> JobRecord:
        self._call_log.append("cancel:{0}".format(job_id))
        return self._store.request_cancel(job_id)

    def retry(self, job_id: str) -> JobRecord:
        self._call_log.append("retry:{0}".format(job_id))
        return self._store.retry(job_id)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class GraphJobApiTests(unittest.TestCase):
    """Test suite for the FastAPI graph-job API layer."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self.db_path = Path(self._tmp) / "jobs.db"
        self.store = JobStore(self.db_path)
        self.pipeline = StubPipeline(self.store)

        # Create injectable file-system roots
        self.input_dir = Path(self._tmp) / "indexing" / "input"
        self.input_dir.mkdir(parents=True, exist_ok=True)
        self.drill_input_dir = Path(self._tmp) / "indexing_drill" / "input"
        self.drill_input_dir.mkdir(parents=True, exist_ok=True)
        self.ground_dl_dir = Path(self._tmp) / "indexing" / "ground_graph_fill"
        self.ground_dl_dir.mkdir(parents=True, exist_ok=True)
        self.drill_dl_dir = Path(self._tmp) / "indexing_drill" / "drill_graph_fill"
        self.drill_dl_dir.mkdir(parents=True, exist_ok=True)

        self.paths = {
            "input_dir": self.input_dir,
            "drill_input_dir": self.drill_input_dir,
            "ground_download_dir": self.ground_dl_dir,
            "drill_download_dir": self.drill_dl_dir,
        }

        # Build the app fresh each test
        from graphrag_api.main import create_app

        self.app = create_app(
            store=self.store,
            pipeline=self.pipeline,
            paths=self.paths,
        )
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self._tmp, ignore_errors=True)

    # -----------------------------------------------------------------------
    # 1. POST /jobs -- create 202
    # -----------------------------------------------------------------------

    def test_create_job_returns_202_with_correct_fields(self) -> None:
        resp = self.client.post("/jobs", json={"graph_type": "ground"})
        self.assertEqual(resp.status_code, 202)
        body = resp.json()
        self.assertEqual(body["graph_type"], "ground")
        self.assertEqual(body["status"], "queued")
        self.assertIn("id", body)
        self.assertIn("created_at", body)
        # All 16 fields must be present
        for field in (
            "id", "graph_type", "status", "stage", "progress",
            "created_at", "started_at", "finished_at", "cancel_requested",
            "input_count", "relationship_count", "artifact_path",
            "artifact_sha256", "error_summary", "log_tail",
        ):
            self.assertIn(field, body, "Missing field: {0}".format(field))

    def test_create_drill_job_returns_202(self) -> None:
        resp = self.client.post("/jobs", json={"graph_type": "drill"})
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.json()["graph_type"], "drill")

    def test_create_job_invalid_graph_type_returns_422(self) -> None:
        resp = self.client.post("/jobs", json={"graph_type": "invalid"})
        self.assertEqual(resp.status_code, 422)

    # -----------------------------------------------------------------------
    # 2. GET /jobs/{id} -- fetch existing job
    # -----------------------------------------------------------------------

    def test_get_job_returns_correct_record(self) -> None:
        create_resp = self.client.post("/jobs", json={"graph_type": "ground"})
        job_id = create_resp.json()["id"]

        resp = self.client.get("/jobs/{0}".format(job_id))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["id"], job_id)
        self.assertEqual(resp.json()["status"], "queued")

    # -----------------------------------------------------------------------
    # 3. Active collision -- 409
    # -----------------------------------------------------------------------

    def test_duplicate_active_job_returns_409(self) -> None:
        self.client.post("/jobs", json={"graph_type": "ground"})
        resp = self.client.post("/jobs", json={"graph_type": "ground"})
        self.assertEqual(resp.status_code, 409)
        body = resp.json()
        self.assertNotEqual(body.get("status"), "success",
                            "Error response must not use success shape")

    # -----------------------------------------------------------------------
    # 4. 404 for missing job
    # -----------------------------------------------------------------------

    def test_get_missing_job_returns_404(self) -> None:
        resp = self.client.get("/jobs/nonexistent-id")
        self.assertEqual(resp.status_code, 404)

    def test_cancel_missing_job_returns_404(self) -> None:
        resp = self.client.post("/jobs/nonexistent-id/cancel")
        self.assertEqual(resp.status_code, 404)

    def test_retry_missing_job_returns_404(self) -> None:
        resp = self.client.post("/jobs/nonexistent-id/retry")
        self.assertEqual(resp.status_code, 404)

    # -----------------------------------------------------------------------
    # 5. Cancel / retry
    # -----------------------------------------------------------------------

    def test_cancel_queued_job_succeeds(self) -> None:
        create_resp = self.client.post("/jobs", json={"graph_type": "ground"})
        job_id = create_resp.json()["id"]

        resp = self.client.post("/jobs/{0}/cancel".format(job_id))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["cancel_requested"])
        self.assertEqual(resp.json()["status"], "cancelling")

    def test_retry_terminal_job_succeeds(self) -> None:
        # Create and force to failed via store
        record = self.store.create("ground")
        self.store.transition(record.id, "failed", 0, error_summary="oops")

        resp = self.client.post("/jobs/{0}/retry".format(record.id))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "queued")

    def test_retry_active_job_returns_409(self) -> None:
        record = self.store.create("ground")
        # Job is still queued (active), retry must be rejected
        resp = self.client.post("/jobs/{0}/retry".format(record.id))
        self.assertEqual(resp.status_code, 409)

    def test_cancel_terminal_job_returns_409(self) -> None:
        record = self.store.create("ground")
        self.store.transition(record.id, "failed", 0, error_summary="x")

        resp = self.client.post("/jobs/{0}/cancel".format(record.id))
        self.assertEqual(resp.status_code, 409)

    # -----------------------------------------------------------------------
    # 6. Legacy adapters -- 202, correct graph_type
    # -----------------------------------------------------------------------

    def test_build_graph_legacy_returns_202_ground(self) -> None:
        resp = self.client.post("/build_graph")
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.json()["graph_type"], "ground")

    def test_build_drillgraph_legacy_returns_202_drill(self) -> None:
        resp = self.client.post("/build_drillgraph")
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.json()["graph_type"], "drill")

    def test_legacy_build_graph_active_collision_returns_409(self) -> None:
        # Create an active ground job first
        self.client.post("/build_graph")
        resp = self.client.post("/build_graph")
        self.assertEqual(resp.status_code, 409)

    # -----------------------------------------------------------------------
    # 7. Lifespan ordering: mark_running_interrupted() THEN pipeline.start()
    #    and shutdown calls pipeline.stop()
    # -----------------------------------------------------------------------

    def test_lifespan_calls_mark_interrupted_before_start(self) -> None:
        """The lifespan must call store.mark_running_interrupted() before
        pipeline.start(), and pipeline.stop() on shutdown."""
        combined_log: List[str] = []

        original_mark = self.store.mark_running_interrupted

        def tracked_mark() -> int:
            combined_log.append("mark_running_interrupted")
            return original_mark()

        self.store.mark_running_interrupted = tracked_mark  # type: ignore[assignment]
        self.pipeline._call_log.clear()

        # Patch pipeline methods to also log into combined_log
        original_start = self.pipeline.start
        original_stop = self.pipeline.stop

        def tracked_start() -> None:
            combined_log.append("start")
            original_start()

        def tracked_stop(timeout: float = 10.0) -> None:
            combined_log.append("stop")
            original_stop(timeout=timeout)

        self.pipeline.start = tracked_start  # type: ignore[assignment]
        self.pipeline.stop = tracked_stop  # type: ignore[assignment]

        # create_app triggers lifespan on TestClient enter/exit
        from graphrag_api.main import create_app

        app = create_app(
            store=self.store,
            pipeline=self.pipeline,
            paths=self.paths,
        )
        with TestClient(app):
            # Inside the lifespan context
            pass

        # mark_running_interrupted must appear before start
        self.assertIn("mark_running_interrupted", combined_log)
        self.assertIn("start", combined_log)

        mark_idx = combined_log.index("mark_running_interrupted")
        start_idx = combined_log.index("start")
        self.assertLess(
            mark_idx, start_idx,
            "mark_running_interrupted must come before start"
        )

        # Shutdown must call stop
        self.assertIn("stop", combined_log)

    def test_lifespan_stops_pipeline_when_context_raises(self) -> None:
        from graphrag_api.main import create_app

        app = create_app(
            store=self.store,
            pipeline=self.pipeline,
            paths=self.paths,
        )

        async def raise_inside_lifespan() -> None:
            async with app.router.lifespan_context(app):
                raise RuntimeError("lifespan body failed")

        with self.assertRaisesRegex(RuntimeError, "lifespan body failed"):
            asyncio.run(raise_inside_lifespan())

        self.assertTrue(self.pipeline._stopped)

    # -----------------------------------------------------------------------
    # 8. Import / create_app does NOT start worker or subprocess
    # -----------------------------------------------------------------------

    def test_import_does_not_start_worker(self) -> None:
        """Importing the module or calling create_app must not start the
        pipeline worker or any subprocess."""
        fresh_pipeline = StubPipeline(self.store)
        self.assertFalse(fresh_pipeline._started)

        from graphrag_api.main import create_app

        app = create_app(
            store=self.store,
            pipeline=fresh_pipeline,
            paths=self.paths,
        )
        # Before entering the lifespan, worker must not be started
        self.assertFalse(fresh_pipeline._started)

    def test_create_app_returns_fastapi_instance(self) -> None:
        from graphrag_api.main import create_app
        from fastapi import FastAPI

        app = create_app(
            store=self.store,
            pipeline=self.pipeline,
            paths=self.paths,
        )
        self.assertIsInstance(app, FastAPI)

    # -----------------------------------------------------------------------
    # 9. GET /health
    # -----------------------------------------------------------------------

    def test_health_returns_200(self) -> None:
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("status", body)

    # -----------------------------------------------------------------------
    # 10. /init_index -- 410 Gone
    # -----------------------------------------------------------------------

    def test_init_index_returns_410(self) -> None:
        resp = self.client.post("/init_index")
        self.assertEqual(resp.status_code, 410)

    # -----------------------------------------------------------------------
    # 11. File list -- ground / drill
    # -----------------------------------------------------------------------

    def test_file_list_ground_returns_sorted_files(self) -> None:
        (self.input_dir / "b.txt").write_text("second")
        (self.input_dir / "a.txt").write_text("first")

        resp = self.client.get("/get_file_list/ground")
        self.assertEqual(resp.status_code, 200)
        names = [f["file_name"] for f in resp.json()["files"]]
        # Must be sorted for stability
        self.assertEqual(names, sorted(names))

    def test_file_list_drill_works(self) -> None:
        (self.drill_input_dir / "d1.csv").write_text("data")

        resp = self.client.get("/get_file_list/drill")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["file_count"], 1)

    def test_file_list_invalid_directory_type_returns_422(self) -> None:
        resp = self.client.get("/get_file_list/invalid")
        self.assertEqual(resp.status_code, 422)

    # -----------------------------------------------------------------------
    # 12. File download
    # -----------------------------------------------------------------------

    def test_download_file_ground_succeeds(self) -> None:
        (self.ground_dl_dir / "out.json").write_text('{"ok":true}')

        resp = self.client.get("/download_file/ground/out.json")
        self.assertEqual(resp.status_code, 200)

    def test_download_missing_file_returns_404(self) -> None:
        resp = self.client.get("/download_file/ground/nope.json")
        self.assertEqual(resp.status_code, 404)

    # -----------------------------------------------------------------------
    # 13. File delete
    # -----------------------------------------------------------------------

    def test_delete_file_succeeds(self) -> None:
        (self.input_dir / "old.txt").write_text("delete me")

        resp = self.client.delete("/delete_specific_file/ground/old.txt")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse((self.input_dir / "old.txt").exists())

    def test_delete_missing_file_returns_404(self) -> None:
        resp = self.client.delete("/delete_specific_file/ground/gone.txt")
        self.assertEqual(resp.status_code, 404)

    # -----------------------------------------------------------------------
    # 14. Path traversal rejection
    # -----------------------------------------------------------------------

    def test_file_list_rejects_traversal(self) -> None:
        resp = self.client.get("/get_file_list/../../etc")
        # The path won't match ground/drill, so 422 from the regex
        self.assertIn(resp.status_code, (400, 404, 422))

    def test_download_rejects_dotdot_filename(self) -> None:
        resp = self.client.get("/download_file/ground/..%2F..%2Fetc%2Fpasswd")
        self.assertIn(resp.status_code, (400, 404))

    def test_delete_rejects_dotdot_filename(self) -> None:
        resp = self.client.delete(
            "/delete_specific_file/ground/..%2F..%2Fetc%2Fpasswd"
        )
        self.assertIn(resp.status_code, (400, 404))

    def test_download_rejects_path_separator_in_filename(self) -> None:
        resp = self.client.get("/download_file/ground/foo/bar.txt")
        # FastAPI may split on "/" giving wrong path segments -> 404 or 422
        self.assertIn(resp.status_code, (400, 404, 422))

    def test_delete_rejects_path_separator_in_filename(self) -> None:
        resp = self.client.delete("/delete_specific_file/ground/foo/bar.txt")
        self.assertIn(resp.status_code, (400, 404, 422))

    # -----------------------------------------------------------------------
    # 15. Downloadable files list
    # -----------------------------------------------------------------------

    def test_downloadable_files_ground_returns_list(self) -> None:
        (self.ground_dl_dir / "g1.json").write_text("{}")

        resp = self.client.get("/get_downloadable_files/ground")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["file_count"], 1)

    def test_downloadable_files_missing_dir_returns_empty(self) -> None:
        import shutil

        shutil.rmtree(self.drill_dl_dir, ignore_errors=True)

        resp = self.client.get("/get_downloadable_files/drill")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["file_count"], 0)

    # -----------------------------------------------------------------------
    # 16. No CORS middleware (removed for internal worker)
    # -----------------------------------------------------------------------

    def test_no_cors_middleware(self) -> None:
        """The app must not have CORSMiddleware configured."""
        from fastapi.middleware.cors import CORSMiddleware

        for middleware in self.app.user_middleware:
            if middleware.cls is CORSMiddleware:
                self.fail("CORSMiddleware should not be configured")

    # -----------------------------------------------------------------------
    # 17. store/pipeline injection -- default create_app() works without args
    # -----------------------------------------------------------------------

    def test_create_app_default_uses_env_db_path(self) -> None:
        """create_app() without arguments should construct a store from
        GRAPH_JOB_DB env var (or default) and a GraphPipeline."""
        from graphrag_api.main import create_app

        # Just verify it does not raise
        app = create_app()
        self.assertIsNotNone(app)


if __name__ == "__main__":
    unittest.main()
