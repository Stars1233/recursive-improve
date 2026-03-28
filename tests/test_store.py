"""Tests for JSONRunStore operations."""

import json
from pathlib import Path

import pytest

from recursive_improve.store.json_store import JSONRunStore


@pytest.fixture
def store(tmp_path):
    return JSONRunStore(store_path=tmp_path / "results.json")


class TestJSONRunStore:
    def test_schema_initialization(self, store):
        """Empty store should return no runs."""
        run = store.get_all_runs()
        assert run == []

    def test_insert_and_get_run(self, store):
        store.insert_run(
            run_id="r1",
            branch="main",
            commit_hash="abc123",
            timestamp="2026-01-01T00:00:00Z",
            traces_dir="./traces",
            success=True,
            duration=12.5,
        )
        run = store.get_run("r1")
        assert run is not None
        assert run["id"] == "r1"
        assert run["branch"] == "main"
        assert run["commit_hash"] == "abc123"
        assert run["success"] == 1
        assert run["duration"] == 12.5

    def test_get_run_not_found(self, store):
        assert store.get_run("nonexistent") is None

    def test_insert_and_get_metrics(self, store):
        store.insert_run(
            run_id="r2",
            timestamp="2026-01-01T00:00:00Z",
        )
        store.insert_metrics("r2", {
            "loop_rate": {"numerator": 2, "denominator": 10, "value": 0.2, "confidence": "full"},
            "error_rate": {"numerator": 1, "denominator": 10, "value": 0.1, "confidence": "full"},
        })

        metrics = store.get_metrics("r2")
        assert len(metrics) == 2
        names = {m["metric_name"] for m in metrics}
        assert "loop_rate" in names
        assert "error_rate" in names

    def test_get_runs_by_branch(self, store):
        store.insert_run(run_id="r3", branch="main", timestamp="2026-01-01T00:00:00Z")
        store.insert_run(run_id="r4", branch="fix", timestamp="2026-01-02T00:00:00Z")
        store.insert_run(run_id="r5", branch="main", timestamp="2026-01-03T00:00:00Z")

        main_runs = store.get_runs_by_branch("main")
        assert len(main_runs) == 2
        assert main_runs[0]["id"] == "r5"  # Most recent first

    def test_get_runs_by_branch_require_metrics(self, store):
        store.insert_run(run_id="r_metrics", branch="main", timestamp="2026-01-01T00:00:00Z")
        store.insert_metrics("r_metrics", {
            "clean_success_rate": {"numerator": 1, "denominator": 1, "value": 1.0, "confidence": "full"},
        })
        store.insert_run(run_id="r_capture", branch="main", timestamp="2026-01-02T00:00:00Z")

        runs = store.get_runs_by_branch("main", require_metrics=True)
        assert [run["id"] for run in runs] == ["r_metrics"]

    def test_get_latest_run(self, store):
        store.insert_run(run_id="r6", branch="main", timestamp="2026-01-01T00:00:00Z")
        store.insert_run(run_id="r7", branch="main", timestamp="2026-01-02T00:00:00Z")

        latest = store.get_latest_run()
        assert latest["id"] == "r7"

        latest_main = store.get_latest_run(branch="main")
        assert latest_main["id"] == "r7"

    def test_get_latest_run_with_branch_filter(self, store):
        store.insert_run(run_id="r8", branch="main", timestamp="2026-01-01T00:00:00Z")
        store.insert_run(run_id="r9", branch="fix", timestamp="2026-01-02T00:00:00Z")

        latest_fix = store.get_latest_run(branch="fix")
        assert latest_fix["id"] == "r9"

    def test_get_branches(self, store):
        store.insert_run(run_id="r10", branch="main", timestamp="2026-01-01T00:00:00Z")
        store.insert_run(run_id="r11", branch="fix", timestamp="2026-01-01T00:00:00Z")
        store.insert_run(run_id="r12", branch="main", timestamp="2026-01-02T00:00:00Z")

        branches = store.get_branches()
        assert sorted(branches) == ["fix", "main"]

    def test_run_has_metrics(self, store):
        store.insert_run(run_id="r_metrics", timestamp="2026-01-01T00:00:00Z")
        store.insert_metrics("r_metrics", {
            "loop_rate": {"numerator": 1, "denominator": 2, "value": 0.5, "confidence": "full"},
        })
        store.insert_run(run_id="r_capture", timestamp="2026-01-02T00:00:00Z")

        assert store.run_has_metrics("r_metrics") is True
        assert store.run_has_metrics("r_capture") is False

    def test_get_all_runs(self, store):
        store.insert_run(run_id="r13", timestamp="2026-01-01T00:00:00Z")
        store.insert_run(run_id="r14", timestamp="2026-01-02T00:00:00Z")

        runs = store.get_all_runs()
        assert len(runs) == 2
        assert runs[0]["id"] == "r14"  # Most recent first

    def test_upsert_run(self, store):
        """Insert with same run_id should update existing run."""
        store.insert_run(run_id="r15", branch="old", timestamp="2026-01-01T00:00:00Z")
        store.insert_run(run_id="r15", branch="new", timestamp="2026-01-02T00:00:00Z")

        run = store.get_run("r15")
        assert run["branch"] == "new"

    def test_metadata_stored_as_json(self, store):
        store.insert_run(
            run_id="r16",
            timestamp="2026-01-01T00:00:00Z",
            metadata={"key": "value"},
        )
        run = store.get_run("r16")
        assert json.loads(run["metadata"]) == {"key": "value"}

    def test_normalize_percentage_values(self, store):
        """Values > 1 for rate metrics should be normalized to 0-1."""
        store.insert_run(run_id="r17", timestamp="2026-01-01T00:00:00Z")
        store.insert_metrics("r17", {
            "error_rate": {"numerator": 2, "denominator": 5, "value": 40.0, "confidence": "full"},
        })
        metrics = store.get_metrics("r17")
        assert len(metrics) == 1
        assert metrics[0]["value"] == pytest.approx(0.4, abs=0.001)

    def test_json_file_format(self, store):
        """Verify the JSON file has the expected structure."""
        store.insert_run(run_id="r18", branch="main", timestamp="2026-01-01T00:00:00Z")
        store.insert_metrics("r18", {
            "loop_rate": {"numerator": 0, "denominator": 4, "value": 0.0, "confidence": "directional-only"},
        })
        data = json.loads(store.store_path.read_text())
        assert data["version"] == 1
        assert len(data["runs"]) == 1
        assert data["runs"][0]["id"] == "r18"
        assert "loop_rate" in data["runs"][0]["metrics"]
