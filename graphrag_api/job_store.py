"""SQLite-backed durable graph job state machine.

Thread-safety: each public method opens its own connection via
``sqlite3.connect(path)`` so concurrent threads never share a handle.
Every mutating operation uses ``BEGIN IMMEDIATE`` to serialise writes.

No API keys, no import side-effects.
"""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

from graphrag_api.schemas import (
    ACTIVE_STATUSES,
    ALLOWED_TRANSITIONS,
    JobRecord,
    TERMINAL_STATUSES,
    VALID_GRAPH_TYPES,
)


class ActiveJobError(ValueError):
    """Raised when a new job would collide with an existing active job."""


class JobStore:
    """Durable job state backed by a single SQLite file.

    Safe for multi-thread per-operation use — every method opens its own
    ``sqlite3.connect()`` so no connection is shared across threads.
    """

    _SCHEMA = """\
CREATE TABLE IF NOT EXISTS jobs (
    id                 TEXT PRIMARY KEY,
    graph_type         TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'queued',
    stage              TEXT NOT NULL DEFAULT 'queued',
    progress           INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT NOT NULL,
    started_at         TEXT,
    finished_at        TEXT,
    cancel_requested   INTEGER NOT NULL DEFAULT 0,
    input_count        INTEGER NOT NULL DEFAULT 0,
    relationship_count INTEGER NOT NULL DEFAULT 0,
    artifact_path      TEXT NOT NULL DEFAULT '',
    artifact_sha256    TEXT NOT NULL DEFAULT '',
    error_summary      TEXT NOT NULL DEFAULT '',
    log_tail           TEXT NOT NULL DEFAULT ''
);

-- Only one active (non-terminal) job per graph_type at a time.
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_graph_type
    ON jobs (graph_type)
    WHERE status NOT IN ('completed','failed','cancelled','interrupted');
"""

    def __init__(self, db_path: Path) -> None:
        self._db_path = str(db_path)
        # Ensure parent directory exists before opening the database.
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        # Create schema once; subsequent connects are no-ops.
        # Wrap everything in BEGIN IMMEDIATE so two processes cannot both
        # see a missing column and race on the ALTER TABLE.
        with self._connect() as cur:
            # executescript commits any open transaction first, then runs
            # the whole script (idempotent schema creation).  The explicit
            # BEGIN IMMEDIATE below protects the migration check.
            cur.executescript(self._SCHEMA)
            # --- migrate legacy tables missing the `stage` column ---
            # Start a fresh transaction after executescript's implicit COMMIT.
            cur.execute("BEGIN IMMEDIATE")
            cur.execute("PRAGMA table_info(jobs)")
            columns = {row[1] for row in cur.fetchall()}
            if "stage" not in columns:
                cur.execute(
                    "ALTER TABLE jobs ADD COLUMN stage TEXT NOT NULL DEFAULT 'queued'"
                )
                # Backfill stage from status for pre-existing rows.
                cur.execute(
                    "UPDATE jobs SET stage = status WHERE stage = 'queued' AND status != 'queued'"
                )
            cur.execute("COMMIT")

    # -- helpers ----------------------------------------------------------

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Cursor, None, None]:
        """Yield a cursor from a **new** connection.

        Opening per-call keeps the store safe for multi-threaded access
        without a connection pool.  The connection is closed on exit.
        """
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            yield conn.cursor()
        finally:
            conn.close()

    @staticmethod
    def _utcnow() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _clamp(text: str, byte_limit: int) -> str:
        """Truncate *text* so its UTF-8 encoding fits in *byte_limit* bytes.

        Keeps the **prefix** (first *byte_limit* bytes).  The result is always
        valid UTF-8 — a multi-byte character is never cut mid-sequence.
        """
        encoded = text.encode("utf-8")
        if len(encoded) <= byte_limit:
            return text
        return encoded[:byte_limit].decode("utf-8", errors="ignore")

    @staticmethod
    def _clamp_tail(text: str, byte_limit: int) -> str:
        """Truncate *text* keeping the **tail** (last *byte_limit* bytes).

        The result is always valid UTF-8 — bytes at the start that fall inside
        a multi-byte character are silently dropped.
        """
        encoded = text.encode("utf-8")
        if len(encoded) <= byte_limit:
            return text
        return encoded[-byte_limit:].decode("utf-8", errors="ignore")

    @staticmethod
    def _row_to_record(row: tuple) -> JobRecord:
        return JobRecord(
            id=row[0],
            graph_type=row[1],
            status=row[2],
            stage=row[3],
            progress=row[4],
            created_at=row[5],
            started_at=row[6],
            finished_at=row[7],
            cancel_requested=bool(row[8]),
            input_count=row[9],
            relationship_count=row[10],
            artifact_path=row[11],
            artifact_sha256=row[12],
            error_summary=row[13],
            log_tail=row[14],
        )

    # -- public API -------------------------------------------------------

    def create(self, graph_type: str) -> JobRecord:
        """Create a new queued job for *graph_type*.

        Raises
        ------
        ValueError
            If *graph_type* is not ``ground`` or ``drill``.
        ActiveJobError
            If an active (non-terminal) job already exists for this type.
        """
        if graph_type not in VALID_GRAPH_TYPES:
            raise ValueError(
                f"graph_type must be one of {VALID_GRAPH_TYPES}, got {graph_type!r}"
            )

        now = self._utcnow()
        job_id = uuid.uuid4().hex

        try:
            with self._connect() as cur:
                cur.execute("BEGIN IMMEDIATE")
                cur.execute(
                    "INSERT INTO jobs (id, graph_type, status, progress, created_at) "
                    "VALUES (?, ?, 'queued', 0, ?)",
                    (job_id, graph_type, now),
                )
                cur.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            raise ActiveJobError(
                f"An active job already exists for graph_type={graph_type!r}"
            ) from exc

        return self.get(job_id)  # type: ignore[return-value]

    def get(self, job_id: str) -> JobRecord | None:
        """Return the current state of *job_id*, or ``None`` if not found."""
        with self._connect() as cur:
            cur.execute(
                "SELECT id, graph_type, status, stage, progress, created_at, "
                "started_at, finished_at, cancel_requested, "
                "input_count, relationship_count, artifact_path, "
                "artifact_sha256, error_summary, log_tail "
                "FROM jobs WHERE id = ?",
                (job_id,),
            )
            row = cur.fetchone()
        return self._row_to_record(row) if row else None

    def transition(
        self,
        job_id: str,
        new_status: str,
        progress: int,
        *,
        stage: str | None = None,
        error_summary: str = "",
        log_tail: str = "",
        input_count: int | None = None,
        relationship_count: int | None = None,
        artifact_path: str | None = None,
        artifact_sha256: str | None = None,
    ) -> JobRecord:
        """Move *job_id* to *new_status* with the given *progress*.

        Raises
        ------
        KeyError
            If *job_id* does not exist.
        ValueError
            On an illegal transition or a progress violation.
        """
        with self._connect() as cur:
            cur.execute("BEGIN IMMEDIATE")
            cur.execute(
                "SELECT id, graph_type, status, stage, progress, created_at, "
                "started_at, finished_at, cancel_requested, "
                "input_count, relationship_count, artifact_path, "
                "artifact_sha256, error_summary, log_tail "
                "FROM jobs WHERE id = ?",
                (job_id,),
            )
            row = cur.fetchone()
            if row is None:
                cur.execute("ROLLBACK")
                raise KeyError(f"missing job {job_id!r}")

            current = self._row_to_record(row)
            old_status = current.status

            # --- progress must be monotonically non-decreasing ---
            if progress < current.progress:
                cur.execute("ROLLBACK")
                raise ValueError(
                    f"progress must be >= {current.progress}, got {progress}"
                )

            # --- validate transition legality ---
            allowed = ALLOWED_TRANSITIONS.get(old_status, set())
            if new_status not in allowed:
                cur.execute("ROLLBACK")
                raise ValueError(
                    f"transition {old_status!r} -> {new_status!r} is not allowed"
                )

            # --- completed requires progress == 100 and source == indexing ---
            if new_status == "completed":
                if old_status != "indexing":
                    cur.execute("ROLLBACK")
                    raise ValueError(
                        "completed can only transition from indexing"
                    )
                if progress != 100:
                    cur.execute("ROLLBACK")
                    raise ValueError(
                        "completed requires progress == 100"
                    )

            # --- timestamps ---
            now = self._utcnow()
            started_at = current.started_at
            if started_at is None and new_status not in TERMINAL_STATUSES:
                started_at = now

            finished_at = current.finished_at
            if new_status in TERMINAL_STATUSES:
                finished_at = now

            # --- stage: explicit value or keep current ---
            new_stage = stage if stage is not None else current.stage

            # --- clamp text fields ---
            clamped_error = self._clamp(error_summary, 2 * 1024) if error_summary else current.error_summary
            clamped_log = self._clamp_tail(log_tail, 32 * 1024) if log_tail else current.log_tail

            # --- optional metadata ---
            inp = input_count if input_count is not None else current.input_count
            rel = relationship_count if relationship_count is not None else current.relationship_count
            art_path = artifact_path if artifact_path is not None else current.artifact_path
            art_sha = artifact_sha256 if artifact_sha256 is not None else current.artifact_sha256

            cur.execute(
                "UPDATE jobs SET "
                "  status = ?, stage = ?, progress = ?, "
                "  started_at = ?, finished_at = ?, "
                "  error_summary = ?, log_tail = ?, "
                "  input_count = ?, relationship_count = ?, "
                "  artifact_path = ?, artifact_sha256 = ? "
                "WHERE id = ?",
                (
                    new_status,
                    new_stage,
                    progress,
                    started_at,
                    finished_at,
                    clamped_error,
                    clamped_log,
                    inp,
                    rel,
                    art_path,
                    art_sha,
                    job_id,
                ),
            )
            cur.execute("COMMIT")

        return self.get(job_id)  # type: ignore[return-value]

    def request_cancel(self, job_id: str) -> JobRecord:
        """Mark *job_id* as ``cancelling`` and set the cancel flag.

        The caller should check ``cancel_requested`` between pipeline stages
        and transition to ``cancelled`` when safe.
        """
        with self._connect() as cur:
            cur.execute("BEGIN IMMEDIATE")
            cur.execute("SELECT status, progress FROM jobs WHERE id = ?", (job_id,))
            row = cur.fetchone()
            if row is None:
                cur.execute("ROLLBACK")
                raise KeyError(f"missing job {job_id!r}")

            old_status, progress = row
            if old_status in TERMINAL_STATUSES:
                cur.execute("ROLLBACK")
                raise ValueError(
                    f"cannot cancel a job in terminal status {old_status!r}"
                )

            cur.execute(
                "UPDATE jobs SET status = 'cancelling', cancel_requested = 1 WHERE id = ?",
                (job_id,),
            )
            cur.execute("COMMIT")

        return self.get(job_id)  # type: ignore[return-value]

    def mark_running_interrupted(self) -> int:
        """Mark every non-terminal job as ``interrupted``.

        Intended to be called once on startup to recover from an unclean
        shutdown.  Returns the number of rows changed.
        """
        with self._connect() as cur:
            cur.execute("BEGIN IMMEDIATE")
            now = self._utcnow()
            cur.execute(
                "UPDATE jobs SET status = 'interrupted', finished_at = ? "
                "WHERE status NOT IN ('completed','failed','cancelled','interrupted')",
                (now,),
            )
            changed = cur.rowcount
            cur.execute("COMMIT")
        return changed

    def retry(self, job_id: str) -> JobRecord:
        """Reset a terminal job back to ``queued`` so it can run again.

        Raises
        ------
        ActiveJobError
            If another active job already exists for the same graph_type.
        """
        with self._connect() as cur:
            cur.execute("BEGIN IMMEDIATE")
            cur.execute(
                "SELECT graph_type, status FROM jobs WHERE id = ?", (job_id,),
            )
            row = cur.fetchone()
            if row is None:
                cur.execute("ROLLBACK")
                raise KeyError(f"missing job {job_id!r}")

            graph_type, status = row
            if status not in TERMINAL_STATUSES:
                cur.execute("ROLLBACK")
                raise ValueError(
                    f"can only retry a terminal job, got {status!r}"
                )

            # Check for collision with another active job of the same type.
            cur.execute(
                "SELECT id FROM jobs "
                "WHERE graph_type = ? AND id != ? "
                "AND status NOT IN ('completed','failed','cancelled','interrupted')",
                (graph_type, job_id),
            )
            if cur.fetchone() is not None:
                cur.execute("ROLLBACK")
                raise ActiveJobError(
                    f"An active job already exists for graph_type={graph_type!r}"
                )

            cur.execute(
                "UPDATE jobs SET "
                "  status = 'queued', stage = 'queued', progress = 0, "
                "  started_at = NULL, finished_at = NULL, "
                "  cancel_requested = 0, "
                "  error_summary = '', log_tail = '' "
                "WHERE id = ?",
                (job_id,),
            )
            cur.execute("COMMIT")

        return self.get(job_id)  # type: ignore[return-value]
