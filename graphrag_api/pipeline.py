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

import inspect
import re
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
        artifact_selector: Optional[Callable[[Path, OutputSnapshot], Path]] = None,
        artifact_converter: Optional[Callable[[Path, Path], ArtifactStats]] = None,
    ) -> None:
        self._store = store
        self._source_dir = source_dir
        self._index_ground = index_ground
        self._index_drill = index_drill
        if finalizer_factory is not None:
            self._finalizer_factory = finalizer_factory
        else:
            self._finalizer_factory = internal_import_finalizer_factory_from_env()
        self._popen_factory = popen_factory or self._default_popen_factory
        self._popen_cmd_factory = popen_cmd_factory or self._default_cmd_factory
        self._terminate_timeout = terminate_timeout
        self._artifact_selector = artifact_selector or select_new_relationship_artifact
        self._artifact_converter = artifact_converter or convert_relationships

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
            try:
                self._run_job(job_id)
            except Exception as exc:
                # Safety net: an unexpected exception escaping _run_job
                # must not leave the job in a non-terminal state.
                # _fail_job swallows its own errors so this never raises.
                self._fail_job(job_id, f"Unexpected worker error: {exc}")

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

            # --- snapshot output dirs before build produces artifacts ---
            snapshot = snapshot_output_directories(index_root / "output")

            # --- building stage (progress 10) ---
            self._store.transition(
                job_id, "building", 10, stage="building"
            )

            cmd = self._popen_cmd_factory(graph_type, index_root)
            build_ok = self._run_subprocess(job_id, cmd)

            if not build_ok:
                # _run_subprocess already transitioned to failed/cancelled
                return

            # --- post-build: select, convert, validate ---
            self._store.transition(
                job_id, "converting", 72, stage="converting"
            )

            selected_parquet = self._artifact_selector(
                index_root / "output", snapshot
            )

            if graph_type == "drill":
                dest_root = index_root / "drill_graph_fill"
            else:
                dest_root = index_root / "ground_graph_fill"
            destination = dest_root / f"{job_id}-relationships.csv"

            stats = self._artifact_converter(selected_parquet, destination)

            # Validate ArtifactStats
            if not isinstance(stats, ArtifactStats):
                raise ValueError(
                    "Artifact converter did not return ArtifactStats"
                )
            if stats.rows < 0:
                raise ValueError(
                    f"Artifact rows must be >= 0, got {stats.rows}"
                )
            if not stats.path:
                raise ValueError("Artifact path is empty")
            if not re.fullmatch(r"[0-9a-f]{64}", stats.sha256):
                raise ValueError(
                    f"Artifact sha256 is not 64 lowercase hex: {stats.sha256!r}"
                )

            self._store.transition(
                job_id, "converting", 78, stage="converting",
                relationship_count=stats.rows,
                artifact_path=stats.path,
                artifact_sha256=stats.sha256,
            )

            # --- finalization ---
            self._finalize(job_id, graph_type, copied_files, stats)

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
                    # No output in 200ms -- poll process liveness
                    # independently of stdout EOF.  A child can exit
                    # while the reader thread never delivers EOF (e.g.
                    # pipe not fully closed), so gating poll() on EOF
                    # would leave the job stuck forever.
                    ret = proc.poll()
                    if ret is not None:
                        # Process exited.  The reader thread may have
                        # read a final buffered line but not yet
                        # enqueued it.  Give it a small, explicitly
                        # bounded opportunity to finish, then drain
                        # non-blockingly.
                        reader_thread.join(timeout=0.05)
                        # Drain any remaining queued
                        # output without blocking, then return.
                        while True:
                            try:
                                tail_line = line_queue.get_nowait()
                            except Empty:
                                break
                            if tail_line == _EOF:
                                break
                            log_buffer.append(tail_line)
                        joined = "".join(log_buffer)
                        if len(joined.encode("utf-8")) > _LOG_TAIL_LIMIT:
                            tail_bytes = joined.encode("utf-8")[
                                -_LOG_TAIL_LIMIT:
                            ]
                            log_buffer = [
                                tail_bytes.decode("utf-8", errors="replace")
                            ]
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
            # Bounded join on the reader thread.  After terminate/kill
            # the readline returns quickly so a short timeout suffices.
            # A long timeout would stall the no-EOF exit path where
            # the reader is permanently blocked on the pipe -- the
            # poll-detected-exit path already did its own brief join
            # and drain, so this is just a safety net.
            reader_thread.join(timeout=0.1)

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
        stats: ArtifactStats,
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

        # Transition to importing before running finalizer
        self._store.transition(
            job_id, "importing", 80, stage="importing"
        )

        try:
            finalizer = self._finalizer_factory()
            sig = inspect.signature(finalizer)
            new_args = (job_id, graph_type, stats)
            legacy_args = (graph_type,)
            try:
                sig.bind(*new_args)
            except TypeError:
                try:
                    sig.bind(*legacy_args)
                except TypeError:
                    raise TypeError(
                        "Finalizer signature does not accept (job_id, "
                        "graph_type, stats) or (graph_type,). "
                        "Supported signatures: "
                        "(job_id: str, graph_type: str, stats: ArtifactStats) "
                        "or (graph_type: str)"
                    )
                evidence = finalizer(*legacy_args)
            else:
                evidence = finalizer(*new_args)
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

        # Transition to indexing after valid evidence
        self._store.transition(
            job_id, "indexing", 95, stage="indexing"
        )

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


# ---------------------------------------------------------------------------
# Artifact conversion utilities
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArtifactStats:
    """Immutable statistics returned by artifact conversion functions."""

    rows: int
    sha256: str
    path: str = ""


def convert_relationships(parquet_path: Path, csv_path: Path) -> ArtifactStats:
    """Read a GraphRAG relationships parquet and write a canonicalised h/r/t CSV.

    Raises ValueError when required columns (source, target, description) are
    missing from the parquet file.
    """
    import csv
    import hashlib
    import io

    import pandas as pd

    df = pd.read_parquet(parquet_path)

    required = ("source", "target", "description")
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(missing)}")

    # Select only required columns
    df = df[list(required)]

    # Normalize: treat None/NaN as empty, otherwise str(value).strip()
    def _norm(v: object) -> str:
        try:
            if pd.isna(v):
                return ""
        except (ValueError, TypeError):
            pass
        return str(v).strip()

    for col in required:
        df[col] = df[col].map(_norm)

    # Rename: source->h, description->r, target->t
    df = df.rename(columns={"source": "h", "description": "r", "target": "t"})

    # Reorder to h, r, t
    df = df[["h", "r", "t"]]

    # Drop rows where any value is blank
    df = df[(df["h"] != "") & (df["r"] != "") & (df["t"] != "")]

    # Drop exact duplicates
    df = df.drop_duplicates()

    # Sort by h, r, t (deterministic)
    df = df.sort_values(by=["h", "r", "t"]).reset_index(drop=True)

    # Write deterministic UTF-8 CSV with newline='' semantics
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["h", "r", "t"])
    for _, row in df.iterrows():
        writer.writerow([row["h"], row["r"], row["t"]])

    raw = buf.getvalue().encode("utf-8")
    csv_path.write_bytes(raw)

    sha256 = hashlib.sha256(raw).hexdigest()
    return ArtifactStats(rows=len(df), sha256=sha256, path=str(csv_path))


# ---------------------------------------------------------------------------
# Output snapshot and new-artifact selector
# ---------------------------------------------------------------------------

_ARTIFACT_REL = Path("artifacts") / "create_final_relationships.parquet"


@dataclass(frozen=True)
class OutputSnapshot:
    """Immutable snapshot of an output directory's direct child directories."""

    directory_names: frozenset[str]
    started_at_ns: int


def snapshot_output_directories(output_root: Path) -> OutputSnapshot:
    """Capture the names of *direct* child directories under *output_root*.

    A missing *output_root* is treated as empty (no error).
    """
    names: set[str] = set()
    if output_root.is_dir():
        for entry in output_root.iterdir():
            if entry.is_dir():
                names.add(entry.name)
    return OutputSnapshot(
        directory_names=frozenset(names),
        started_at_ns=time.time_ns(),
    )


class InternalImportFinalizer:
    """Callable finalizer that POSTs to the internal import endpoint.

    Validates the response and returns :class:`FinalizationEvidence`.
    The token is never exposed in repr or error messages.
    """

    def __init__(
        self,
        url: str,
        token: str,
        timeout: float = 1800,
        post: Any = None,
    ) -> None:
        self._url = url.strip()
        self._token = token.strip()
        self._timeout = float(timeout)
        self._post = post

        if not self._url:
            raise ValueError("url must be non-empty")
        if not self._token:
            raise ValueError("token must be non-empty")
        if self._timeout <= 0:
            raise ValueError("timeout must be > 0")

    def __repr__(self) -> str:
        return f"InternalImportFinalizer(url={self._url!r}, timeout={self._timeout})"

    def __call__(
        self, job_id: str, graph_type: str, stats: ArtifactStats
    ) -> FinalizationEvidence:
        post = self._post
        if post is None:
            import httpx

            post = httpx.post

        response = post(
            self._url,
            json={
                "task_id": job_id,
                "graph_type": graph_type,
                "artifact_path": Path(stats.path).name,
            },
            headers={"X-Graph-Internal-Token": self._token},
            timeout=self._timeout,
        )
        response.raise_for_status()
        body = response.json()

        if not isinstance(body, dict):
            raise ValueError("response body must be a JSON object")

        # --- identity / status checks ---
        body_status = body.get("status")
        if body_status != "success":
            raise ValueError(
                f"response status must be 'success', got {body_status!r}"
            )

        body_task_id = body.get("task_id")
        if body_task_id != job_id:
            raise ValueError(
                f"response task_id must match job_id {job_id!r}, "
                f"got {body_task_id!r}"
            )

        body_graph_type = body.get("graph_type")
        if body_graph_type != graph_type:
            raise ValueError(
                f"response graph_type must match {graph_type!r}, "
                f"got {body_graph_type!r}"
            )

        expected_artifact = Path(stats.path).name
        body_artifact = body.get("artifact_path")
        if body_artifact != expected_artifact:
            raise ValueError(
                f"response artifact_path must match {expected_artifact!r}, "
                f"got {body_artifact!r}"
            )

        # --- strict non-negative int fields (bool rejected) ---
        for _field in ("node_count", "relationship_count", "embedded_count"):
            if _field not in body:
                raise ValueError(f"response missing '{_field}'")
            _val = body[_field]
            if not isinstance(_val, int) or isinstance(_val, bool) or _val < 0:
                raise ValueError(
                    f"{_field} must be a non-negative int, got {_val!r}"
                )

        rc = body["relationship_count"]

        if "vector_index_ready" not in body:
            raise ValueError("response missing 'vector_index_ready'")
        vir = body["vector_index_ready"]
        if vir is not True and vir is not False:
            raise ValueError(
                f"vector_index_ready must be a strict bool, got {vir!r}"
            )

        return FinalizationEvidence(
            indexed=vir,
            relationship_count=rc,
            artifact_path=stats.path,
            artifact_sha256=stats.sha256,
        )


def internal_import_finalizer_factory_from_env() -> Optional[FinalizerFactory]:
    """Create a FinalizerFactory from environment variables, or ``None``.

    Reads:
      - ``MAIN_API_INTERNAL_URL`` (required, stripped)
      - ``GRAPH_INTERNAL_TOKEN`` (required, stripped)
      - ``GRAPH_IMPORT_TIMEOUT`` (optional, default ``'1800'``, must be > 0)

    Returns ``None`` when URL or token is missing/empty.
    Returns a zero-arg factory that creates ``InternalImportFinalizer`` instances.
    """
    import os

    url = os.environ.get("MAIN_API_INTERNAL_URL", "").strip()
    token = os.environ.get("GRAPH_INTERNAL_TOKEN", "").strip()

    if not url or not token:
        return None

    timeout_str = os.environ.get("GRAPH_IMPORT_TIMEOUT", "1800").strip()
    try:
        timeout = float(timeout_str)
    except (ValueError, TypeError):
        raise ValueError(
            f"GRAPH_IMPORT_TIMEOUT must be a positive number, got {timeout_str!r}"
        ) from None
    if timeout <= 0:
        raise ValueError(
            f"GRAPH_IMPORT_TIMEOUT must be > 0, got {timeout}"
        )

    def _factory() -> InternalImportFinalizer:
        return InternalImportFinalizer(url, token, timeout)

    return _factory


def select_new_relationship_artifact(
    output_root: Path, snapshot: OutputSnapshot
) -> Path:
    """Return the path to the newest valid relationship artifact.

    A *candidate* directory must:
      1. be a direct child of *output_root* **not** already in
         *snapshot.directory_names*;
      2. contain a regular file at exactly ``artifacts/create_final_relationships.parquet``.

    When multiple candidates qualify, the one with the maximum
    ``(artifact.st_mtime_ns, directory_name)`` is selected.
    Raises ``FileNotFoundError`` when no valid candidate exists.
    """
    best: tuple[int, str, Path] | None = None

    if not output_root.is_dir():
        raise FileNotFoundError("No new relationship artifact found")

    for entry in output_root.iterdir():
        if not entry.is_dir():
            continue
        if entry.name in snapshot.directory_names:
            continue
        artifact = entry / _ARTIFACT_REL
        if not artifact.is_file():
            continue
        key = (artifact.stat().st_mtime_ns, entry.name)
        if best is None or key > (best[0], best[1]):
            best = (key[0], key[1], artifact)

    if best is None:
        raise FileNotFoundError("No new relationship artifact found")

    return best[2]
