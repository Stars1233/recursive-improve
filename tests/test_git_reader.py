"""Tests for git_reader: cross-branch reading of benchmark results."""

import json
import subprocess
from pathlib import Path

import pytest

from recursive_improve.store.git_reader import (
    list_branches,
    read_file_from_branch,
    load_runs_from_all_branches,
)


@pytest.fixture
def git_repo(tmp_path):
    """Create a temp git repo with 2 branches, each with benchmark_results.json."""
    repo = tmp_path / "repo"
    repo.mkdir()

    def run(*args):
        subprocess.run(
            ["git"] + list(args),
            cwd=str(repo), capture_output=True, text=True, check=True,
            env={"GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t",
                 "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t",
                 "HOME": str(tmp_path), "PATH": "/usr/bin:/usr/local/bin:/bin"},
        )

    run("init", "-b", "main")
    run("config", "user.email", "t@t")
    run("config", "user.name", "test")

    # Create benchmark_results.json on main
    eval_dir = repo / "eval"
    eval_dir.mkdir()
    store_data = {
        "version": 1,
        "runs": [{
            "id": "run_main_1",
            "branch": "main",
            "commit_hash": "aaa111",
            "timestamp": "2026-01-01T00:00:00Z",
            "metrics": {
                "error_rate": {"numerator": 2, "denominator": 10, "value": 0.2},
            },
        }],
    }
    (eval_dir / "benchmark_results.json").write_text(json.dumps(store_data))
    run("add", "eval/benchmark_results.json")
    run("commit", "-m", "main benchmark")

    # Create improvement branch with different results
    run("checkout", "-b", "improve/fix-errors")
    store_data["runs"].append({
        "id": "run_improve_1",
        "branch": "improve/fix-errors",
        "commit_hash": "bbb222",
        "timestamp": "2026-01-02T00:00:00Z",
        "metrics": {
            "error_rate": {"numerator": 1, "denominator": 10, "value": 0.1},
        },
    })
    (eval_dir / "benchmark_results.json").write_text(json.dumps(store_data))
    run("add", "eval/benchmark_results.json")
    run("commit", "-m", "improvement benchmark")

    # Go back to main
    run("checkout", "main")

    return repo


class TestListBranches:
    def test_lists_branches(self, git_repo):
        branches = list_branches(git_repo)
        assert "main" in branches
        assert "improve/fix-errors" in branches

    def test_returns_empty_for_non_repo(self, tmp_path):
        assert list_branches(tmp_path) == []


class TestReadFileFromBranch:
    def test_reads_from_main(self, git_repo):
        content = read_file_from_branch("main", "eval/benchmark_results.json", git_repo)
        assert content is not None
        data = json.loads(content)
        assert len(data["runs"]) == 1
        assert data["runs"][0]["id"] == "run_main_1"

    def test_reads_from_improvement_branch(self, git_repo):
        content = read_file_from_branch(
            "improve/fix-errors", "eval/benchmark_results.json", git_repo)
        assert content is not None
        data = json.loads(content)
        assert len(data["runs"]) == 2

    def test_returns_none_for_missing_file(self, git_repo):
        assert read_file_from_branch("main", "nonexistent.json", git_repo) is None


class TestLoadRunsFromAllBranches:
    def test_deduplicates_across_branches(self, git_repo):
        runs = load_runs_from_all_branches("eval/benchmark_results.json", git_repo)
        run_ids = [r["id"] for r in runs]
        # Both runs should appear (deduplicated)
        assert "run_main_1" in run_ids
        assert "run_improve_1" in run_ids
        # No duplicates
        assert len(run_ids) == len(set(run_ids))

    def test_sorted_by_timestamp_desc(self, git_repo):
        runs = load_runs_from_all_branches("eval/benchmark_results.json", git_repo)
        assert runs[0]["id"] == "run_improve_1"  # 2026-01-02
        assert runs[1]["id"] == "run_main_1"  # 2026-01-01

    def test_local_working_tree_wins(self, git_repo):
        """Uncommitted local changes should override branch data."""
        local_data = {
            "version": 1,
            "runs": [{
                "id": "run_main_1",
                "branch": "main",
                "commit_hash": "ccc333",
                "timestamp": "2026-01-03T00:00:00Z",
                "metrics": {"error_rate": {"value": 0.05}},
            }],
        }
        (git_repo / "eval" / "benchmark_results.json").write_text(
            json.dumps(local_data))
        runs = load_runs_from_all_branches("eval/benchmark_results.json", git_repo)
        main_run = next(r for r in runs if r["id"] == "run_main_1")
        # Local version should win (commit_hash=ccc333)
        assert main_run["commit_hash"] == "ccc333"
