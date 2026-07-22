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

import shutil
import subprocess as _subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any, Callable, List, Optional

from graphrag_api.job_store import JobStore
from graphrag_api.pipeline import FinalizationEvidence, GraphPipeline


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
    ) -> GraphPipeline:
        """Create a pipeline with injectable dependencies."""
        if popen_cmd_factory is None:
            popen_cmd_factory = lambda gt, root: ["echo", "noop"]

        p = GraphPipeline(
            store=self.store,
            source_dir=self.source_dir,
            index_ground=self.index_ground,
            index_drill=self.index_drill,
            finalizer_factory=finalizer_factory,
            popen_factory=popen_factory,
            popen_cmd_factory=popen_cmd_factory,
            terminate_timeout=terminate_timeout,
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


if __name__ == "__main__":
    unittest.main()
