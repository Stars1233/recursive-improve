"""Tests for compare: ref resolution, delta computation, table formatting."""

import pytest

from recursive_improve.store.json_store import JSONRunStore
from recursive_improve.eval.compare import resolve_run, compare_runs, format_comparison_table


@pytest.fixture
def store(tmp_path):
    s = JSONRunStore(store_path=tmp_path / "results.json")
    # Insert test runs
    s.insert_run(run_id="abc123", branch="main", commit_hash="aaa111",
                 timestamp="2026-01-01T00:00:00Z")
    s.insert_run(run_id="def456", branch="fix/retry", commit_hash="bbb222",
                 timestamp="2026-01-02T00:00:00Z")
    # Insert metrics
    s.insert_metrics("abc123", {
        "loop_rate": {"numerator": 3, "denominator": 25, "value": 0.12, "confidence": "full"},
        "error_rate": {"numerator": 5, "denominator": 25, "value": 0.20, "confidence": "full"},
        "clean_success_rate": {"numerator": 15, "denominator": 25, "value": 0.60, "confidence": "full"},
    })
    s.insert_metrics("def456", {
        "loop_rate": {"numerator": 1, "denominator": 25, "value": 0.04, "confidence": "full"},
        "error_rate": {"numerator": 3, "denominator": 20, "value": 0.15, "confidence": "full"},
        "clean_success_rate": {"numerator": 16, "denominator": 20, "value": 0.80, "confidence": "full"},
    })
    return s


class TestResolveRun:
    def test_resolve_by_run_id(self, store):
        run = resolve_run("abc123", store)
        assert run is not None
        assert run["id"] == "abc123"

    def test_resolve_by_branch(self, store):
        run = resolve_run("main", store)
        assert run is not None
        assert run["id"] == "abc123"

    def test_resolve_by_commit_prefix(self, store):
        run = resolve_run("bbb", store)
        assert run is not None
        assert run["id"] == "def456"

    def test_resolve_not_found(self, store):
        assert resolve_run("nonexistent", store) is None


class TestCompareRuns:
    def test_basic_comparison(self, store):
        result = compare_runs("main", "fix/retry", store=store)

        assert "error" not in result
        assert result["left"]["run_id"] == "abc123"
        assert result["right"]["run_id"] == "def456"
        assert len(result["comparisons"]) == 3

        # Find loop_rate comparison
        loop = next(c for c in result["comparisons"] if c["metric"] == "loop_rate")
        assert loop["left_value"] == 0.12
        assert loop["right_value"] == 0.04
        assert loop["delta"] == pytest.approx(-0.08, abs=0.001)

    def test_error_on_missing_ref(self, store):
        result = compare_runs("main", "nonexistent", store=store)
        assert "error" in result

    def test_delta_computation(self, store):
        result = compare_runs("abc123", "def456", store=store)
        clean = next(c for c in result["comparisons"] if c["metric"] == "clean_success_rate")
        assert clean["delta"] == pytest.approx(0.20, abs=0.001)

    def test_branch_resolution_prefers_latest_evaluated_run(self, store):
        store.insert_run(
            run_id="trace_only",
            branch="main",
            commit_hash="aaa999",
            timestamp="2026-01-03T00:00:00Z",
        )

        result = compare_runs("main", "fix/retry", store=store)
        assert "error" not in result
        assert result["left"]["run_id"] == "abc123"

    def test_exact_capture_run_without_metrics_returns_error(self, store):
        store.insert_run(
            run_id="trace_only",
            branch="main",
            commit_hash="aaa999",
            timestamp="2026-01-03T00:00:00Z",
        )

        result = compare_runs("trace_only", "fix/retry", store=store)
        assert "error" in result


class TestFormatComparisonTable:
    def test_formats_table(self, store):
        result = compare_runs("main", "fix/retry", store=store)
        table = format_comparison_table(result)

        assert "abc123" in table
        assert "def456" in table
        assert "loop_rate" in table
        assert "%" in table

    def test_error_message(self):
        table = format_comparison_table({"error": "Not found"})
        assert "Error: Not found" in table
