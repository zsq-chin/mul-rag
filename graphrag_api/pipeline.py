"""Single-worker daemon build pipeline for durable graph jobs.

Design principles:
  - One daemon thread per GraphPipeline instance (idempotent start).
  - Every stage is fail-safe: any exception -> failed, never leaves active.
  - Subprocess via Popen with incremental log tailing (never subprocess.run).
  - Injectable Popen/command/finalizer factories for deterministic testing.
  - Finalizer extension point for Task 10; absent -> failed with clear message.
  - A reader thread drains stdout so the main loop can poll cancel_requested
    even when the subprocess is silent.
"""

from __future__ import annotations

import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Callable, List, Optional

from graphrag_api.job_store import JobStore
from graphrag_api.schemas import TERMINAL_STATUSES


# ---------------------------------------------------------------------------
# Finalization evidence (Task 10 extension point)
# ---------------------------------------------------------------------------


@dataclass
class FinalizationEvidence:
    """Evidence that indexing completed successfully.

    The pipeline only transitions to ``completed`` when *all* fields indicate
    success AND the store is currently in the ``indexing`` stage.
    """

    indexed: bool
    relationship_count: int
    artifact_path: str
    artifact_sha256: str


# ---------------------------------------------------------------------------
# Type aliases for injectable factories
# ---------------------------------------------------------------------------

# (graph_type, index_root) -> command list
CommandFactory = Callable[[str, Path], List[str]]

# Mirrors subprocess.Popen signature enough for our usage
PopenFactory = Callable[..., Any]

# () -> callable that produces FinalizationEvidence
FinalizerFactory = Callable[[], Callable[..., FinalizationEvidence]]


# ---------------------------------------------------------------------------
# Progress mapping for GraphRAG workflow stages
# ---------------------------------------------------------------------------

# Known workflow stage names in the order GraphRAG emits them.
_WORKFLOW_STAGES: List[str] = [
    "create_base_text_units",
    "create_entities",
    "create_relationships",
    "create_community_reports",
    "write_final_documents",
]

# Map stage name -> progress value (monotonic, bounded 10..70)
_STAGE_PROGRESS: dict[str, int] = {
    "create_base_text_units": 15,
    "create_entities": 25,
    "create_relationships": 40,
    "create_community_reports": 55,
    "write_final_documents": 70,
}

# ---------------------------------------------------------------------------
# Log tail size limit (32 KiB)
# ---------------------------------------------------------------------------

_LOG_TAIL_LIMIT = 32 * 1024


# ---------------------------------------------------------------------------
# Default production paths
# ---------------------------------------------------------------------------

_DEFAULT_SOURCE_DIR = Path("/app/saves/data/copypath")
_DEFAULT_INDEX_GROUND = Path("/app/indexing")
_DEFAULT_INDEX_DRILL = Path("/app/indexing_drill")


# ---------------------------------------------------------------------------
# Sentinel for stdout reader thread
# ---------------------------------------------------------------------------

_EOF = ""  # readline() returns "" on EOF


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class GraphPipeline:
    """Orchestrates durable graph build jobs on a single daemon worker.

    Thread-safe: submit/cancel/retry can be called from any thread.
    The worker thread is the sole consumer of the internal job queue.
    """

    def __init__(
        self,
        store: JobStore,
        *,
        source_dir: Path = _DEFAULT_SOURCE_DIR,
        index_ground: Path = _DEFAULT_INDEX_GROUND,
        index_drill: Path = _DEFAULT_INDEX_DRILL,
        finalizer_factory: Optional[FinalizerFactory] = None,
        popen_factory: Optional[PopenFactory] = None,
        popen_cmd_factory: Optional[CommandFactory] = None,
        terminate_timeout: float = 10.0,
    ) -> None:
        self._store = store
        self._source_dir = source_dir
        self._index_ground = index_ground
        self._index_drill = index_drill
        self._finalizer_factory = finalizer_factory
        self._popen_factory = popen_factory or self._default_popen_factory
        self._popen_cmd_factory = popen_cmd_factory or self._default_cmd_factory
        self._terminate_timeout = terminate_timeout

        self._queue: Queue[str | None] = Queue()
        self._worker: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # Lock-protected reference to the currently running subprocess so
        # stop() can terminate it even when the worker thread is blocked
        # inside _run_subprocess waiting on a silent process.
        self._active_proc_lock = threading.Lock()
        self._active_proc: Optional[Any] = None

    # -- factories ----------------------------------------------------------

    @staticmethod
    def _default_popen_factory(*args: Any, **kwargs: Any) -> Any:
        import subprocess

        return subprocess.Popen(*args, **kwargs)

    @staticmethod
    def _default_cmd_factory(graph_type: str, root: Path) -> List[str]:
        return ["python", "-m", "graphrag.index", "--root", str(root)]

    # -- helpers ------------------------------------------------------------

    def _index_root(self, graph_type: str) -> Path:
        if graph_type == "drill":
            return self._index_drill
        return self._index_ground

    # -- public API ---------------------------------------------------------

    def submit(self, graph_type: str) -> Any:
        """Create a queued job and enqueue it for the daemon worker."""
        record = self._store.create(graph_type)
        self._queue.put(record.id)
        return record

    def start(self) -> None:
        """Start the daemon worker thread.  Idempotent -- no-op if running."""
        if self._worker is not None and self._worker.is_alive():
            return
        self._stop_event.clear()
        self._worker = threading.Thread(
            target=self._worker_loop,
            name="graph-pipeline-worker",
            daemon=True,
        )
        self._worker.start()

    def stop(self, timeout: float = 10.0) -> None:
        """Signal the worker to stop and join the thread.

        If the worker is blocked on a subprocess, the subprocess is
        terminated/kill before the second join.  Raises TimeoutError
        when the thread still refuses to exit (self._worker is kept so
        callers can retry or inspect).
        """
        self._stop_event.set()
        # Put a sentinel to wake the worker if it's blocking on Queue.get()
        self._queue.put(None)
        if self._worker is None:
            return

        deadline = time.monotonic() + timeout
        self._worker.join(timeout=timeout)

        if self._worker.is_alive():
            # Worker is stuck -- forcibly terminate any active subprocess.
            with self._active_proc_lock:
                proc = self._active_proc
            if proc is not None:
                self._terminate_proc(proc)

            remaining = deadline - time.monotonic()
            self._worker.join(timeout=max(0.01, remaining))

            if self._worker.is_alive():
                raise TimeoutError(
                    "Worker thread did not exit after terminating subprocess"
                )

        # Thread exited -- safe to clear.
        self._worker = None

    def retry(self, job_id: str) -> Any:
        """Retry a terminal job: reset in store and re-enqueue."""
        record = self._store.retry(job_id)
        self._queue.put(record.id)
        return record

    def cancel(self, job_id: str) -> Any:
        """Persist cancel request via the store."""
        return self._store.request_cancel(job_id)

    # -- worker loop --------------------------------------------------------

    def _worker_loop(self) -> None:
        """Main loop: dequeue job IDs and process them one at a time."""
        while not self._stop_event.is_set():
            try:
                job_id = self._queue.get(timeout=0.5)
            except Empty:
                continue
            if job_id is None:
                break
            self._run_job(job_id)

    # -- job runner ---------------------------------------------------------

    def _run_job(self, job_id: str) -> None:
        """Execute the full pipeline for a single job.

        Every branch ends in a terminal state -- no exception escapes.
        """
        record = self._store.get(job_id)
        if record is None:
            return
        if record.status in TERMINAL_STATUSES:
            return

        # If cancel was requested before we started, go straight to
        # cancelled without spawning any subprocess.
        if record.cancel_requested or record.status == "cancelling":
            self._store.transition(
                job_id,
                "cancelled",
                record.progress,
                stage="cancelled",
            )
            return

        graph_type = record.graph_type
        index_root = self._index_root(graph_type)
        copied_files: List[Path] = []

        try:
            # --- validate source directory ---
            if not self._source_dir.is_dir():
                raise FileNotFoundError(
                    f"Source directory does not exist: {self._source_dir}"
                )

            source_files = [
                f for f in self._source_dir.iterdir() if f.is_file()
            ]
            if not source_files:
                raise FileNotFoundError(
                    f"Source directory has no regular files: {self._source_dir}"
                )

            # --- copying stage (progress 5) ---
            input_dir = index_root / "input"
            input_dir.mkdir(parents=True, exist_ok=True)

            for src_file in source_files:
                dest = input_dir / src_file.name
                shutil.copy2(src_file, dest)
                copied_files.append(src_file)

            self._store.transition(
                job_id,
                "copying",
                5,
                stage="copying",
                input_count=len(source_files),
            )

            # --- check cancel after copying, before spawning subprocess ---
            if self._cancel_before_process(job_id):
                return

            # --- check stop after copying, before spawning subprocess ---
            if self._stop_event.is_set():
                rec = self._store.get(job_id)
                if rec is not None and rec.status not in TERMINAL_STATUSES:
                    self._store.transition(
                        job_id,
                        "interrupted",
                        rec.progress,
                        stage="interrupted",
                    )
                return

            # --- building stage (progress 10) ---
            self._store.transition(
                job_id, "building", 10, stage="building"
            )

            cmd = self._popen_cmd_factory(graph_type, index_root)
            build_ok = self._run_subprocess(job_id, cmd)

            if not build_ok:
                # _run_subprocess already transitioned to failed/cancelled
                return

            # --- post-build transition chain ---
            # building -> converting -> importing -> indexing
            self._store.transition(
                job_id, "converting", 72, stage="converting"
            )
            self._store.transition(
                job_id, "importing", 76, stage="importing"
            )

            # --- finalization (runs in indexing stage) ---
            self._finalize(job_id, graph_type, copied_files)

        except Exception as exc:
            self._fail_job(job_id, str(exc))

    # -- subprocess ---------------------------------------------------------

    def _run_subprocess(self, job_id: str, cmd: List[str]) -> bool:
        """Run the build subprocess with incremental log tailing.

        Returns True on success (exit code 0), False on failure or cancel.
        The caller should not transition on False -- this method already did.
        """
        proc = self._popen_factory(
            cmd,
            stdout=-1,  # PIPE
            stderr=-2,  # STDOUT
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        # Register so stop() can terminate us even when the reader blocks.
        with self._active_proc_lock:
            self._active_proc = proc

        log_buffer: List[str] = []
        current_progress = 10
        stdout_eof = False

        # --- reader thread: drains stdout into a queue so the main loop
        #     is never blocked on readline (enables cancel on silent proc).
        line_queue: Queue[str] = Queue()

        def _reader() -> None:
            try:
                while True:
                    line = proc.stdout.readline()  # type: ignore[union-attr]
                    line_queue.put(line)
                    if line == _EOF:
                        break
            except Exception:
                line_queue.put(_EOF)

        reader_thread = threading.Thread(target=_reader, daemon=True)
        reader_thread.start()

        try:
            while True:
                # Check stop_event first -- stop() needs to be able to
                # break out even when no cancel was requested via the store.
                if self._stop_event.is_set():
                    self._terminate_proc(proc)
                    rec = self._store.get(job_id)
                    if rec is not None and rec.status not in TERMINAL_STATUSES:
                        self._store.transition(
                            job_id,
                            "interrupted",
                            rec.progress,
                            stage="interrupted",
                        )
                    return False

                # Check cancel between reads
                rec = self._store.get(job_id)
                if rec is not None and rec.cancel_requested:
                    self._handle_cancel(proc, job_id, log_buffer)
                    return False

                try:
                    line = line_queue.get(timeout=0.2)
                except Empty:
                    # No output in 200ms -- loop back and check cancel
                    if stdout_eof:
                        ret = proc.poll()
                        if ret is not None:
                            log_tail = "".join(log_buffer)
                            if ret != 0:
                                self._fail_job(
                                    job_id,
                                    f"Build process exited with code {ret}",
                                    log_tail=log_tail,
                                )
                                return False
                            self._store.transition(
                                job_id,
                                "building",
                                current_progress,
                                log_tail=log_tail,
                            )
                            return True
                    continue

                if line == _EOF:
                    # stdout closed but process may still be running
                    stdout_eof = True
                    continue

                # Accumulate log line
                log_buffer.append(line)
                # Trim to last 32 KiB
                joined = "".join(log_buffer)
                if len(joined.encode("utf-8")) > _LOG_TAIL_LIMIT:
                    tail_bytes = joined.encode("utf-8")[-_LOG_TAIL_LIMIT:]
                    log_buffer = [
                        tail_bytes.decode("utf-8", errors="replace")
                    ]

                # Detect workflow stage transitions
                new_progress = self._detect_progress(line, current_progress)
                if new_progress > current_progress:
                    current_progress = new_progress
                    self._store.transition(
                        job_id,
                        "building",
                        current_progress,
                        stage=self._detect_stage(line),
                        log_tail="".join(log_buffer),
                    )
                else:
                    self._store.transition(
                        job_id,
                        "building",
                        current_progress,
                        log_tail="".join(log_buffer),
                    )

        except Exception as exc:
            self._fail_job(job_id, f"Subprocess error: {exc}")
            return False
        finally:
            # Always clear the active-process reference so stop() does not
            # try to terminate a process that has already exited.
            with self._active_proc_lock:
                self._active_proc = None
            # Bounded join on the reader thread -- after terminate/kill the
            # readline should return EOF quickly, but we cap the wait to
            # avoid hanging on a misbehaving stdio pipe.
            reader_thread.join(timeout=5)

    # -- shutdown helper ----------------------------------------------------

    def _terminate_proc(self, proc: Any) -> None:
        """Terminate/kill a subprocess with bounded waits.

        Reused by stop() and _run_subprocess so the cancel path is not
        modified.
        """
        try:
            proc.terminate()
            try:
                proc.wait(timeout=self._terminate_timeout)
            except Exception:
                proc.kill()
                proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass

    # -- cancel handler -----------------------------------------------------

    def _handle_cancel(
        self, proc: Any, job_id: str, log_buffer: List[str]
    ) -> None:
        """Terminate/kill a subprocess and transition to cancelled."""
        try:
            proc.terminate()
            try:
                proc.wait(timeout=self._terminate_timeout)
            except Exception:
                proc.kill()
                proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass

        log_tail = "".join(log_buffer)
        rec = self._store.get(job_id)
        current_progress = rec.progress if rec else 0
        self._store.transition(
            job_id,
            "cancelled",
            current_progress,
            stage="cancelled",
            log_tail=log_tail,
        )

    # -- finalizer ----------------------------------------------------------

    def _finalize(
        self,
        job_id: str,
        graph_type: str,
        copied_files: List[Path],
    ) -> None:
        """Run the finalizer (Task 10 extension point) and transition.

        Without a finalizer, the job fails with a clear message.
        With a finalizer, only transitions to completed if evidence is valid
        AND the store is currently in indexing stage.
        """
        if self._finalizer_factory is None:
            self._fail_job(
                job_id,
                "Build succeeded but finalization is not configured. "
                "Cannot complete without a finalizer.",
            )
            return

        # Transition to indexing before running finalizer
        self._store.transition(
            job_id, "indexing", 80, stage="indexing"
        )

        try:
            finalizer = self._finalizer_factory()
            evidence = finalizer(graph_type)
        except Exception as exc:
            self._fail_job(job_id, f"Finalizer raised: {exc}")
            return

        # Validate evidence
        if not isinstance(evidence, FinalizationEvidence):
            self._fail_job(
                job_id, "Finalizer did not return FinalizationEvidence"
            )
            return

        if not evidence.indexed:
            self._fail_job(
                job_id,
                "Finalization evidence indicates indexing did not succeed "
                f"(indexed={evidence.indexed})",
            )
            return

        if not evidence.artifact_path:
            self._fail_job(
                job_id, "Finalization evidence missing artifact_path"
            )
            return

        if not evidence.artifact_sha256:
            self._fail_job(
                job_id, "Finalization evidence missing artifact_sha256"
            )
            return

        # Verify store is still in indexing stage
        rec = self._store.get(job_id)
        if rec is None or rec.status != "indexing":
            self._fail_job(
                job_id,
                f"Store state changed during finalization: "
                f"{rec.status if rec else '<gone>'}",
            )
            return

        # All evidence valid -- clean source files before transitioning
        # to completed so the active slot is still held and no subsequent
        # job can re-read the same source files.
        self._cleanup_source(copied_files)

        self._store.transition(
            job_id,
            "completed",
            100,
            stage="completed",
            relationship_count=evidence.relationship_count,
            artifact_path=evidence.artifact_path,
            artifact_sha256=evidence.artifact_sha256,
        )

    # -- helpers ------------------------------------------------------------

    def _fail_job(
        self, job_id: str, error: str, log_tail: str = ""
    ) -> None:
        """Transition a job to failed.  Never raises."""
        try:
            rec = self._store.get(job_id)
            progress = rec.progress if rec else 0
            self._store.transition(
                job_id,
                "failed",
                progress,
                stage="failed",
                error_summary=error,
                log_tail=log_tail,
            )
        except Exception:
            pass

    def _cancel_before_process(self, job_id: str) -> bool:
        """If cancel was requested, transition to cancelled and return True.

        Used between pipeline stages (e.g. after copying, before Popen) so
        a cancel request is honoured without spawning a subprocess.
        """
        rec = self._store.get(job_id)
        if rec is not None and (rec.cancel_requested or rec.status == "cancelling"):
            self._store.transition(
                job_id,
                "cancelled",
                rec.progress,
                stage="cancelled",
            )
            return True
        return False

    def _cleanup_source(self, files: List[Path]) -> None:
        """Remove copied source files.  Only called on success."""
        for f in files:
            try:
                if f.exists():
                    f.unlink()
            except Exception:
                pass

    # -- progress detection -------------------------------------------------

    @staticmethod
    def _detect_progress(line: str, current: int) -> int:
        """Detect known GraphRAG workflow markers and return mapped progress.

        Returns *current* if no advancement detected (monotonic).
        """
        for stage_name, stage_progress in _STAGE_PROGRESS.items():
            if stage_name in line and stage_progress > current:
                return stage_progress
        return current

    @staticmethod
    def _detect_stage(line: str) -> str:
        """Extract the most recent workflow stage name from a log line."""
        for stage_name in reversed(_WORKFLOW_STAGES):
            if stage_name in line:
                return stage_name
        return "building"
