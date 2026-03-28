"""Git operations for the ratchet loop."""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone


def create_ratchet_branch() -> str:
    """Create and checkout a ratchet branch. Returns the branch name."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    branch = f"ri/ratchet-{ts}"
    subprocess.run(["git", "checkout", "-b", branch], check=True, capture_output=True)
    return branch


def commit_iteration(iteration: int, score: float, prev_score: float | None = None) -> str | None:
    """Stage all changes and commit. Returns the commit hash or None if nothing to commit."""
    # Check if there are changes
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True, text=True,
    )
    if not result.stdout.strip():
        return None

    subprocess.run(["git", "add", "-A"], check=True, capture_output=True)

    delta = ""
    if prev_score is not None:
        delta = f" ({prev_score:.4f} -> {score:.4f})"

    msg = f"ratchet: iteration {iteration}, score {score:.4f}{delta}"
    subprocess.run(["git", "commit", "-m", msg], check=True, capture_output=True)

    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True,
    )
    return result.stdout.strip()


def revert_to_last_commit() -> None:
    """Discard all working tree changes back to the last commit."""
    subprocess.run(["git", "checkout", "--", "."], check=True, capture_output=True)
    # Also remove untracked files created by the improvement step
    subprocess.run(["git", "clean", "-fd"], capture_output=True)


def is_dirty() -> bool:
    """Check if the working tree has uncommitted changes."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True, text=True,
    )
    return bool(result.stdout.strip())


def current_branch() -> str | None:
    """Return the current branch name."""
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None
