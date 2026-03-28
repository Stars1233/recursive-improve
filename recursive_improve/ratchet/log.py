"""Structured logging for ratchet iterations."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def append_iteration(
    log_path: str | Path,
    *,
    iteration: int,
    duration_s: float,
    baseline_score: float,
    new_score: float,
    decision: str,
    commit_hash: str | None,
    metrics: dict,
    traces_count: int,
) -> None:
    """Append one JSON line per iteration to the log file."""
    entry = {
        "iteration": iteration,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "duration_s": round(duration_s, 1),
        "baseline_score": round(baseline_score, 4),
        "new_score": round(new_score, 4),
        "decision": decision,
        "commit_hash": commit_hash,
        "metrics": {
            k: round(v["value"], 4) if isinstance(v, dict) else v
            for k, v in metrics.items()
        },
        "traces_count": traces_count,
    }
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def load_log(log_path: str | Path) -> list[dict]:
    """Load all iterations from the JSONL log."""
    path = Path(log_path)
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            entries.append(json.loads(line))
    return entries


def write_summary(summary_path: str | Path, log_path: str | Path) -> None:
    """Generate a human-readable markdown summary from the log."""
    entries = load_log(log_path)
    if not entries:
        return

    path = Path(summary_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    best = max(entries, key=lambda e: e["new_score"])
    keeps = sum(1 for e in entries if e["decision"] == "keep")
    reverts = len(entries) - keeps

    lines = [
        "# Ratchet Run Summary",
        "",
        f"Iterations: {len(entries)} ({keeps} kept, {reverts} reverted)",
        f"Best score: {best['new_score']:.4f} (iteration {best['iteration']})",
        f"Latest: {entries[-1]['timestamp']}",
        "",
        "| Iter | Score | Delta | Decision | Commit | Duration |",
        "|------|-------|-------|----------|--------|----------|",
    ]

    for e in entries:
        delta = e["new_score"] - e["baseline_score"]
        delta_str = f"{delta:+.4f}" if e["iteration"] > 0 else "—"
        commit = e.get("commit_hash") or "—"
        mins = e["duration_s"] / 60
        lines.append(
            f"| {e['iteration']:<4} | {e['new_score']:.4f} | {delta_str} "
            f"| {e['decision']:<8} | {commit} | {mins:.1f}m |"
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
