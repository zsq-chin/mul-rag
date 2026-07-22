"""Tests for graphrag_api.pipeline -- conversion, OutputSnapshot, snapshot and selector APIs.

ConvertRelationshipsTests (10 tests, real pandas parquet):
  exact h,r,t header, trim, drop any blank, exact dedupe, h/r/t sort, rows
  count, 64 lowercase hex digest, digest equals file bytes, byte/digest
  determinism, missing source/target/description errors.

Selection tests (future public APIs, TDD red step):
  - OutputSnapshot dataclass with directory_names (frozenset) and started_at_ns:int.
  - snapshot_output_directories(output_root: Path) -> OutputSnapshot.
  - select_new_relationship_artifact(output_root: Path, snapshot: OutputSnapshot) -> Path.

Required behavior encoded:
  1. snapshot captures only existing direct child directory names and a positive
     wall-clock time_ns marker. Missing output root is treated as an empty
     snapshot, not an error.
  2. selector never chooses a pre-existing directory from snapshot, even when
     that stale directory has the newest mtime.
  3. candidate must be a newly named direct child directory containing exactly
     artifacts/create_final_relationships.parquet as a file.
  4. when no valid new candidate exists, raise FileNotFoundError with a clear
     message.
  5. multiple valid new candidates are selected deterministically by max
     (artifact mtime_ns, directory name); a same-mtime tie resolves to
     lexicographically greatest directory name.
  6. membership in snapshot is primary: a valid newly named candidate remains
     eligible even if its artifact mtime is rounded to before
     snapshot.started_at_ns.
"""

import hashlib
import os
import tempfile
import time
import unittest
from pathlib import Path

from graphrag_api.pipeline import (
    ArtifactStats,
    OutputSnapshot,
    convert_relationships,
    select_new_relationship_artifact,
    snapshot_output_directories,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_PARQUET_MAGIC = b"PAR1"  # Minimal parquet footer marker for placeholder bytes


def _write_placeholder_parquet(path: Path) -> None:
    """Write a minimal placeholder file that looks like a parquet file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_PARQUET_MAGIC)


def _make_output_root(tmp: Path, dirs: dict[str, bool]) -> Path:
    """Create output_root with named child directories.

    Args:
        tmp: parent temp directory.
        dirs: mapping of directory_name -> whether to create the parquet artifact.
    """
    root = tmp / "output"
    root.mkdir()
    for name, with_artifact in dirs.items():
        child = root / name
        child.mkdir()
        if with_artifact:
            _write_placeholder_parquet(
                child / "artifacts" / "create_final_relationships.parquet"
            )
    return root


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class ConvertRelationshipsTests(unittest.TestCase):
    """Tests for convert_relationships using real pandas parquet."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.parquet_path = self.tmp / "input.parquet"
        self.csv_path = self.tmp / "output.csv"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_parquet(self, rows: list[dict]) -> None:
        """Write a real parquet file with GraphRAG extra columns."""
        import pandas as pd

        df = pd.DataFrame(rows)
        df.to_parquet(self.parquet_path, index=False)

    def test_exact_hrt_header(self) -> None:
        """CSV must have exactly h,r,t as the header."""
        self._write_parquet([
            {"source": "a", "target": "b", "description": "c"},
        ])
        convert_relationships(self.parquet_path, self.csv_path)
        lines = self.csv_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "h,r,t")

    def test_trim_whitespace(self) -> None:
        """Values must be trimmed of surrounding whitespace."""
        self._write_parquet([
            {"source": " a ", "target": " b ", "description": " c "},
        ])
        result = convert_relationships(self.parquet_path, self.csv_path)
        lines = self.csv_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[1], "a,c,b")
        self.assertEqual(result.rows, 1)

    def test_drop_any_blank(self) -> None:
        """Rows where any of h, r, t is blank after normalization must be dropped."""
        self._write_parquet([
            {"source": "a", "target": "b", "description": "c"},
            {"source": "", "target": "b", "description": "c"},
            {"source": "a", "target": "", "description": "c"},
            {"source": "a", "target": "b", "description": ""},
            {"source": None, "target": "b", "description": "c"},
        ])
        result = convert_relationships(self.parquet_path, self.csv_path)
        lines = self.csv_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)  # header + 1 valid row
        self.assertEqual(result.rows, 1)

    def test_exact_dedupe(self) -> None:
        """Exact duplicate rows must be deduplicated."""
        self._write_parquet([
            {"source": "a", "target": "b", "description": "c"},
            {"source": "a", "target": "b", "description": "c"},
            {"source": "a", "target": "b", "description": "c"},
        ])
        result = convert_relationships(self.parquet_path, self.csv_path)
        lines = self.csv_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)  # header + 1 deduplicated row
        self.assertEqual(result.rows, 1)

    def test_hrt_sort(self) -> None:
        """Rows must be sorted by h, r, t."""
        self._write_parquet([
            {"source": "z", "target": "a", "description": "m"},
            {"source": "a", "target": "z", "description": "m"},
            {"source": "a", "target": "a", "description": "z"},
        ])
        convert_relationships(self.parquet_path, self.csv_path)
        lines = self.csv_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[1], "a,m,z")
        self.assertEqual(lines[2], "a,z,a")
        self.assertEqual(lines[3], "z,m,a")

    def test_rows_count(self) -> None:
        """ArtifactStats.rows must equal the number of data rows written."""
        self._write_parquet([
            {"source": "a", "target": "b", "description": "c"},
            {"source": "d", "target": "e", "description": "f"},
        ])
        result = convert_relationships(self.parquet_path, self.csv_path)
        self.assertEqual(result.rows, 2)

    def test_sha256_64_lowercase_hex(self) -> None:
        """sha256 must be a 64-character lowercase hex string."""
        self._write_parquet([
            {"source": "a", "target": "b", "description": "c"},
        ])
        result = convert_relationships(self.parquet_path, self.csv_path)
        self.assertEqual(len(result.sha256), 64)
        self.assertRegex(result.sha256, r"^[0-9a-f]{64}$")

    def test_digest_equals_file_bytes(self) -> None:
        """sha256 must equal the hash of the actual file bytes."""
        import hashlib

        self._write_parquet([
            {"source": "a", "target": "b", "description": "c"},
        ])
        result = convert_relationships(self.parquet_path, self.csv_path)
        expected = hashlib.sha256(self.csv_path.read_bytes()).hexdigest()
        self.assertEqual(result.sha256, expected)

    def test_byte_and_digest_determinism(self) -> None:
        """Running conversion twice must produce identical bytes and digest."""
        self._write_parquet([
            {"source": "a", "target": "b", "description": "c"},
            {"source": "x", "target": "y", "description": "z"},
        ])
        result1 = convert_relationships(self.parquet_path, self.csv_path)
        bytes1 = self.csv_path.read_bytes()
        result2 = convert_relationships(self.parquet_path, self.csv_path)
        bytes2 = self.csv_path.read_bytes()
        self.assertEqual(bytes1, bytes2)
        self.assertEqual(result1.sha256, result2.sha256)

    def test_missing_source_raises(self) -> None:
        """Missing 'source' column must raise ValueError."""
        self._write_parquet([
            {"target": "b", "description": "c"},
        ])
        with self.assertRaises(ValueError) as ctx:
            convert_relationships(self.parquet_path, self.csv_path)
        self.assertIn("source", str(ctx.exception))

    def test_missing_target_raises(self) -> None:
        """Missing 'target' column must raise ValueError."""
        self._write_parquet([
            {"source": "a", "description": "c"},
        ])
        with self.assertRaises(ValueError) as ctx:
            convert_relationships(self.parquet_path, self.csv_path)
        self.assertIn("target", str(ctx.exception))

    def test_missing_description_raises(self) -> None:
        """Missing 'description' column must raise ValueError."""
        self._write_parquet([
            {"source": "a", "target": "b"},
        ])
        with self.assertRaises(ValueError) as ctx:
            convert_relationships(self.parquet_path, self.csv_path)
        self.assertIn("description", str(ctx.exception))


class OutputSnapshotDataclassTests(unittest.TestCase):
    """Tests for OutputSnapshot dataclass shape and immutability."""

    def test_has_directory_names_field(self) -> None:
        """OutputSnapshot must expose directory_names as a frozenset."""
        snap = OutputSnapshot(directory_names=frozenset({"a", "b"}), started_at_ns=100)
        self.assertIsInstance(snap.directory_names, frozenset)
        self.assertEqual(snap.directory_names, frozenset({"a", "b"}))

    def test_has_started_at_ns_field(self) -> None:
        """OutputSnapshot must expose started_at_ns as a positive int."""
        snap = OutputSnapshot(directory_names=frozenset(), started_at_ns=123)
        self.assertIsInstance(snap.started_at_ns, int)
        self.assertGreater(snap.started_at_ns, 0)

    def test_directory_names_is_immutable(self) -> None:
        """directory_names must be immutable (frozenset), not a mutable set."""
        snap = OutputSnapshot(directory_names=frozenset({"x"}), started_at_ns=1)
        with self.assertRaises(AttributeError):
            snap.directory_names.add("y")  # type: ignore[union-attr]


class SnapshotOutputDirectoriesTests(unittest.TestCase):
    """Tests for snapshot_output_directories."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_captures_existing_child_directory_names(self) -> None:
        """Snapshot must contain the names of existing direct child directories."""
        root = _make_output_root(self.tmp, {"run_a": False, "run_b": False})
        snap = snapshot_output_directories(root)
        self.assertEqual(snap.directory_names, frozenset({"run_a", "run_b"}))

    def test_does_not_capture_files(self) -> None:
        """Snapshot must only contain directories, not files."""
        root = self.tmp / "output"
        root.mkdir()
        (root / "child_dir").mkdir()
        (root / "a_file.txt").write_text("hello")
        snap = snapshot_output_directories(root)
        self.assertEqual(snap.directory_names, frozenset({"child_dir"}))

    def test_does_not_capture_nested_directories(self) -> None:
        """Snapshot must only include direct children, not grandchildren."""
        root = self.tmp / "output"
        root.mkdir()
        child = root / "child_dir"
        child.mkdir()
        (child / "grandchild").mkdir()
        snap = snapshot_output_directories(root)
        self.assertNotIn("grandchild", snap.directory_names)

    def test_started_at_ns_is_positive_wall_clock(self) -> None:
        """started_at_ns must be a positive wall-clock time_ns marker."""
        before = time.time_ns()
        root = self.tmp / "output"
        root.mkdir()
        snap = snapshot_output_directories(root)
        after = time.time_ns()
        self.assertGreaterEqual(snap.started_at_ns, before)
        self.assertLessEqual(snap.started_at_ns, after)

    def test_missing_root_returns_empty_snapshot_not_error(self) -> None:
        """Missing output root is treated as an empty snapshot, not an error."""
        missing = self.tmp / "does_not_exist"
        snap = snapshot_output_directories(missing)
        self.assertEqual(snap.directory_names, frozenset())
        self.assertGreater(snap.started_at_ns, 0)

    def test_empty_root_returns_empty_snapshot(self) -> None:
        """An existing root with no children yields an empty snapshot."""
        root = self.tmp / "output"
        root.mkdir()
        snap = snapshot_output_directories(root)
        self.assertEqual(snap.directory_names, frozenset())


class SelectNewRelationshipArtifactTests(unittest.TestCase):
    """Tests for select_new_relationship_artifact."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- requirement 2: stale directory with newest mtime is not chosen -------

    def test_rejects_preexisting_directory_even_with_newest_mtime(self) -> None:
        """Selector never chooses a pre-existing directory from snapshot,
        even when that stale directory has the newest mtime."""
        root = _make_output_root(self.tmp, {"stale_run": True})
        snap = snapshot_output_directories(root)
        # Create a new valid candidate after the snapshot
        _write_placeholder_parquet(
            root / "new_run" / "artifacts" / "create_final_relationships.parquet"
        )
        # Make the stale dir have a newer mtime by touching the parquet artifact
        stale_artifact = (
            root / "stale_run" / "artifacts" / "create_final_relationships.parquet"
        )
        future_ns = time.time_ns() + 60_000_000_000  # 60s in the future
        os.utime(stale_artifact, ns=(future_ns, future_ns))

        result = select_new_relationship_artifact(root, snap)
        self.assertEqual(result.parent.parent.name, "new_run")

    # -- requirement 3: candidate must contain the exact parquet file ---------

    def test_candidate_must_contain_artifact_file(self) -> None:
        """A new directory without the required parquet file is not a candidate."""
        root = _make_output_root(self.tmp, {"old_run": True})
        snap = snapshot_output_directories(root)
        # new_run exists but has no artifact
        (root / "new_run").mkdir()
        with self.assertRaises(FileNotFoundError):
            select_new_relationship_artifact(root, snap)

    def test_artifact_must_be_at_exact_path(self) -> None:
        """The parquet file must be at exactly artifacts/create_final_relationships.parquet."""
        root = _make_output_root(self.tmp, {"old_run": True})
        snap = snapshot_output_directories(root)
        wrong = root / "new_run" / "artifacts" / "wrong_name.parquet"
        _write_placeholder_parquet(wrong)
        with self.assertRaises(FileNotFoundError):
            select_new_relationship_artifact(root, snap)

    def test_artifact_must_be_file_not_directory(self) -> None:
        """The artifact path must be a file, not a directory."""
        root = _make_output_root(self.tmp, {"old_run": True})
        snap = snapshot_output_directories(root)
        (root / "new_run" / "artifacts" / "create_final_relationships.parquet").mkdir(
            parents=True
        )
        with self.assertRaises(FileNotFoundError):
            select_new_relationship_artifact(root, snap)

    # -- requirement 4: no valid candidate -> FileNotFoundError ---------------

    def test_no_new_candidates_raises_file_not_found(self) -> None:
        """When no valid new candidate exists, raise FileNotFoundError."""
        root = _make_output_root(self.tmp, {"run_a": True, "run_b": True})
        snap = snapshot_output_directories(root)
        with self.assertRaises(FileNotFoundError) as ctx:
            select_new_relationship_artifact(root, snap)
        self.assertIn("No new", str(ctx.exception))

    def test_empty_output_root_raises_file_not_found(self) -> None:
        """An empty output root with an empty snapshot raises FileNotFoundError."""
        root = self.tmp / "output"
        root.mkdir()
        snap = OutputSnapshot(directory_names=frozenset(), started_at_ns=time.time_ns())
        with self.assertRaises(FileNotFoundError):
            select_new_relationship_artifact(root, snap)

    # -- requirement 5: deterministic selection by max (mtime_ns, name) -------

    def test_selects_by_max_mtime_then_name(self) -> None:
        """Multiple candidates are selected by max (artifact mtime_ns, dir name)."""
        root = _make_output_root(self.tmp, {"old": True})
        snap = snapshot_output_directories(root)
        # Create two new candidates
        for name in ("run_early", "run_late"):
            _write_placeholder_parquet(
                root / name / "artifacts" / "create_final_relationships.parquet"
            )
        # Make run_late's artifact have a newer mtime
        early_artifact = (
            root / "run_early" / "artifacts" / "create_final_relationships.parquet"
        )
        late_artifact = (
            root / "run_late" / "artifacts" / "create_final_relationships.parquet"
        )
        now = time.time_ns()
        os.utime(early_artifact, ns=(now, now))
        os.utime(late_artifact, ns=(now + 1000, now + 1000))

        result = select_new_relationship_artifact(root, snap)
        self.assertEqual(result.parent.parent.name, "run_late")

    def test_same_mtime_tie_resolves_to_greatest_directory_name(self) -> None:
        """When two candidates have the same artifact mtime, the
        lexicographically greatest directory name wins."""
        root = _make_output_root(self.tmp, {"old": True})
        snap = snapshot_output_directories(root)
        for name in ("aaa_run", "zzz_run"):
            _write_placeholder_parquet(
                root / name / "artifacts" / "create_final_relationships.parquet"
            )
        # Set identical mtimes
        now = time.time_ns()
        for name in ("aaa_run", "zzz_run"):
            artifact = (
                root / name / "artifacts" / "create_final_relationships.parquet"
            )
            os.utime(artifact, ns=(now, now))

        result = select_new_relationship_artifact(root, snap)
        self.assertEqual(result.parent.parent.name, "zzz_run")

    # -- requirement 6: snapshot membership is primary -----------------------

    def test_new_candidate_eligible_even_if_mtime_before_snapshot(self) -> None:
        """A valid newly named candidate remains eligible even if its artifact
        mtime is rounded to before snapshot.started_at_ns. Snapshot membership
        is the primary filter, not mtime comparison."""
        # Create output with one pre-existing dir
        root = _make_output_root(self.tmp, {"old_run": True})
        snap = snapshot_output_directories(root)
        # Create a new candidate AFTER snapshot
        _write_placeholder_parquet(
            root / "new_run" / "artifacts" / "create_final_relationships.parquet"
        )
        # Force its artifact mtime to well before the snapshot time
        artifact = (
            root / "new_run" / "artifacts" / "create_final_relationships.parquet"
        )
        past_ns = snap.started_at_ns - 10_000_000_000  # 10s before snapshot
        os.utime(artifact, ns=(past_ns, past_ns))

        result = select_new_relationship_artifact(root, snap)
        self.assertEqual(result.parent.parent.name, "new_run")

    # -- basic contract: returns a Path to the artifact ----------------------

    def test_returns_path_to_artifact_file(self) -> None:
        """Selector returns a Path pointing to the parquet artifact file."""
        root = _make_output_root(self.tmp, {"old": True})
        snap = snapshot_output_directories(root)
        _write_placeholder_parquet(
            root / "new_run" / "artifacts" / "create_final_relationships.parquet"
        )
        result = select_new_relationship_artifact(root, snap)
        self.assertIsInstance(result, Path)
        self.assertTrue(result.exists())
        self.assertTrue(result.is_file())
        self.assertEqual(result.name, "create_final_relationships.parquet")

    def test_returned_path_is_under_new_directory(self) -> None:
        """The returned artifact path must be under a directory not in snapshot."""
        root = _make_output_root(self.tmp, {"old": True})
        snap = snapshot_output_directories(root)
        _write_placeholder_parquet(
            root / "fresh" / "artifacts" / "create_final_relationships.parquet"
        )
        result = select_new_relationship_artifact(root, snap)
        self.assertNotIn(result.parent.parent.name, snap.directory_names)


if __name__ == "__main__":
    unittest.main()
