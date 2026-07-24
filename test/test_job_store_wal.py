"""Behavior tests for JobStore WAL-once initialization (Task 14).

Proves:
  1. WAL is requested exactly once during __init__, never on per-operation
     connections.
  2. busy_timeout is set *before* the single WAL attempt during init.
  3. Per-operation connections only apply bounded busy_timeout.
  4. A concurrency regression test with repeated readers and writer
     transitions terminates in bounded time and never strands an active job.
"""

from __future__ import annotations

import contextlib
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import List, Tuple
from unittest.mock import patch

from graphrag_api.job_store import JobStore


# ---------------------------------------------------------------------------
# Tracing helper: wraps a real sqlite3.Connection to record every PRAGMA call
# ---------------------------------------------------------------------------


class _TracingCursor:
    """Wraps a real sqlite3.Cursor to record PRAGMA calls."""

    def __init__(self, cursor: sqlite3.Cursor, conn_idx: int,
                 call_log: list, lock: threading.Lock) -> None:
        self._cur = cursor
        self._conn_idx = conn_idx
        self._call_log = call_log
        self._lock = lock

    def __getattr__(self, name: str):
        return getattr(self._cur, name)

    def execute(self, sql: str, *args, **kwargs):  # type: ignore[override]
        if sql.strip().upper().startswith("PRAGMA"):
            with self._lock:
                self._call_log.append((self._conn_idx, sql.strip()))
        return self._cur.execute(sql, *args, **kwargs)

    def executescript(self, script: str):  # type: ignore[override]
        return self._cur.executescript(script)

    def fetchall(self):  # type: ignore[override]
        return self._cur.fetchall()

    def fetchone(self):  # type: ignore[override]
        return self._cur.fetchone()


class _TracingConnection:
    """Thin wrapper around a real sqlite3.Connection that records every
    ``PRAGMA ...`` execute call.  Delegates everything else."""

    def __init__(self, conn: sqlite3.Connection, conn_idx: int,
                 call_log: list, lock: threading.Lock) -> None:
        self._conn = conn
        self._conn_idx = conn_idx
        self._call_log = call_log
        self._lock = lock

    # Delegate all attributes we don't intercept.
    def __getattr__(self, name: str):
        return getattr(self._conn, name)

    def execute(self, sql: str, *args, **kwargs):  # type: ignore[override]
        if sql.strip().upper().startswith("PRAGMA"):
            with self._lock:
                self._call_log.append((self._conn_idx, sql.strip()))
        return self._conn.execute(sql, *args, **kwargs)

    def executescript(self, script: str):  # type: ignore[override]
        return self._conn.executescript(script)

    def cursor(self):  # type: ignore[override]
        return _TracingCursor(
            self._conn.cursor(), self._conn_idx, self._call_log, self._lock,
        )

    def commit(self):  # type: ignore[override]
        return self._conn.commit()

    def close(self):  # type: ignore[override]
        return self._conn.close()


class _PragmaTracer:
    """Intercepts ``sqlite3.connect`` to record every ``PRAGMA ...`` call,
    preserving the (connection_index, sql) pair."""

    def __init__(self) -> None:
        self._calls: List[Tuple[int, str]] = []
        self._conn_counter = 0
        self._lock = threading.Lock()

    @property
    def calls(self) -> List[Tuple[int, str]]:
        with self._lock:
            return list(self._calls)

    @property
    def wal_calls(self) -> List[Tuple[int, str]]:
        return [
            (idx, sql)
            for idx, sql in self.calls
            if "journal_mode" in sql.lower()
        ]

    @property
    def busy_timeout_calls(self) -> List[Tuple[int, str]]:
        return [
            (idx, sql)
            for idx, sql in self.calls
            if "busy_timeout" in sql.lower()
        ]

    def make_factory(self):
        """Return a patched ``sqlite3.connect`` that traces PRAGMAs."""
        original_connect = sqlite3.connect
        tracer = self

        def _traced_connect(path, *args, **kwargs):
            with tracer._lock:
                tracer._conn_counter += 1
                conn_idx = tracer._conn_counter
            conn = original_connect(path, *args, **kwargs)
            return _TracingConnection(conn, conn_idx, tracer._calls, tracer._lock)

        return _traced_connect


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestWALOnceOnInit(unittest.TestCase):
    """Prove WAL is requested exactly once during __init__."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self.db_path = Path(self._tmp) / "jobs.db"
        self.tracer = _PragmaTracer()

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_wal_requested_exactly_once_during_init(self) -> None:
        """PRAGMA journal_mode=WAL must appear exactly once, and that one
        call must happen on the very first connection (the init connection)."""
        with patch("sqlite3.connect", side_effect=self.tracer.make_factory()):
            store = JobStore(self.db_path)

        wal = self.tracer.wal_calls
        self.assertEqual(len(wal), 1, f"Expected exactly 1 WAL call, got {len(wal)}: {wal}")
        # The single WAL call must be on connection index 1 (init connection)
        self.assertEqual(wal[0][0], 1, "WAL must be set on the first (init) connection")

    def test_wal_not_requested_on_subsequent_operations(self) -> None:
        """After init, create/get/transition must NOT re-request WAL."""
        with patch("sqlite3.connect", side_effect=self.tracer.make_factory()):
            store = JobStore(self.db_path)
            # Clear init-phase calls
            init_call_count = len(self.tracer.calls)

            # Exercise every public method
            job = store.create("ground")
            store.get(job.id)
            store.transition(job.id, "copying", 5)
            store.transition(job.id, "building", 30)
            store.transition(job.id, "converting", 72)
            store.transition(job.id, "importing", 80)
            store.transition(job.id, "indexing", 95)
            store.transition(job.id, "completed", 100)

        wal_after_init = [
            (idx, sql)
            for idx, sql in self.tracer.calls[init_call_count:]
            if "journal_mode" in sql.lower()
        ]
        self.assertEqual(
            len(wal_after_init),
            0,
            f"Per-operation connections must NOT request WAL: {wal_after_init}",
        )


class TestBusyTimeoutBeforeWAL(unittest.TestCase):
    """Prove busy_timeout is set *before* the WAL attempt during init."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self.db_path = Path(self._tmp) / "jobs.db"
        self.tracer = _PragmaTracer()

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_busy_timeout_before_wal_on_init_connection(self) -> None:
        """On the init connection, busy_timeout must be set before
        journal_mode=WAL, so the timeout applies to the WAL lock wait."""
        with patch("sqlite3.connect", side_effect=self.tracer.make_factory()):
            store = JobStore(self.db_path)

        # Filter init connection calls only (connection index 1)
        init_calls = [sql for idx, sql in self.tracer.calls if idx == 1]
        pragmas = [s for s in init_calls if s.upper().startswith("PRAGMA")]

        # Find positions
        busy_pos = None
        wal_pos = None
        for i, p in enumerate(pragmas):
            if "busy_timeout" in p.lower():
                busy_pos = i
            if "journal_mode" in p.lower():
                wal_pos = i

        self.assertIsNotNone(busy_pos, f"busy_timeout not found in pragmas: {pragmas}")
        self.assertIsNotNone(wal_pos, f"journal_mode=WAL not found in pragmas: {pragmas}")
        self.assertLess(
            busy_pos,
            wal_pos,
            f"busy_timeout (pos {busy_pos}) must come before WAL (pos {wal_pos}): {pragmas}",
        )


class TestPerOperationConnections(unittest.TestCase):
    """Prove per-operation connections apply busy_timeout but not WAL."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self.db_path = Path(self._tmp) / "jobs.db"
        self.tracer = _PragmaTracer()

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_per_operation_only_sets_busy_timeout(self) -> None:
        """Every per-operation connection (after init) must set
        busy_timeout but never journal_mode."""
        with patch("sqlite3.connect", side_effect=self.tracer.make_factory()):
            store = JobStore(self.db_path)
            init_count = len(self.tracer.calls)

            job = store.create("ground")
            store.get(job.id)
            store.transition(job.id, "copying", 5)
            store.transition(job.id, "building", 30)
            store.transition(job.id, "converting", 72)
            store.transition(job.id, "importing", 80)
            store.transition(job.id, "indexing", 95)
            store.transition(job.id, "completed", 100)

        after_init = self.tracer.calls[init_count:]
        # Group by connection index
        conn_ids = {idx for idx, _ in after_init}
        for conn_id in conn_ids:
            conn_pragmas = [
                sql for idx, sql in after_init if idx == conn_id
            ]
            has_wal = any("journal_mode" in p.lower() for p in conn_pragmas)
            has_busy = any("busy_timeout" in p.lower() for p in conn_pragmas)
            self.assertFalse(
                has_wal,
                f"Connection {conn_id} should NOT set WAL: {conn_pragmas}",
            )
            self.assertTrue(
                has_busy,
                f"Connection {conn_id} should set busy_timeout: {conn_pragmas}",
            )

    def test_connect_uses_bounded_timeout(self) -> None:
        """sqlite3.connect must be called with a timeout= parameter
        on per-operation connections."""
        connect_calls: list = []
        original_connect = sqlite3.connect

        def _spy_connect(path, *args, **kwargs):
            connect_calls.append({"path": path, "args": args, "kwargs": kwargs})
            return original_connect(path, *args, **kwargs)

        with patch("sqlite3.connect", side_effect=_spy_connect):
            store = JobStore(self.db_path)
            init_count = len(connect_calls)

            job = store.create("ground")

        # After init, the create() call should have timeout in kwargs
        for call in connect_calls[init_count:]:
            self.assertIn(
                "timeout",
                call["kwargs"],
                f"sqlite3.connect missing timeout= kwarg: {call['kwargs']}",
            )
            self.assertGreater(
                call["kwargs"]["timeout"],
                0,
                "timeout must be positive",
            )


class TestConcurrencyRegression(unittest.TestCase):
    """Repeated readers and a writer must terminate in bounded time.

    This is the production scenario: the pipeline worker thread writes
    job transitions while the API read thread calls get() concurrently.
    The old code could deadlock because every connection renegotiated WAL.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self.db_path = Path(self._tmp) / "jobs.db"
        self.store = JobStore(self.db_path)

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_concurrent_readers_and_writer_bounded_termination(self) -> None:
        """Spawn 4 reader threads and 1 writer thread.  The writer
        transitions a job through the full lifecycle while readers
        repeatedly call get().  All threads must join within 15 seconds
        and the job must reach a terminal state."""
        job = self.store.create("ground")
        errors: List[str] = []
        done = threading.Event()

        def _reader() -> None:
            """Read the job record until the writer signals done."""
            try:
                while not done.is_set():
                    rec = self.store.get(job.id)
                    assert rec is not None
                    time.sleep(0.001)
            except Exception as exc:
                errors.append(f"reader error: {exc}")

        def _writer() -> None:
            """Walk the job through every status to terminal."""
            try:
                transitions = [
                    ("copying", 5),
                    ("building", 10),
                    ("building", 40),
                    ("converting", 72),
                    ("importing", 80),
                    ("indexing", 95),
                    ("completed", 100),
                ]
                for status, progress in transitions:
                    self.store.transition(job.id, status, progress)
                    time.sleep(0.002)
            except Exception as exc:
                errors.append(f"writer error: {exc}")
            finally:
                done.set()

        readers = [threading.Thread(target=_reader, daemon=True) for _ in range(4)]
        writer = threading.Thread(target=_writer, daemon=True)

        t0 = time.monotonic()
        for r in readers:
            r.start()
        writer.start()

        writer.join(timeout=10)
        done.set()  # unblock any remaining readers
        for r in readers:
            r.join(timeout=5)

        elapsed = time.monotonic() - t0

        self.assertFalse(writer.is_alive(), "Writer thread did not finish")
        for i, r in enumerate(readers):
            self.assertFalse(r.is_alive(), f"Reader {i} did not finish")
        self.assertEqual(errors, [], f"Thread errors: {errors}")
        self.assertLess(elapsed, 15.0, f"Took {elapsed:.1f}s -- likely deadlock")

        rec = self.store.get(job.id)
        self.assertEqual(rec.status, "completed")
        self.assertEqual(rec.progress, 100)

    def test_no_stranded_active_job_after_concurrent_transitions(self) -> None:
        """After concurrent write operations complete, the store must
        allow a new job of the same type -- the active slot must be free."""
        job = self.store.create("ground")
        self.store.transition(job.id, "copying", 5)
        self.store.transition(job.id, "building", 30)
        self.store.transition(job.id, "converting", 72)
        self.store.transition(job.id, "importing", 80)
        self.store.transition(job.id, "indexing", 95)
        self.store.transition(job.id, "completed", 100)

        # After terminal state, we can create a new job for the same type
        job2 = self.store.create("ground")
        self.assertEqual(job2.status, "queued")


class TestLegacyMigrationWithWALOnce(unittest.TestCase):
    """Ensure the stage migration still works after the WAL-once refactor."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_legacy_db_migrated_and_wal_set_once(self) -> None:
        """A legacy database without the stage column must be migrated,
        and WAL must still be configured exactly once."""
        tracer = _PragmaTracer()
        legacy_path = Path(self._tmp) / "legacy" / "jobs.db"
        legacy_path.parent.mkdir(parents=True)

        # Create legacy schema — contextlib.closing ensures the connection
        # is closed (sqlite3 context-manager only commits/rolls back).
        with contextlib.closing(sqlite3.connect(legacy_path)) as conn:
            conn.execute("""
                CREATE TABLE jobs (
                    id TEXT PRIMARY KEY,
                    graph_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    progress INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    input_count INTEGER NOT NULL DEFAULT 0,
                    relationship_count INTEGER NOT NULL DEFAULT 0,
                    artifact_path TEXT NOT NULL DEFAULT '',
                    artifact_sha256 TEXT NOT NULL DEFAULT '',
                    error_summary TEXT NOT NULL DEFAULT '',
                    log_tail TEXT NOT NULL DEFAULT ''
                )
            """)
            conn.execute(
                "INSERT INTO jobs (id, graph_type, status, progress, created_at) "
                "VALUES ('old-job', 'ground', 'failed', 37, '2026-01-01T00:00:00+00:00')"
            )
            conn.commit()

        with patch("sqlite3.connect", side_effect=tracer.make_factory()):
            migrated_store = JobStore(legacy_path)
            job = migrated_store.get("old-job")

        self.assertIsNotNone(job)
        self.assertEqual(job.stage, "failed")
        self.assertEqual(job.progress, 37)

        wal = tracer.wal_calls
        self.assertEqual(len(wal), 1, f"Expected 1 WAL call after migration, got {len(wal)}")


class TestConnectPragmaFailure(unittest.TestCase):
    """If PRAGMA busy_timeout raises inside _connect(), the connection
    must still be closed (no leak)."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self.db_path = Path(self._tmp) / "jobs.db"

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_pragma_failure_closes_connection(self) -> None:
        """When PRAGMA busy_timeout raises, _connect() must close the
        connection and propagate the exception."""
        store = JobStore(self.db_path)

        # Build a fake connection whose PRAGMA execute raises and that
        # records whether close() was called.
        class _FakeConnection:
            def __init__(self):
                self.closed = False

            def execute(self, sql, *a, **kw):
                if sql.strip().upper().startswith("PRAGMA"):
                    raise sqlite3.OperationalError("fake PRAGMA failure")

            def cursor(self):
                return self

            def close(self):
                self.closed = True

        fake = _FakeConnection()

        def _connect_returns_fake(*args, **kwargs):
            return fake

        with patch("sqlite3.connect", side_effect=_connect_returns_fake):
            with self.assertRaises(sqlite3.OperationalError):
                with store._connect():
                    pass  # pragma: no cover

        self.assertTrue(
            fake.closed,
            "Connection must be closed even when PRAGMA busy_timeout raises",
        )


if __name__ == "__main__":
    unittest.main()
