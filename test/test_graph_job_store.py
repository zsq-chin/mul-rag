import sqlite3
import tempfile
import unittest
from pathlib import Path

from graphrag_api.job_store import ActiveJobError, JobStore


class GraphJobStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = JobStore(Path(self.tmp.name) / "jobs.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_progress_is_monotonic_and_completion_requires_indexing(self):
        job = self.store.create("ground")
        self.assertEqual(job.stage, "queued")
        self.store.transition(job.id, "copying", 5)
        self.store.transition(job.id, "building", 30)

        with self.assertRaisesRegex(ValueError, "progress"):
            self.store.transition(job.id, "building", 20)
        with self.assertRaisesRegex(ValueError, "transition"):
            self.store.transition(job.id, "completed", 100)

        self.store.transition(job.id, "converting", 72)
        self.store.transition(job.id, "importing", 80)
        self.store.transition(job.id, "indexing", 95)
        completed = self.store.transition(job.id, "completed", 100)

        self.assertEqual(completed.status, "completed")
        self.assertEqual(completed.progress, 100)
        self.assertIsNotNone(completed.finished_at)

    def test_active_stage_can_report_monotonic_progress_and_detail(self):
        job = self.store.create("ground")
        self.store.transition(job.id, "copying", 5)
        self.store.transition(job.id, "building", 10)

        updated = self.store.transition(job.id, "building", 45, stage="create_entities")

        self.assertEqual(updated.status, "building")
        self.assertEqual(updated.stage, "create_entities")
        self.assertEqual(updated.progress, 45)

    def test_only_one_active_job_per_graph_type(self):
        self.store.create("drill")

        with self.assertRaisesRegex(ActiveJobError, "active"):
            self.store.create("drill")

        self.store.create("ground")

    def test_restart_marks_all_unfinished_jobs_interrupted(self):
        queued = self.store.create("ground")
        building = self.store.create("drill")
        self.store.transition(building.id, "copying", 5)
        self.store.transition(building.id, "building", 25)

        changed = self.store.mark_running_interrupted()

        self.assertEqual(changed, 2)
        self.assertEqual(self.store.get(queued.id).status, "interrupted")
        self.assertEqual(self.store.get(building.id).status, "interrupted")

    def test_cancel_request_is_persisted_and_can_finish_cancelled(self):
        job = self.store.create("ground")
        cancelling = self.store.request_cancel(job.id)

        self.assertEqual(cancelling.status, "cancelling")
        self.assertTrue(cancelling.cancel_requested)

        cancelled = self.store.transition(job.id, "cancelled", cancelling.progress)
        self.assertEqual(cancelled.status, "cancelled")
        self.assertIsNotNone(cancelled.finished_at)

    def test_retry_resets_a_terminal_job(self):
        job = self.store.create("ground")
        self.store.transition(job.id, "failed", 0, error_summary="failed once")

        retried = self.store.retry(job.id)

        self.assertEqual(retried.id, job.id)
        self.assertEqual(retried.status, "queued")
        self.assertEqual(retried.progress, 0)
        self.assertFalse(retried.cancel_requested)
        self.assertIsNone(retried.started_at)
        self.assertIsNone(retried.finished_at)
        self.assertEqual(retried.error_summary, "")
        self.assertEqual(retried.stage, "queued")

    def test_terminal_job_cannot_bypass_retry_with_transition(self):
        job = self.store.create("ground")
        self.store.transition(job.id, "failed", 0)

        with self.assertRaisesRegex(ValueError, "transition"):
            self.store.transition(job.id, "queued", 0)

    def test_retry_rejects_collision_with_another_active_job(self):
        old_job = self.store.create("ground")
        self.store.transition(old_job.id, "failed", 0)
        self.store.create("ground")

        with self.assertRaisesRegex(ActiveJobError, "active"):
            self.store.retry(old_job.id)

    def test_error_and_log_fields_are_bounded_by_utf8_bytes(self):
        job = self.store.create("ground")
        newest_log = "旧" * 20000 + "最新一行"
        failed = self.store.transition(
            job.id,
            "failed",
            0,
            error_summary="错" * 3000,
            log_tail=newest_log,
        )

        self.assertLessEqual(len(failed.error_summary.encode("utf-8")), 2 * 1024)
        self.assertLessEqual(len(failed.log_tail.encode("utf-8")), 32 * 1024)
        self.assertTrue(failed.log_tail.endswith("最新一行"))

    def test_active_job_error_is_a_value_error(self):
        self.assertTrue(issubclass(ActiveJobError, ValueError))

    def test_existing_database_without_stage_is_migrated(self):
        legacy_path = Path(self.tmp.name) / "legacy" / "jobs.db"
        legacy_path.parent.mkdir(parents=True)
        with sqlite3.connect(legacy_path) as connection:
            connection.execute(
                """
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
                """
            )
            connection.execute(
                """
                INSERT INTO jobs (id, graph_type, status, progress, created_at)
                VALUES ('legacy-job', 'ground', 'failed', 37, '2026-01-01T00:00:00+00:00')
                """
            )
            connection.commit()
        connection.close()

        migrated_store = JobStore(legacy_path)
        migrated_job = migrated_store.get("legacy-job")

        self.assertIsNotNone(migrated_job)
        self.assertEqual(migrated_job.stage, "failed")
        self.assertEqual(migrated_job.progress, 37)

    def test_unknown_graph_type_and_job_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "graph_type"):
            self.store.create("unknown")
        with self.assertRaisesRegex(KeyError, "missing"):
            self.store.transition("missing", "copying", 5)


if __name__ == "__main__":
    unittest.main()
