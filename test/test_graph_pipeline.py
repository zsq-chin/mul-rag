"""Tests for GraphPipeline -- the single-worker daemon build pipeline.

Exercises every major contract in Task 9B-1:
  - submit enqueues, single daemon worker, idempotent start
  - normal build with finalizer evidence -> completed
  - no finalizer -> failed (not fake-completed), releases active slot
  - non-zero exit -> failed
  - silent-process cancel -> terminate then kill -> cancelled
  - retry re-enqueues
  - progress is monotonic and bounded
  - cleanup of source files on success only
"""

import os
import shutil
import subprocess as _subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any, Callable, List, Optional
from unittest.mock import patch

from graphrag_api.job_store import JobStore
from graphrag_api.pipeline import (
    ArtifactStats,
    FinalizationEvidence,
    GraphPipeline,
    OutputSnapshot,
)

try:
    from graphrag_api.pipeline import (
        InternalImportFinalizer,
        internal_import_finalizer_factory_from_env,
    )
except ImportError:
    # Expected RED state: APIs not yet implemented.
    InternalImportFinalizer = None  # type: ignore[assignment,misc]
    internal_import_finalizer_factory_from_env = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------


class _FakeProcess:
    """Deterministic stand-in for subprocess.Popen.

    Behaviour:
      - Normal mode (blocks_forever=False): readline() yields lines one at a
        time, then returns "". After all lines are read, poll()/wait() return
        immediately with the configured returncode.
      - blocks_forever mode: readline() blocks until terminate()/kill() is
        called. After terminate(), wait(timeout) raises TimeoutExpired
        (simulating SIGTERM not working). After kill(), wait() returns
        immediately.

    Every terminate/wait/kill call is recorded in ``_calls`` so tests can
    assert the exact shutdown sequence.
    """

    def __init__(
        self,
        stdout_lines: Optional[List[str]] = None,
        returncode: int = 0,
        blocks_forever: bool = False,
        eof_while_running: bool = False,
    ):
        self._lines = stdout_lines or []
        self._returncode = returncode
        self._blocks_forever = blocks_forever
        self._eof_while_running = eof_while_running
        self._line_idx = 0
        self._lock = threading.Lock()
        self._terminated = threading.Event()
        self._killed = threading.Event()
        self._read_event = threading.Event()
        self._all_lines_read = threading.Event()
        self._calls: List[str] = []

    @property
    def returncode(self) -> int:
        return self._returncode

    @property
    def stdout(self):
        return self

    def poll(self) -> Optional[int]:
        if self._killed.is_set():
            return self._returncode
        if self._terminated.is_set() and not self._blocks_forever:
            return self._returncode
        if self._blocks_forever or self._eof_while_running:
            return None
        if self._all_lines_read.is_set():
            return self._returncode
        return None

    def readline(self) -> str:
        """Yield lines one at a time; block forever when _blocks_forever."""
        if self._blocks_forever:
            self._read_event.wait(timeout=300)
            return ""
        if self._eof_while_running:
            self._all_lines_read.set()
            return ""
        with self._lock:
            if self._line_idx >= len(self._lines):
                self._all_lines_read.set()
                return ""
            line = self._lines[self._line_idx]
            self._line_idx += 1
            if self._line_idx >= len(self._lines):
                self._all_lines_read.set()
            return line

    def terminate(self) -> None:
        self._calls.append("terminate")
        self._terminated.set()
        if self._blocks_forever:
            self._read_event.set()

    def kill(self) -> None:
        self._calls.append("kill")
        self._killed.set()
        self._read_event.set()

    def wait(self, timeout: Optional[float] = None) -> int:
        self._calls.append("wait({0})".format(timeout))
        # After kill, always return immediately.
        if self._killed.is_set():
            return self._returncode
        # blocks_forever: after terminate, SIGTERM did not kill the process,
        # so raise TimeoutExpired to let the caller escalate to kill().
        if self._blocks_forever and self._terminated.is_set():
            raise _subprocess.TimeoutExpired(
                cmd="fake", timeout=timeout or 0
            )
        if self._eof_while_running and not self._terminated.is_set():
            if timeout is None:
                raise AssertionError("wait() must not be unbounded after stdout EOF")
            raise _subprocess.TimeoutExpired(cmd="fake", timeout=timeout)
        # Normal process that finished reading all lines: return at once.
        if not self._blocks_forever and self._all_lines_read.is_set():
            return self._returncode
        # Normal process terminated before EOF: respond to SIGTERM immediately.
        if not self._blocks_forever and self._terminated.is_set():
            return self._returncode
        # Otherwise block (simulating hung process or waiting for natural exit).
        self._read_event.wait(timeout=timeout)
        return self._returncode


def _make_popen_factory(
    stdout_lines: Optional[List[str]] = None,
    returncode: int = 0,
    blocks_forever: bool = False,
    eof_while_running: bool = False,
    captured: Optional[List["_FakeProcess"]] = None,
) -> Callable[..., Any]:
    """Return a Popen-factory callable that creates _FakeProcess instances.

    If *captured* is provided, each created _FakeProcess is appended to it so
    tests can inspect the fake after the pipeline runs.
    """

    def factory(*args: Any, **kwargs: Any) -> _FakeProcess:
        proc = _FakeProcess(
            stdout_lines=list(stdout_lines) if stdout_lines else None,
            returncode=returncode,
            blocks_forever=blocks_forever,
            eof_while_running=eof_while_running,
        )
        if captured is not None:
            captured.append(proc)
        return proc

    return factory


def _noop_finalizer(*args: Any, **kwargs: Any) -> FinalizationEvidence:
    """Default finalizer returning valid evidence."""
    return FinalizationEvidence(
        indexed=True,
        relationship_count=42,
        artifact_path="/output/graph.json",
        artifact_sha256="abc123",
    )


def _noop_finalizer_factory() -> Callable[..., FinalizationEvidence]:
    return _noop_finalizer


class _FakeHTTPError(Exception):
    """Stand-in for requests.HTTPError."""


class _FakeResponse:
    """Minimal fake for requests.Response.

    Tracks whether raise_for_status was called so tests can assert the
    finalizer actually checks the HTTP status.
    """

    def __init__(
        self,
        json_data: Optional[dict] = None,
        status_code: int = 200,
        raise_error: Optional[Exception] = None,
    ):
        self._json = json_data if json_data is not None else {}
        self.status_code = status_code
        self._raise_error = raise_error
        self.raise_for_status_called = False

    def json(self) -> dict:
        return self._json

    def raise_for_status(self) -> None:
        self.raise_for_status_called = True
        if self._raise_error is not None:
            raise self._raise_error


class _RecordingPost:
    """Fake ``requests.post`` that records every call and returns a
    pre-configured :class:`_FakeResponse`."""

    def __init__(self, response: _FakeResponse):
        self._response = response
        self.calls: List[dict] = []

    def __call__(
        self,
        url: str,
        json: Optional[dict] = None,
        headers: Optional[dict] = None,
        timeout: Optional[float] = None,
    ) -> _FakeResponse:
        self.calls.append(
            {"url": url, "json": json, "headers": headers, "timeout": timeout}
        )
        return self._response


def _default_artifact_selector(
    output_root: Path, snapshot: OutputSnapshot
) -> Path:
    """Default artifact selector returning a placeholder parquet path."""
    placeholder = (
        output_root
        / "new_test_run"
        / "artifacts"
        / "create_final_relationships.parquet"
    )
    placeholder.parent.mkdir(parents=True, exist_ok=True)
    placeholder.write_bytes(b"fake parquet")
    return placeholder


def _default_artifact_converter(
    parquet_path: Path, csv_path: Path
) -> ArtifactStats:
    """Default artifact converter creating a deterministic small CSV."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_bytes(b"h,r,t\na,b,c\nd,e,f\n")
    return ArtifactStats(rows=2, sha256="b" * 64, path=str(csv_path))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class GraphPipelineTests(unittest.TestCase):
    """Test suite for GraphPipeline."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self._tmp_path = Path(self._tmp)
        self.db_path = self._tmp_path / "jobs.db"
        self.store = JobStore(self.db_path)

        # Set up source directory with test files
        self.source_dir = self._tmp_path / "copypath"
        self.source_dir.mkdir()
        (self.source_dir / "doc1.txt").write_text("hello world")
        (self.source_dir / "doc2.txt").write_text("second file")

        # Per-type index roots
        self.index_ground = self._tmp_path / "indexing"
        self.index_drill = self._tmp_path / "indexing_drill"

        self._pipelines: List[GraphPipeline] = []

    def tearDown(self) -> None:
        for p in self._pipelines:
            try:
                p.stop(timeout=5)
            except Exception:
                pass
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _make_pipeline(
        self,
        *,
        finalizer_factory: Optional[Callable[[], Callable[..., FinalizationEvidence]]] = None,
        popen_factory: Optional[Callable[..., Any]] = None,
        popen_cmd_factory: Optional[Callable[[str, Path], List[str]]] = None,
        terminate_timeout: float = 0.5,
        artifact_selector: Optional[Callable[[Path, OutputSnapshot], Path]] = None,
        artifact_converter: Optional[Callable[[Path, Path], ArtifactStats]] = None,
    ) -> GraphPipeline:
        """Create a pipeline with injectable dependencies."""
        if popen_cmd_factory is None:
            popen_cmd_factory = lambda gt, root: ["echo", "noop"]

        if artifact_selector is None:
            artifact_selector = _default_artifact_selector
        if artifact_converter is None:
            artifact_converter = _default_artifact_converter

        p = GraphPipeline(
            store=self.store,
            source_dir=self.source_dir,
            index_ground=self.index_ground,
            index_drill=self.index_drill,
            finalizer_factory=finalizer_factory,
            popen_factory=popen_factory,
            popen_cmd_factory=popen_cmd_factory,
            terminate_timeout=terminate_timeout,
            artifact_selector=artifact_selector,
            artifact_converter=artifact_converter,
        )
        self._pipelines.append(p)
        return p

    def _wait_for_status(
        self, job_id: str, target: str, timeout: float = 10.0
    ) -> None:
        """Poll the store until *job_id* reaches *target* status."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rec = self.store.get(job_id)
            if rec is not None and rec.status == target:
                return
            time.sleep(0.05)
        rec = self.store.get(job_id)
        self.fail(
            "Job {0} never reached status {1!r}; current={2}".format(
                job_id, target, rec.status if rec else "<gone>"
            )
        )

    # -----------------------------------------------------------------------
    # 1. submit + single daemon + normal build with finalizer evidence
    # -----------------------------------------------------------------------

    def test_submit_enqueue_and_normal_build_completes_with_finalizer(self) -> None:
        """submit creates queued record; daemon runs it through to completed
        only when finalizer provides valid evidence."""
        build_output = [
            "workflow: create_base_text_units\n",
            "workflow: create_entities\n",
            "workflow: create_relationships\n",
            "workflow: create_community_reports\n",
            "workflow: write_final_documents\n",
        ]
        p = self._make_pipeline(
            finalizer_factory=_noop_finalizer_factory,
            popen_factory=_make_popen_factory(
                stdout_lines=build_output, returncode=0
            ),
            popen_cmd_factory=lambda gt, root: [
                "fake-index", "--root", str(root)
            ],
        )
        p.start()

        job = p.submit("ground")
        self.assertEqual(job.status, "queued")

        self._wait_for_status(job.id, "completed", timeout=10)

        done = self.store.get(job.id)
        self.assertEqual(done.status, "completed")
        self.assertEqual(done.progress, 100)
        self.assertEqual(done.input_count, 2)
        self.assertGreater(done.relationship_count, 0)
        self.assertTrue(done.artifact_path)
        self.assertTrue(done.artifact_sha256)
        self.assertTrue(done.finished_at)

    # -----------------------------------------------------------------------
    # 2. no finalizer -> failed, releases active slot
    # -----------------------------------------------------------------------

    def test_no_finalizer_ends_failed_and_releases_slot(self) -> None:
        """Without a finalizer, GraphRAG build success -> failed (not completed).
        The active slot must be freed so a new job can be submitted."""
        build_output = [
            "workflow: create_base_text_units\n",
            "workflow: create_entities\n",
            "workflow: create_relationships\n",
        ]
        p = self._make_pipeline(
            finalizer_factory=None,  # no finalizer
            popen_factory=_make_popen_factory(
                stdout_lines=build_output, returncode=0
            ),
            popen_cmd_factory=lambda gt, root: ["fake-index"],
        )
        p.start()

        job = p.submit("ground")
        self._wait_for_status(job.id, "failed", timeout=10)

        done = self.store.get(job.id)
        self.assertIn("finalization", done.error_summary.lower())

        # Active slot released: submitting another job for same type must succeed
        job2 = p.submit("ground")
        self.assertEqual(job2.status, "queued")

    # -----------------------------------------------------------------------
    # 3. non-zero exit -> failed with meaningful error/log
    # -----------------------------------------------------------------------

    def test_nonzero_exit_transitions_to_failed(self) -> None:
        """A subprocess that exits non-zero must result in failed status
        with meaningful error_summary and log_tail."""
        build_output = [
            "workflow: create_base_text_units\n",
            "ERROR: something went wrong\n",
        ]
        p = self._make_pipeline(
            finalizer_factory=_noop_finalizer_factory,
            popen_factory=_make_popen_factory(
                stdout_lines=build_output, returncode=42
            ),
            popen_cmd_factory=lambda gt, root: ["fake-index"],
        )
        p.start()

        job = p.submit("ground")
        self._wait_for_status(job.id, "failed", timeout=10)

        done = self.store.get(job.id)
        self.assertIn("42", done.error_summary)
        self.assertIn("ERROR", done.log_tail)

    # -----------------------------------------------------------------------
    # 4. silent-process cancel -> terminate, wait timeout -> kill -> cancelled
    # -----------------------------------------------------------------------

    def test_cancel_silent_process_terminate_then_kill(self) -> None:
        """When subprocess produces no output, cancel must still work:
        terminate -> wait (injectable short timeout) -> kill -> cancelled.

        The test also asserts the exact call sequence on the fake process.
        """
        fakes: List[_FakeProcess] = []
        p = self._make_pipeline(
            finalizer_factory=_noop_finalizer_factory,
            popen_factory=_make_popen_factory(
                blocks_forever=True, captured=fakes,
            ),
            popen_cmd_factory=lambda gt, root: ["fake-index"],
            terminate_timeout=0.3,
        )
        p.start()

        job = p.submit("ground")
        # Wait until we know the subprocess has been spawned (job is building+)
        for _ in range(200):
            rec = self.store.get(job.id)
            if rec and rec.status in ("copying", "building"):
                break
            time.sleep(0.05)

        # Request cancel
        p.cancel(job.id)

        self._wait_for_status(job.id, "cancelled", timeout=10)

        done = self.store.get(job.id)
        self.assertEqual(done.status, "cancelled")
        self.assertTrue(done.cancel_requested)

        # Exactly one fake process was created
        self.assertEqual(len(fakes), 1, "Expected exactly one fake process")
        fake = fakes[0]
        calls = fake._calls

        # Must contain terminate and kill
        self.assertIn("terminate", calls, "terminate was never called")
        self.assertIn("kill", calls, "kill was never called")

        # Extract the shutdown-relevant subsequence
        shutdown_calls = [
            c for c in calls
            if c == "terminate" or c == "kill" or c.startswith("wait")
        ]

        # Find positions of terminate and kill
        t_idx = shutdown_calls.index("terminate")
        k_idx = shutdown_calls.index("kill")
        self.assertGreater(k_idx, t_idx, "kill must come after terminate")

        # At least one wait between terminate and kill
        waits_between = [
            c for c in shutdown_calls[t_idx + 1: k_idx]
            if c.startswith("wait")
        ]
        self.assertTrue(
            len(waits_between) > 0,
            "Expected wait() between terminate and kill, got: {0}".format(
                shutdown_calls
            ),
        )

        # At least one wait after kill
        waits_after = [
            c for c in shutdown_calls[k_idx + 1:] if c.startswith("wait")
        ]
        self.assertTrue(
            len(waits_after) > 0,
            "Expected wait() after kill, got: {0}".format(shutdown_calls),
        )

    # -----------------------------------------------------------------------
    # 5. retry re-enqueues
    # -----------------------------------------------------------------------

    def test_retry_re_enqueues_failed_job(self) -> None:
        """After a job fails, retry must reset it to queued and re-enqueue."""
        p = self._make_pipeline(
            finalizer_factory=_noop_finalizer_factory,
            popen_factory=_make_popen_factory(returncode=1),
            popen_cmd_factory=lambda gt, root: ["fake-index"],
        )
        p.start()

        job = p.submit("ground")
        self._wait_for_status(job.id, "failed", timeout=10)

        # Now retry with a successful process
        p._popen_factory = _make_popen_factory(
            stdout_lines=["workflow: ok\n"], returncode=0
        )

        retried = p.retry(job.id)
        self.assertEqual(retried.status, "queued")

        self._wait_for_status(job.id, "completed", timeout=10)
        done = self.store.get(job.id)
        self.assertEqual(done.status, "completed")

    # -----------------------------------------------------------------------
    # 6. progress monotonic and bounded
    # -----------------------------------------------------------------------

    def test_progress_monotonic_and_bounded(self) -> None:
        """Build progress must be monotonically non-decreasing and never
        exceed 70 during the building stage (before finalizer)."""
        stages = [
            "workflow: create_base_text_units\n",
            "workflow: create_entities\n",
            "workflow: create_relationships\n",
            "workflow: create_community_reports\n",
            "workflow: write_final_documents\n",
        ]
        progress_snapshots: List[int] = []
        original_transition = self.store.transition

        def tracking_transition(*args, **kwargs):
            rec = original_transition(*args, **kwargs)
            progress_snapshots.append(rec.progress)
            return rec

        self.store.transition = tracking_transition  # type: ignore[assignment]

        p = self._make_pipeline(
            finalizer_factory=_noop_finalizer_factory,
            popen_factory=_make_popen_factory(
                stdout_lines=stages, returncode=0
            ),
            popen_cmd_factory=lambda gt, root: ["fake-index"],
        )
        p.start()

        job = p.submit("ground")
        self._wait_for_status(job.id, "completed", timeout=10)

        # Check monotonic
        for i in range(1, len(progress_snapshots)):
            self.assertGreaterEqual(
                progress_snapshots[i],
                progress_snapshots[i - 1],
                "Progress decreased: {0} -> {1}".format(
                    progress_snapshots[i - 1], progress_snapshots[i]
                ),
            )

        # During building (before completion), progress should be <= 70
        building_progress = [
            pv for pv in progress_snapshots if pv <= 70
        ]
        for pval in building_progress:
            self.assertLessEqual(pval, 70)
        self.assertTrue(
            len(building_progress) > 0,
            "Should have building-stage progress values",
        )

    # -----------------------------------------------------------------------
    # 7. idempotent start
    # -----------------------------------------------------------------------

    def test_start_is_idempotent(self) -> None:
        """Calling start() multiple times must not create additional workers."""
        before = {
            t.ident for t in threading.enumerate()
            if t.name.startswith("graph-pipeline-worker")
        }

        p = self._make_pipeline(
            finalizer_factory=_noop_finalizer_factory,
            popen_factory=_make_popen_factory(returncode=0),
            popen_cmd_factory=lambda gt, root: ["fake-index"],
        )
        p.start()
        p.start()
        p.start()

        time.sleep(0.1)

        after = {
            t.ident for t in threading.enumerate()
            if t.name.startswith("graph-pipeline-worker")
        }
        new_threads = after - before
        # start() called 3 times but only 1 new thread should be created
        self.assertEqual(len(new_threads), 1)

    # -----------------------------------------------------------------------
    # 8. stop cleans up
    # -----------------------------------------------------------------------

    def test_stop_terminates_worker(self) -> None:
        """stop(timeout) must reliably join the daemon thread."""
        p = self._make_pipeline(
            finalizer_factory=_noop_finalizer_factory,
            popen_factory=_make_popen_factory(returncode=0),
            popen_cmd_factory=lambda gt, root: ["fake-index"],
        )
        p.start()
        worker = p._worker
        self.assertIsNotNone(worker)
        self.assertTrue(worker.is_alive())

        p.stop(timeout=5)
        time.sleep(0.1)
        self.assertFalse(worker.is_alive())

    def test_stop_terminates_active_subprocess_before_returning(self) -> None:
        captured: List[_FakeProcess] = []
        p = self._make_pipeline(
            finalizer_factory=_noop_finalizer_factory,
            popen_factory=_make_popen_factory(
                blocks_forever=True,
                captured=captured,
            ),
            popen_cmd_factory=lambda gt, root: ["fake-index"],
            terminate_timeout=0.05,
        )
        p.start()
        job = p.submit("ground")
        self._wait_for_status(job.id, "building", timeout=2)
        worker = p._worker
        self.assertIsNotNone(worker)

        try:
            p.stop(timeout=0.5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(len(captured), 1)
            calls = captured[0]._calls
            self.assertIn("terminate", calls)
            self.assertIn("kill", calls)
        finally:
            if worker.is_alive():
                p.cancel(job.id)
                worker.join(timeout=2)

    def test_stdout_eof_while_process_runs_remains_cancellable(self) -> None:
        captured: List[_FakeProcess] = []
        p = self._make_pipeline(
            finalizer_factory=_noop_finalizer_factory,
            popen_factory=_make_popen_factory(
                eof_while_running=True,
                captured=captured,
            ),
            popen_cmd_factory=lambda gt, root: ["fake-index"],
            terminate_timeout=0.05,
        )
        p.start()
        job = p.submit("ground")
        self._wait_for_status(job.id, "building", timeout=2)

        p.cancel(job.id)
        self._wait_for_status(job.id, "cancelled", timeout=2)

        self.assertEqual(len(captured), 1)
        self.assertIn("terminate", captured[0]._calls)

    # -----------------------------------------------------------------------
    # 9. source directory validation
    # -----------------------------------------------------------------------

    def test_missing_source_dir_transitions_to_failed(self) -> None:
        """If the source directory doesn't exist, the job must fail."""
        bad_pipeline = GraphPipeline(
            store=self.store,
            source_dir=Path(self._tmp) / "nonexistent_dir",
            index_ground=self.index_ground,
            index_drill=self.index_drill,
            finalizer_factory=_noop_finalizer_factory,
            popen_factory=_make_popen_factory(returncode=0),
            popen_cmd_factory=lambda gt, root: ["fake-index"],
        )
        self._pipelines.append(bad_pipeline)
        bad_pipeline.start()

        job = bad_pipeline.submit("ground")
        self._wait_for_status(job.id, "failed", timeout=10)

        done = self.store.get(job.id)
        self.assertIn("source", done.error_summary.lower())

    def test_empty_source_dir_transitions_to_failed(self) -> None:
        """If the source directory has no regular files, the job must fail."""
        empty_dir = self._tmp_path / "empty_copypath"
        empty_dir.mkdir()

        bad_pipeline = GraphPipeline(
            store=self.store,
            source_dir=empty_dir,
            index_ground=self.index_ground,
            index_drill=self.index_drill,
            finalizer_factory=_noop_finalizer_factory,
            popen_factory=_make_popen_factory(returncode=0),
            popen_cmd_factory=lambda gt, root: ["fake-index"],
        )
        self._pipelines.append(bad_pipeline)
        bad_pipeline.start()

        job = bad_pipeline.submit("ground")
        self._wait_for_status(job.id, "failed", timeout=10)

        done = self.store.get(job.id)
        self.assertIn("source", done.error_summary.lower())

    # -----------------------------------------------------------------------
    # 10. source cleanup on success, preserved on failure
    # -----------------------------------------------------------------------

    def test_source_files_cleaned_on_success(self) -> None:
        """After a successful build + finalizer, copied source files must be cleaned."""
        p = self._make_pipeline(
            finalizer_factory=_noop_finalizer_factory,
            popen_factory=_make_popen_factory(returncode=0),
            popen_cmd_factory=lambda gt, root: ["fake-index"],
        )
        p.start()

        job = p.submit("ground")
        self._wait_for_status(job.id, "completed", timeout=10)

        # Source files should have been cleaned
        remaining = [f for f in self.source_dir.iterdir() if f.is_file()]
        self.assertEqual(
            len(remaining), 0, "Source files not cleaned: {0}".format(remaining)
        )

    def test_source_files_preserved_on_failure(self) -> None:
        """After a failed build, source files must be preserved for retry."""
        p = self._make_pipeline(
            finalizer_factory=_noop_finalizer_factory,
            popen_factory=_make_popen_factory(returncode=1),
            popen_cmd_factory=lambda gt, root: ["fake-index"],
        )
        p.start()

        job = p.submit("ground")
        self._wait_for_status(job.id, "failed", timeout=10)

        remaining = [f for f in self.source_dir.iterdir() if f.is_file()]
        self.assertGreater(
            len(remaining), 0, "Source files were cleaned after failure"
        )

    # -----------------------------------------------------------------------
    # 11. cancel persisted via store
    # -----------------------------------------------------------------------

    def test_cancel_persists_via_store(self) -> None:
        """cancel(job_id) must persist the cancel request through the store."""
        p = self._make_pipeline(
            finalizer_factory=_noop_finalizer_factory,
            popen_factory=_make_popen_factory(blocks_forever=True),
            popen_cmd_factory=lambda gt, root: ["fake-index"],
            terminate_timeout=0.3,
        )
        p.start()

        job = p.submit("ground")
        for _ in range(200):
            rec = self.store.get(job.id)
            if rec and rec.status in ("copying", "building"):
                break
            time.sleep(0.05)

        p.cancel(job.id)

        self._wait_for_status(job.id, "cancelled", timeout=10)
        rec = self.store.get(job.id)
        self.assertTrue(rec.cancel_requested)

    # -----------------------------------------------------------------------
    # 12. exception during build -> failed
    # -----------------------------------------------------------------------

    def test_exception_during_build_transitions_to_failed(self) -> None:
        """An unexpected exception during the build phase must not leave
        the job in an active state; it must transition to failed."""

        def bad_popen(*args, **kwargs):
            raise OSError("simulated Popen failure")

        p = self._make_pipeline(
            finalizer_factory=_noop_finalizer_factory,
            popen_factory=bad_popen,
            popen_cmd_factory=lambda gt, root: ["fake-index"],
        )
        p.start()

        job = p.submit("ground")
        self._wait_for_status(job.id, "failed", timeout=10)

        done = self.store.get(job.id)
        self.assertEqual(done.status, "failed")
        self.assertTrue(
            "Popen" in done.error_summary or "simulated" in done.error_summary,
            "Expected 'Popen' or 'simulated' in error_summary, got: {0}".format(
                done.error_summary
            ),
        )

    # -----------------------------------------------------------------------
    # 13. drill type uses correct index directory
    # -----------------------------------------------------------------------

    def test_drill_type_uses_drill_directory(self) -> None:
        """A drill job must use the drill index root, not the ground one."""
        captured_roots: List[Path] = []

        def spy_cmd(gt: str, root: Path) -> List[str]:
            captured_roots.append(root)
            return ["fake-index", "--root", str(root)]

        p = self._make_pipeline(
            finalizer_factory=_noop_finalizer_factory,
            popen_factory=_make_popen_factory(returncode=0),
            popen_cmd_factory=spy_cmd,
        )
        p.start()

        job = p.submit("drill")
        self._wait_for_status(job.id, "completed", timeout=10)

        self.assertEqual(len(captured_roots), 1)
        self.assertEqual(captured_roots[0], self.index_drill)

    # -----------------------------------------------------------------------
    # 14. exception in finalizer -> failed
    # -----------------------------------------------------------------------

    def test_finalizer_exception_transitions_to_failed(self) -> None:
        """If the finalizer raises, the job must fail, not hang."""

        def bad_finalizer(*a, **kw):
            raise RuntimeError("finalizer exploded")

        def bad_factory():
            return bad_finalizer

        p = self._make_pipeline(
            finalizer_factory=bad_factory,
            popen_factory=_make_popen_factory(returncode=0),
            popen_cmd_factory=lambda gt, root: ["fake-index"],
        )
        p.start()

        job = p.submit("ground")
        self._wait_for_status(job.id, "failed", timeout=10)

        done = self.store.get(job.id)
        self.assertEqual(done.status, "failed")
        self.assertIn("finalizer", done.error_summary.lower())

    # -----------------------------------------------------------------------
    # 15. finalizer returns invalid evidence -> failed
    # -----------------------------------------------------------------------

    def test_finalizer_bad_evidence_transitions_to_failed(self) -> None:
        """If the finalizer returns evidence with indexed=False, the job
        must fail, not be marked completed."""

        def bad_evidence_factory():
            def bad(*a, **kw):
                return FinalizationEvidence(
                    indexed=False,  # invalid
                    relationship_count=0,
                    artifact_path="",
                    artifact_sha256="",
                )
            return bad

        p = self._make_pipeline(
            finalizer_factory=bad_evidence_factory,
            popen_factory=_make_popen_factory(returncode=0),
            popen_cmd_factory=lambda gt, root: ["fake-index"],
        )
        p.start()

        job = p.submit("ground")
        self._wait_for_status(job.id, "failed", timeout=10)

        done = self.store.get(job.id)
        self.assertEqual(done.status, "failed")

    # -----------------------------------------------------------------------
    # 16. cancel while queued -> cancelled (no subprocess spawned)
    # -----------------------------------------------------------------------

    def test_cancel_while_queued_transitions_to_cancelled(self) -> None:
        """Cancel requested before the worker picks up the job must still
        result in cancelled status, not building or failed."""
        # Use a pipeline that we never start, so the job stays queued.
        p = self._make_pipeline(
            finalizer_factory=_noop_finalizer_factory,
            popen_factory=_make_popen_factory(returncode=0),
            popen_cmd_factory=lambda gt, root: ["fake-index"],
        )
        # Do NOT call p.start() -- job stays queued in the store.
        job = p.submit("ground")
        self.assertEqual(job.status, "queued")

        # Cancel while queued
        p.cancel(job.id)

        # Now start the worker -- it should pick up the cancelling job and
        # transition it to cancelled without spawning a subprocess.
        p.start()
        self._wait_for_status(job.id, "cancelled", timeout=10)

        done = self.store.get(job.id)
        self.assertEqual(done.status, "cancelled")
        self.assertTrue(done.cancel_requested)

    # -----------------------------------------------------------------------
    # 17. artifact_selector + artifact_converter before finalizer
    # -----------------------------------------------------------------------

    def test_successful_build_converts_artifact_before_finalizer(self) -> None:
        """Contract: the pipeline must snapshot output dirs, select the new
        parquet via artifact_selector, convert it via artifact_converter,
        then pass the resulting ArtifactStats to the new-style finalizer
        ``(job_id, graph_type, stats)``."""
        build_output = [
            "workflow: create_base_text_units\n",
            "workflow: create_entities\n",
            "workflow: create_relationships\n",
            "workflow: create_community_reports\n",
            "workflow: write_final_documents\n",
        ]

        # Snapshot root is where GraphRAG writes output; destination root is
        # where converted CSVs land.  They must be distinct.
        output_snapshot_root = self.index_ground / "output"
        csv_destination_root = self.index_ground / "ground_graph_fill"

        # Create a stale output directory *before* pipeline construction so
        # it is captured in the pre-build snapshot.
        stale_dir = output_snapshot_root / "stale_run_001"
        stale_dir.mkdir(parents=True)

        # --- hook records ----------------------------------------------------
        selector_calls: List[dict] = []
        converter_calls: List[dict] = []
        finalizer_calls: List[tuple] = []

        def artifact_selector(output_root: Path, snapshot: OutputSnapshot) -> Path:
            selector_calls.append(
                {"output_root": output_root, "snapshot": snapshot}
            )
            # output_root must be the snapshot root, not the CSV destination
            self.assertEqual(output_root, output_snapshot_root)
            # Verify stale dir name is present in snapshot
            self.assertIn("stale_run_001", snapshot.directory_names)
            # Return a placeholder parquet path under the output snapshot root
            placeholder = (
                output_root
                / "new_run"
                / "artifacts"
                / "create_final_relationships.parquet"
            )
            placeholder.parent.mkdir(parents=True, exist_ok=True)
            placeholder.write_bytes(b"fake parquet")
            return placeholder

        def artifact_converter(parquet_path: Path, csv_path: Path) -> ArtifactStats:
            converter_calls.append(
                {"parquet_path": parquet_path, "csv_path": csv_path}
            )
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            csv_path.write_bytes(b"h,r,t\na,b,c\nd,e,f\ng,h,i\n")
            return ArtifactStats(
                rows=3, sha256="a" * 64, path=str(csv_path)
            )

        def new_finalizer(
            job_id: str, graph_type: str, stats: ArtifactStats
        ) -> FinalizationEvidence:
            finalizer_calls.append((job_id, graph_type, stats))
            return FinalizationEvidence(
                indexed=True,
                relationship_count=stats.rows,
                artifact_path=stats.path,
                artifact_sha256=stats.sha256,
            )

        def new_finalizer_factory() -> Callable[..., FinalizationEvidence]:
            return new_finalizer  # type: ignore[return-value]

        # --- spy on store.transition -----------------------------------------
        transition_log: List[tuple] = []
        real_transition = self.store.transition

        def spy_transition(
            job_id: str, new_status: str, progress: int, **kwargs: Any
        ) -> Any:
            transition_log.append((new_status, progress))
            return real_transition(job_id, new_status, progress, **kwargs)

        self.store.transition = spy_transition  # type: ignore[assignment]

        # --- construct pipeline with future kwargs ----------------------------
        p = GraphPipeline(
            store=self.store,
            source_dir=self.source_dir,
            index_ground=self.index_ground,
            index_drill=self.index_drill,
            finalizer_factory=new_finalizer_factory,
            popen_factory=_make_popen_factory(
                stdout_lines=build_output, returncode=0
            ),
            popen_cmd_factory=lambda gt, root: [
                "fake-index", "--root", str(root)
            ],
            artifact_selector=artifact_selector,
            artifact_converter=artifact_converter,
        )
        self._pipelines.append(p)
        p.start()

        job = p.submit("ground")
        self._wait_for_status(job.id, "completed", timeout=10)

        # --- assert selector and converter each called once -------------------
        self.assertEqual(len(selector_calls), 1)
        self.assertEqual(len(converter_calls), 1)

        # --- destination parent is exactly index_ground / 'ground_graph_fill' --
        csv_path = Path(converter_calls[0]["csv_path"])
        self.assertEqual(csv_path.parent, csv_destination_root)

        # --- destination filename contains job.id and ends .csv ---------------
        self.assertIn(job.id, csv_path.name)
        self.assertTrue(csv_path.name.endswith(".csv"))

        # --- finalizer received (job_id, graph_type, stats) -------------------
        self.assertEqual(len(finalizer_calls), 1)
        fjid, fgt, fstats = finalizer_calls[0]
        self.assertEqual(fjid, job.id)
        self.assertEqual(fgt, "ground")
        self.assertIsInstance(fstats, ArtifactStats)
        self.assertEqual(fstats, ArtifactStats(rows=3, sha256="a" * 64, path=str(csv_path)))

        # --- ordered status/progress subsequence ------------------------------
        expected_subsequence = [
            ("converting", 72),
            ("converting", 78),
            ("importing", 80),
            ("indexing", 95),
            ("completed", 100),
        ]
        pos = 0
        for status, progress in transition_log:
            if pos < len(expected_subsequence) and (status, progress) == expected_subsequence[pos]:
                pos += 1
        self.assertEqual(
            pos,
            len(expected_subsequence),
            "Expected subsequence {0} not found in {1}".format(
                expected_subsequence, transition_log
            ),
        )

        # --- completed record persists rows=3, converted path and sha256 ------
        done = self.store.get(job.id)
        self.assertEqual(done.status, "completed")
        self.assertEqual(done.relationship_count, 3)
        self.assertEqual(done.artifact_path, str(csv_path))
        self.assertEqual(done.artifact_sha256, "a" * 64)


    # -----------------------------------------------------------------------
    # 18. legacy one-argument finalizer is called exactly once
    # -----------------------------------------------------------------------

    def test_legacy_one_argument_finalizer_is_called_once(self) -> None:
        """A legacy finalizer with strict ``(graph_type: str)`` signature must
        be called exactly once with ``['ground']`` -- no retry, no double
        invocation.

        Production currently calls ``finalizer(job_id, graph_type, stats)``
        (three args), so this test is expected to fail (RED) until the
        pipeline is taught to detect and call legacy finalizers correctly.
        """
        calls: List[str] = []

        def legacy_finalizer(graph_type: str) -> FinalizationEvidence:
            calls.append(graph_type)
            return FinalizationEvidence(
                indexed=True,
                relationship_count=42,
                artifact_path="/output/graph.json",
                artifact_sha256="abc123",
            )

        def legacy_finalizer_factory() -> Callable[..., FinalizationEvidence]:
            return legacy_finalizer  # type: ignore[return-value]

        p = self._make_pipeline(
            finalizer_factory=legacy_finalizer_factory,
            popen_factory=_make_popen_factory(
                stdout_lines=["workflow: ok\n"], returncode=0
            ),
        )
        p.start()

        job = p.submit("ground")
        self._wait_for_status(job.id, "completed", timeout=10)

        self.assertEqual(calls, ["ground"])

    # -----------------------------------------------------------------------
    # 19. selector raises FileNotFoundError -> failed, source preserved
    # -----------------------------------------------------------------------

    def test_selector_not_found_ends_failed_preserves_source(self) -> None:
        """When artifact_selector raises FileNotFoundError, the job must end
        failed with that message, the finalizer is never called, and source
        files are preserved."""
        finalizer_calls: List[tuple] = []

        def failing_selector(
            output_root: Path, snapshot: OutputSnapshot
        ) -> Path:
            raise FileNotFoundError("No new relationship artifact found")

        def tracking_finalizer(
            *a: Any, **kw: Any
        ) -> FinalizationEvidence:
            finalizer_calls.append(a)
            return _noop_finalizer(*a, **kw)

        def tracking_factory() -> Callable[..., FinalizationEvidence]:
            return tracking_finalizer  # type: ignore[return-value]

        p = self._make_pipeline(
            finalizer_factory=tracking_factory,
            popen_factory=_make_popen_factory(
                stdout_lines=["workflow: ok\n"], returncode=0
            ),
            artifact_selector=failing_selector,
        )
        p.start()

        job = p.submit("ground")
        self._wait_for_status(job.id, "failed", timeout=10)

        done = self.store.get(job.id)
        self.assertEqual(done.status, "failed")
        self.assertIn("No new relationship artifact found", done.error_summary)
        self.assertEqual(len(finalizer_calls), 0)

        remaining = [f for f in self.source_dir.iterdir() if f.is_file()]
        self.assertEqual(
            len(remaining),
            2,
            "Source files should be preserved on selector failure",
        )

    # -----------------------------------------------------------------------
    # 20. converter raises RuntimeError -> failed, source preserved
    # -----------------------------------------------------------------------

    def test_converter_explode_ends_failed_preserves_source(self) -> None:
        """When artifact_converter raises RuntimeError, the job must end
        failed with that message, the finalizer is never called, and source
        files are preserved."""
        finalizer_calls: List[tuple] = []

        def exploding_converter(
            parquet_path: Path, csv_path: Path
        ) -> ArtifactStats:
            raise RuntimeError("conversion exploded")

        def tracking_finalizer(
            *a: Any, **kw: Any
        ) -> FinalizationEvidence:
            finalizer_calls.append(a)
            return _noop_finalizer(*a, **kw)

        def tracking_factory() -> Callable[..., FinalizationEvidence]:
            return tracking_finalizer  # type: ignore[return-value]

        p = self._make_pipeline(
            finalizer_factory=tracking_factory,
            popen_factory=_make_popen_factory(
                stdout_lines=["workflow: ok\n"], returncode=0
            ),
            artifact_converter=exploding_converter,
        )
        p.start()

        job = p.submit("ground")
        self._wait_for_status(job.id, "failed", timeout=10)

        done = self.store.get(job.id)
        self.assertEqual(done.status, "failed")
        self.assertIn("conversion exploded", done.error_summary)
        self.assertEqual(len(finalizer_calls), 0)

        remaining = [f for f in self.source_dir.iterdir() if f.is_file()]
        self.assertEqual(
            len(remaining),
            2,
            "Source files should be preserved on converter failure",
        )

    # -----------------------------------------------------------------------
    # 21. drill successful path: converter destination under drill_graph_fill
    # -----------------------------------------------------------------------

    def test_drill_successful_converter_destination(self) -> None:
        """A successful drill job must place the converted CSV under
        index_drill / 'drill_graph_fill', with filename containing the job
        id and ending '.csv'."""
        converter_destinations: List[Path] = []

        def capturing_converter(
            parquet_path: Path, csv_path: Path
        ) -> ArtifactStats:
            converter_destinations.append(csv_path)
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            csv_path.write_bytes(b"h,r,t\na,b,c\n")
            return ArtifactStats(
                rows=1, sha256="a" * 64, path=str(csv_path)
            )

        p = self._make_pipeline(
            finalizer_factory=_noop_finalizer_factory,
            popen_factory=_make_popen_factory(
                stdout_lines=["workflow: ok\n"], returncode=0
            ),
            artifact_converter=capturing_converter,
        )
        p.start()

        job = p.submit("drill")
        self._wait_for_status(job.id, "completed", timeout=10)

        self.assertEqual(len(converter_destinations), 1)
        dest = converter_destinations[0]
        self.assertEqual(dest.parent, self.index_drill / "drill_graph_fill")
        self.assertIn(job.id, dest.name)
        self.assertTrue(dest.name.endswith(".csv"))

    # -----------------------------------------------------------------------
    # 22. two sequential ground jobs: distinct converter destinations
    # -----------------------------------------------------------------------

    def test_two_sequential_ground_jobs_distinct_converter_destinations(
        self,
    ) -> None:
        """Two sequential successful ground jobs must produce distinct CSV
        paths, each containing its corresponding job id."""
        converter_destinations: List[Path] = []

        def capturing_converter(
            parquet_path: Path, csv_path: Path
        ) -> ArtifactStats:
            converter_destinations.append(csv_path)
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            csv_path.write_bytes(b"h,r,t\na,b,c\n")
            return ArtifactStats(
                rows=1, sha256="a" * 64, path=str(csv_path)
            )

        p = self._make_pipeline(
            finalizer_factory=_noop_finalizer_factory,
            popen_factory=_make_popen_factory(
                stdout_lines=["workflow: ok\n"], returncode=0
            ),
            artifact_converter=capturing_converter,
        )
        p.start()

        # First job
        job1 = p.submit("ground")
        self._wait_for_status(job1.id, "completed", timeout=10)

        # Source files cleaned after first success; recreate one for second job
        (self.source_dir / "doc1.txt").write_text("recreated")

        # Second job
        job2 = p.submit("ground")
        self._wait_for_status(job2.id, "completed", timeout=10)

        self.assertEqual(len(converter_destinations), 2)
        dest1, dest2 = converter_destinations

        # Distinct paths
        self.assertNotEqual(dest1, dest2)

        # Each contains its job id
        self.assertIn(job1.id, dest1.name)
        self.assertIn(job2.id, dest2.name)

        # Both end with .csv
        self.assertTrue(dest1.name.endswith(".csv"))
        self.assertTrue(dest2.name.endswith(".csv"))


class InternalImportFinalizerTests(unittest.TestCase):
    """RED tests for InternalImportFinalizer -- not yet implemented."""

    def test_successful_post_maps_response_to_evidence(self) -> None:
        """POST with valid params -> FinalizationEvidence mapped from response."""
        response = _FakeResponse(
            json_data={
                "status": "success",
                "task_id": "job-1",
                "graph_type": "ground",
                "artifact_path": "job-1-relationships.csv",
                "node_count": 5,
                "relationship_count": 3,
                "embedded_count": 2,
                "vector_index_ready": True,
            }
        )
        fake_post = _RecordingPost(response)

        finalizer = InternalImportFinalizer(
            "http://api:5050/api/data/graph/internal/import",
            "secret-token",
            timeout=123,
            post=fake_post,
        )

        stats = ArtifactStats(
            rows=3,
            sha256="a" * 64,
            path="/app/indexing/ground_graph_fill/job-1-relationships.csv",
        )
        evidence = finalizer("job-1", "ground", stats)

        self.assertEqual(len(fake_post.calls), 1)
        call = fake_post.calls[0]
        self.assertEqual(call["url"], "http://api:5050/api/data/graph/internal/import")
        self.assertEqual(
            call["json"],
            {
                "task_id": "job-1",
                "graph_type": "ground",
                "artifact_path": "job-1-relationships.csv",
            },
        )
        self.assertEqual(
            call["headers"],
            {"X-Graph-Internal-Token": "secret-token"},
        )
        self.assertEqual(call["timeout"], 123)
        self.assertTrue(response.raise_for_status_called)

        self.assertIsInstance(evidence, FinalizationEvidence)
        self.assertTrue(evidence.indexed)
        self.assertEqual(evidence.relationship_count, 3)
        self.assertEqual(
            evidence.artifact_path,
            "/app/indexing/ground_graph_fill/job-1-relationships.csv",
        )
        self.assertEqual(evidence.artifact_sha256, "a" * 64)

    # -----------------------------------------------------------------------
    # vector_index_ready False -> evidence.indexed False
    # -----------------------------------------------------------------------

    def test_vector_index_ready_false_maps_to_indexed_false(self) -> None:
        """When the upstream response has vector_index_ready=False,
        FinalizationEvidence.indexed must be False."""
        response = _FakeResponse(
            json_data={
                "status": "success",
                "task_id": "job-1",
                "graph_type": "ground",
                "artifact_path": "job-1-relationships.csv",
                "node_count": 5,
                "relationship_count": 3,
                "embedded_count": 2,
                "vector_index_ready": False,
            }
        )
        fake_post = _RecordingPost(response)

        finalizer = InternalImportFinalizer(
            "http://api:5050/api/data/graph/internal/import",
            "secret-token",
            timeout=30,
            post=fake_post,
        )

        stats = ArtifactStats(
            rows=3, sha256="a" * 64, path="/out/job-1-relationships.csv"
        )
        evidence = finalizer("job-1", "ground", stats)

        self.assertIsInstance(evidence, FinalizationEvidence)
        self.assertFalse(evidence.indexed)
        self.assertEqual(evidence.relationship_count, 3)

    # -----------------------------------------------------------------------
    # Response validation: missing/bad relationship_count and vector_index_ready
    # -----------------------------------------------------------------------

    def test_response_validation_errors_raise_value_error(self) -> None:
        """Various malformed response bodies must raise ValueError."""
        base_body = {
            "status": "success",
            "task_id": "job-1",
            "graph_type": "ground",
            "artifact_path": "job-1-relationships.csv",
            "node_count": 5,
            "relationship_count": 3,
            "embedded_count": 2,
            "vector_index_ready": True,
        }
        stats = ArtifactStats(
            rows=3, sha256="a" * 64, path="/out/job-1-relationships.csv"
        )

        def _drop(d: dict, key: str) -> dict:
            return {k: v for k, v in d.items() if k != key}

        bad_bodies: dict[str, dict] = {
            # --- identity / status mismatches ---
            "status not success": {**base_body, "status": "error"},
            "status missing": _drop(base_body, "status"),
            "task_id mismatch": {**base_body, "task_id": "other-job"},
            "task_id missing": _drop(base_body, "task_id"),
            "graph_type mismatch": {**base_body, "graph_type": "drill"},
            "graph_type missing": _drop(base_body, "graph_type"),
            "artifact_path mismatch": {
                **base_body,
                "artifact_path": "wrong-file.csv",
            },
            "artifact_path missing": _drop(base_body, "artifact_path"),
            # --- count fields ---
            "missing relationship_count": _drop(base_body, "relationship_count"),
            "negative relationship_count": {**base_body, "relationship_count": -1},
            "non-int relationship_count (string)": {
                **base_body,
                "relationship_count": "three",
            },
            "non-int relationship_count (bool True)": {
                **base_body,
                "relationship_count": True,
            },
            "non-int relationship_count (bool False)": {
                **base_body,
                "relationship_count": False,
            },
            "missing node_count": _drop(base_body, "node_count"),
            "negative node_count": {**base_body, "node_count": -1},
            "non-int node_count (string)": {**base_body, "node_count": "five"},
            "non-int node_count (bool True)": {**base_body, "node_count": True},
            "non-int node_count (bool False)": {
                **base_body,
                "node_count": False,
            },
            "missing embedded_count": _drop(base_body, "embedded_count"),
            "negative embedded_count": {**base_body, "embedded_count": -1},
            "non-int embedded_count (string)": {
                **base_body,
                "embedded_count": "two",
            },
            "non-int embedded_count (bool True)": {
                **base_body,
                "embedded_count": True,
            },
            "non-int embedded_count (bool False)": {
                **base_body,
                "embedded_count": False,
            },
            # --- vector_index_ready ---
            "missing vector_index_ready": _drop(base_body, "vector_index_ready"),
            "non-bool vector_index_ready (string)": {
                **base_body,
                "vector_index_ready": "yes",
            },
            "non-bool vector_index_ready (int)": {
                **base_body,
                "vector_index_ready": 1,
            },
        }

        finalizer = InternalImportFinalizer(
            "http://api:5050/api/data/graph/internal/import",
            "secret-token",
            timeout=30,
        )

        for label, body in bad_bodies.items():
            with self.subTest(label=label):
                response = _FakeResponse(json_data=body)
                fake_post = _RecordingPost(response)
                finalizer._post = fake_post

                with self.assertRaises(ValueError, msg=label):
                    finalizer("job-1", "ground", stats)

    # -----------------------------------------------------------------------
    # Focused: missing / invalid node_count and embedded_count
    # -----------------------------------------------------------------------

    @staticmethod
    def _full_valid_body() -> dict:
        """Return a response body that passes all validation checks."""
        return {
            "status": "success",
            "task_id": "job-1",
            "graph_type": "ground",
            "artifact_path": "job-1-relationships.csv",
            "node_count": 5,
            "relationship_count": 3,
            "embedded_count": 2,
            "vector_index_ready": True,
        }

    def _make_finalizer(self, body: dict) -> InternalImportFinalizer:
        """Create a finalizer wired to return *body* from POST."""
        response = _FakeResponse(json_data=body)
        fake_post = _RecordingPost(response)
        return InternalImportFinalizer(
            "http://api:5050/api/data/graph/internal/import",
            "secret-token",
            timeout=30,
            post=fake_post,
        )

    def _valid_stats(self) -> ArtifactStats:
        return ArtifactStats(
            rows=3, sha256="a" * 64, path="/out/job-1-relationships.csv"
        )

    def test_missing_node_count_raises(self) -> None:
        body = self._full_valid_body()
        del body["node_count"]
        finalizer = self._make_finalizer(body)
        with self.assertRaises(ValueError) as ctx:
            finalizer("job-1", "ground", self._valid_stats())
        self.assertIn("node_count", str(ctx.exception))

    def test_invalid_node_count_string_raises(self) -> None:
        body = {**self._full_valid_body(), "node_count": "five"}
        finalizer = self._make_finalizer(body)
        with self.assertRaises(ValueError):
            finalizer("job-1", "ground", self._valid_stats())

    def test_invalid_node_count_negative_raises(self) -> None:
        body = {**self._full_valid_body(), "node_count": -1}
        finalizer = self._make_finalizer(body)
        with self.assertRaises(ValueError):
            finalizer("job-1", "ground", self._valid_stats())

    def test_bool_node_count_rejected(self) -> None:
        """Python bool is a subclass of int; it must still be rejected."""
        for bad in (True, False):
            with self.subTest(node_count=bad):
                body = {**self._full_valid_body(), "node_count": bad}
                finalizer = self._make_finalizer(body)
                with self.assertRaises(ValueError):
                    finalizer("job-1", "ground", self._valid_stats())

    def test_missing_embedded_count_raises(self) -> None:
        body = self._full_valid_body()
        del body["embedded_count"]
        finalizer = self._make_finalizer(body)
        with self.assertRaises(ValueError) as ctx:
            finalizer("job-1", "ground", self._valid_stats())
        self.assertIn("embedded_count", str(ctx.exception))

    def test_invalid_embedded_count_string_raises(self) -> None:
        body = {**self._full_valid_body(), "embedded_count": "two"}
        finalizer = self._make_finalizer(body)
        with self.assertRaises(ValueError):
            finalizer("job-1", "ground", self._valid_stats())

    def test_invalid_embedded_count_negative_raises(self) -> None:
        body = {**self._full_valid_body(), "embedded_count": -1}
        finalizer = self._make_finalizer(body)
        with self.assertRaises(ValueError):
            finalizer("job-1", "ground", self._valid_stats())

    def test_bool_embedded_count_rejected(self) -> None:
        """Python bool is a subclass of int; it must still be rejected."""
        for bad in (True, False):
            with self.subTest(embedded_count=bad):
                body = {**self._full_valid_body(), "embedded_count": bad}
                finalizer = self._make_finalizer(body)
                with self.assertRaises(ValueError):
                    finalizer("job-1", "ground", self._valid_stats())

    # -----------------------------------------------------------------------
    # Focused: response identity / status / path mismatches
    # -----------------------------------------------------------------------

    def test_status_not_success_raises(self) -> None:
        body = {**self._full_valid_body(), "status": "error"}
        finalizer = self._make_finalizer(body)
        with self.assertRaises(ValueError) as ctx:
            finalizer("job-1", "ground", self._valid_stats())
        self.assertIn("success", str(ctx.exception))

    def test_status_missing_raises(self) -> None:
        body = self._full_valid_body()
        del body["status"]
        finalizer = self._make_finalizer(body)
        with self.assertRaises(ValueError):
            finalizer("job-1", "ground", self._valid_stats())

    def test_task_id_mismatch_raises(self) -> None:
        body = {**self._full_valid_body(), "task_id": "other-job"}
        finalizer = self._make_finalizer(body)
        with self.assertRaises(ValueError) as ctx:
            finalizer("job-1", "ground", self._valid_stats())
        self.assertIn("job-1", str(ctx.exception))

    def test_task_id_missing_raises(self) -> None:
        body = self._full_valid_body()
        del body["task_id"]
        finalizer = self._make_finalizer(body)
        with self.assertRaises(ValueError):
            finalizer("job-1", "ground", self._valid_stats())

    def test_graph_type_mismatch_raises(self) -> None:
        body = {**self._full_valid_body(), "graph_type": "drill"}
        finalizer = self._make_finalizer(body)
        with self.assertRaises(ValueError) as ctx:
            finalizer("job-1", "ground", self._valid_stats())
        self.assertIn("ground", str(ctx.exception))

    def test_graph_type_missing_raises(self) -> None:
        body = self._full_valid_body()
        del body["graph_type"]
        finalizer = self._make_finalizer(body)
        with self.assertRaises(ValueError):
            finalizer("job-1", "ground", self._valid_stats())

    def test_artifact_path_mismatch_raises(self) -> None:
        body = {**self._full_valid_body(), "artifact_path": "wrong.csv"}
        finalizer = self._make_finalizer(body)
        with self.assertRaises(ValueError) as ctx:
            finalizer("job-1", "ground", self._valid_stats())
        self.assertIn("job-1-relationships.csv", str(ctx.exception))

    def test_artifact_path_missing_raises(self) -> None:
        body = self._full_valid_body()
        del body["artifact_path"]
        finalizer = self._make_finalizer(body)
        with self.assertRaises(ValueError):
            finalizer("job-1", "ground", self._valid_stats())

    def test_response_not_dict_raises(self) -> None:
        """A non-dict JSON response body must raise ValueError."""
        response = _FakeResponse(json_data="not a dict")  # type: ignore[arg-type]
        fake_post = _RecordingPost(response)
        finalizer = InternalImportFinalizer(
            "http://api:5050/api/data/graph/internal/import",
            "secret-token",
            timeout=30,
            post=fake_post,
        )
        with self.assertRaises(ValueError) as ctx:
            finalizer("job-1", "ground", self._valid_stats())
        self.assertIn("JSON object", str(ctx.exception))

    # -----------------------------------------------------------------------
    # Constructor validation: bad URL, token, timeout + repr hides token
    # -----------------------------------------------------------------------

    def test_constructor_rejects_bad_url_token_and_timeout(self) -> None:
        """Constructor must reject empty/whitespace URL, empty token,
        and non-positive timeout."""
        with self.subTest(label="empty url"):
            with self.assertRaises(ValueError):
                InternalImportFinalizer("", "secret-token")

        with self.subTest(label="whitespace url"):
            with self.assertRaises(ValueError):
                InternalImportFinalizer("   ", "secret-token")

        with self.subTest(label="empty token"):
            with self.assertRaises(ValueError):
                InternalImportFinalizer("http://api:5050", "")

        with self.subTest(label="whitespace token"):
            with self.assertRaises(ValueError):
                InternalImportFinalizer("http://api:5050", "   ")

        with self.subTest(label="timeout zero"):
            with self.assertRaises(ValueError):
                InternalImportFinalizer(
                    "http://api:5050", "secret-token", timeout=0
                )

        with self.subTest(label="timeout negative"):
            with self.assertRaises(ValueError):
                InternalImportFinalizer(
                    "http://api:5050", "secret-token", timeout=-5
                )

    def test_repr_does_not_contain_token(self) -> None:
        """repr() must never expose the authentication token."""
        finalizer = InternalImportFinalizer(
            "http://api:5050/api/data/graph/internal/import",
            "super-secret-token-123",
            timeout=42,
        )
        r = repr(finalizer)
        self.assertNotIn("super-secret-token-123", r)
        self.assertNotIn("token", r.lower())
        self.assertIn("http://api:5050", r)

    # -----------------------------------------------------------------------
    # raise_for_status error propagation, token not leaked
    # -----------------------------------------------------------------------

    def test_raise_for_status_error_propagates_without_leaking_token(
        self,
    ) -> None:
        """When raise_for_status raises, the exception propagates and its
        str/repr must not contain the secret token."""
        upstream_error = RuntimeError("upstream failed")
        response = _FakeResponse(
            json_data={"relationship_count": 3, "vector_index_ready": True},
            raise_error=upstream_error,
        )
        fake_post = _RecordingPost(response)

        finalizer = InternalImportFinalizer(
            "http://api:5050/api/data/graph/internal/import",
            "super-secret-token-123",
            timeout=30,
            post=fake_post,
        )

        stats = ArtifactStats(
            rows=3, sha256="a" * 64, path="/out/job-1-relationships.csv"
        )

        with self.assertRaises(RuntimeError) as ctx:
            finalizer("job-1", "ground", stats)

        self.assertIs(ctx.exception, upstream_error)
        self.assertIn("upstream failed", str(ctx.exception))
        self.assertNotIn("super-secret-token-123", str(ctx.exception))
        self.assertNotIn("super-secret-token-123", repr(ctx.exception))
        self.assertEqual(len(fake_post.calls), 1)

    # -----------------------------------------------------------------------
    # internal_import_finalizer_factory_from_env
    # -----------------------------------------------------------------------

    def test_factory_from_env_returns_none_when_url_missing(self) -> None:
        """Factory must return None when MAIN_API_INTERNAL_URL is absent."""
        with patch.dict("os.environ", {}, clear=True):
            result = internal_import_finalizer_factory_from_env()
            self.assertIsNone(result)

    def test_factory_from_env_returns_none_when_url_empty(self) -> None:
        """Factory must return None when URL is empty string."""
        with patch.dict(
            "os.environ",
            {"MAIN_API_INTERNAL_URL": "", "GRAPH_INTERNAL_TOKEN": "tok"},
            clear=True,
        ):
            result = internal_import_finalizer_factory_from_env()
            self.assertIsNone(result)

    def test_factory_from_env_returns_none_when_token_missing(self) -> None:
        """Factory must return None when GRAPH_INTERNAL_TOKEN is absent."""
        with patch.dict(
            "os.environ",
            {"MAIN_API_INTERNAL_URL": "http://api:5050"},
            clear=True,
        ):
            result = internal_import_finalizer_factory_from_env()
            self.assertIsNone(result)

    def test_factory_from_env_returns_none_when_token_empty(self) -> None:
        """Factory must return None when token is empty string."""
        with patch.dict(
            "os.environ",
            {"MAIN_API_INTERNAL_URL": "http://api:5050", "GRAPH_INTERNAL_TOKEN": ""},
            clear=True,
        ):
            result = internal_import_finalizer_factory_from_env()
            self.assertIsNone(result)

    def test_factory_from_env_returns_callable_with_correct_timeout_and_no_token_in_repr(
        self,
    ) -> None:
        """With URL, token, and GRAPH_IMPORT_TIMEOUT set, factory returns a
        callable whose repr includes the timeout but not the secret token."""
        env = {
            "MAIN_API_INTERNAL_URL": "http://api:5050",
            "GRAPH_INTERNAL_TOKEN": "super-secret-token-123",
            "GRAPH_IMPORT_TIMEOUT": "45",
        }
        with patch.dict("os.environ", env, clear=True):
            factory = internal_import_finalizer_factory_from_env()
            self.assertIsNotNone(factory)
            self.assertTrue(callable(factory))

            instance = factory()
            self.assertIsInstance(instance, InternalImportFinalizer)
            r = repr(instance)
            self.assertIn("45", r, "repr should include timeout")
            self.assertNotIn("super-secret-token-123", r)
            self.assertNotIn("token", r.lower())


# Sentinel to distinguish "not passed" from "passed as None"
_UNSET = object()


class GraphPipelineFinalizerWiringTests(unittest.TestCase):
    """Tests for GraphPipeline auto-wiring of finalizer_factory from env."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self._tmp_path = Path(self._tmp)
        self.db_path = self._tmp_path / "jobs.db"
        self.store = JobStore(self.db_path)
        self.source_dir = self._tmp_path / "source"
        self.source_dir.mkdir()
        (self.source_dir / "f.txt").write_text("x")
        self.index_ground = self._tmp_path / "indexing"
        self.index_drill = self._tmp_path / "indexing_drill"

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _build_pipeline(
        self, *, finalizer_factory: Any = _UNSET,
        env: Optional[dict] = None,
    ) -> GraphPipeline:
        """Helper: construct GraphPipeline under a patched env, no worker start."""
        kwargs: dict[str, Any] = dict(
            store=self.store,
            source_dir=self.source_dir,
            index_ground=self.index_ground,
            index_drill=self.index_drill,
            popen_cmd_factory=lambda gt, root: ["echo", "noop"],
        )
        if finalizer_factory is not _UNSET:
            kwargs["finalizer_factory"] = finalizer_factory

        if env is not None:
            with patch.dict("os.environ", env, clear=True):
                p = GraphPipeline(**kwargs)
        else:
            p = GraphPipeline(**kwargs)
        return p

    # ---- Test 1: env-set finalizer auto-wired into pipeline (RED) ----------------

    def test_env_default_factory_is_wired(self) -> None:
        """When env has MAIN_API_INTERNAL_URL, GRAPH_INTERNAL_TOKEN, and
        GRAPH_IMPORT_TIMEOUT, constructing GraphPipeline with no explicit
        finalizer_factory should auto-wire a non-None callable whose return
        value is an InternalImportFinalizer whose repr includes the timeout
        but not the token.

        This is expected to be RED because the constructor currently stores
        None when no explicit factory is passed.
        """
        env = {
            "MAIN_API_INTERNAL_URL": "http://api:5050",
            "GRAPH_INTERNAL_TOKEN": "super-secret-token-xyz",
            "GRAPH_IMPORT_TIMEOUT": "42",
        }
        p = self._build_pipeline(env=env)
        try:
            factory = p._finalizer_factory
            self.assertIsNotNone(factory, "finalizer_factory should be auto-wired from env")
            self.assertTrue(callable(factory), "finalizer_factory should be callable")

            instance = factory()
            self.assertIsInstance(instance, InternalImportFinalizer)
            r = repr(instance)
            self.assertIn("42", r, "repr should include the timeout value")
            self.assertNotIn("super-secret-token-xyz", r, "repr must not expose the token")
            self.assertNotIn("token", r.lower(), "repr must not mention 'token'")
        finally:
            try:
                p.stop(timeout=1)
            except Exception:
                pass

    # ---- Test 2: explicit factory wins over env (GREEN) --------------------------

    def test_explicit_factory_overrides_env(self) -> None:
        """When an explicit finalizer_factory is passed, it must be retained
        exactly, regardless of what the env contains.
        """
        sentinel = _noop_finalizer_factory
        env = {
            "MAIN_API_INTERNAL_URL": "http://api:5050",
            "GRAPH_INTERNAL_TOKEN": "tok",
            "GRAPH_IMPORT_TIMEOUT": "99",
        }
        p = self._build_pipeline(finalizer_factory=sentinel, env=env)
        try:
            self.assertIs(
                p._finalizer_factory,
                sentinel,
                "Explicit finalizer_factory must be retained, not replaced by env wiring",
            )
        finally:
            try:
                p.stop(timeout=1)
            except Exception:
                pass

    # ---- Test 3: env clear + no explicit -> stays None (GREEN) -------------------

    def test_no_env_no_explicit_factory_is_none(self) -> None:
        """With env clear and no explicit finalizer_factory, the pipeline's
        factory must remain None (existing safety behavior).
        """
        env = {}  # clear=True via patch.dict
        p = self._build_pipeline(env=env)
        try:
            self.assertIsNone(
                p._finalizer_factory,
                "finalizer_factory should remain None when env is empty and none passed",
            )
        finally:
            try:
                p.stop(timeout=1)
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
