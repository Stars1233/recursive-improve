"""Cross-branch git reading for benchmark results."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def list_branches(cwd: str | Path | None = None) -> list[str]:
    """Return list of local branch names."""
    try:
        r = subprocess.run(
            ["git", "branch", "--list", "--format=%(refname:short)"],
            capture_output=True, text=True,
            cwd=str(cwd) if cwd else None,
        )
        if r.returncode != 0:
            return []
        return [b.strip() for b in r.stdout.strip().splitlines() if b.strip()]
    except Exception:
        return []


def current_branch(cwd: str | Path | None = None) -> str | None:
    """Return the current branch name."""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True,
            cwd=str(cwd) if cwd else None,
        )
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


def read_file_from_branch(
    branch: str, file_path: str, cwd: str | Path | None = None,
) -> str | None:
    """Read a file's contents from a specific branch via git show."""
    try:
        r = subprocess.run(
            ["git", "show", f"{branch}:{file_path}"],
            capture_output=True, text=True,
            cwd=str(cwd) if cwd else None,
        )
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None


def load_runs_from_all_branches(
    store_path: str = "eval/benchmark_results.json",
    cwd: str | Path | None = None,
) -> list[dict]:
    """Load and deduplicate runs from all branches + working tree.

    Dedup: if same run_id on multiple branches, keep the one where
    run["branch"] matches the source branch.
    """
    seen: dict[str, dict] = {}  # run_id -> run dict
    seen_source: dict[str, str] = {}  # run_id -> source branch

    branches = list_branches(cwd)

    for branch in branches:
        content = read_file_from_branch(branch, store_path, cwd)
        if not content:
            continue
        try:
            data = json.loads(content)
            for run in data.get("runs", []):
                rid = run.get("id")
                if not rid:
                    continue
                # Prefer the version where run["branch"] matches source
                if rid in seen:
                    if run.get("branch") == branch and seen_source[rid] != branch:
                        seen[rid] = run
                        seen_source[rid] = branch
                else:
                    seen[rid] = run
                    seen_source[rid] = branch
        except (json.JSONDecodeError, KeyError):
            continue

    # Also load from working tree (local uncommitted file)
    cwd_path = Path(cwd) if cwd else Path(".")
    local_file = cwd_path / store_path
    if local_file.exists():
        try:
            data = json.loads(local_file.read_text(encoding="utf-8"))
            for run in data.get("runs", []):
                rid = run.get("id")
                if not rid:
                    continue
                # Local always wins (most up-to-date)
                seen[rid] = run
        except (json.JSONDecodeError, KeyError):
            pass

    # Sort by timestamp descending
    runs = list(seen.values())
    runs.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    return runs
