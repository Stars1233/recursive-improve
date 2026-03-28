"""Ratchet utilities: eval+score, commit, revert, log — called by CLI subcommands."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from recursive_improve.ratchet.config import RatchetConfig
from recursive_improve.ratchet.scorer import composite_score
from recursive_improve.ratchet import git_ops, log


def ratchet_eval(config: RatchetConfig) -> dict:
    """Run eval, compute composite score, return structured result."""
    from recursive_improve.eval.runner import run_eval

    traces_dir = Path(config.traces_dir)
    eval_dir = Path(config.eval_dir)
    try:
        result = run_eval(traces_dir)
    except Exception:
        # Built-in eval may fail on non-standard trace formats (e.g., Raven)
        # Fall through to custom metrics
        result = {"metrics": {}, "trace_count": 0, "run_id": None}
    metrics = result.get("metrics", {})

    # Also run custom detectors if compute_baselines.py exists
    baselines_script = eval_dir / "compute_baselines.py"
    if baselines_script.exists():
        try:
            cp = subprocess.run(
                [sys.executable, str(baselines_script),
                 "--traces-dir", str(traces_dir),
                 "--output", str(eval_dir / "custom_metrics.json")],
                capture_output=True, text=True, timeout=600,
            )
            if cp.returncode == 0:
                custom_path = eval_dir / "custom_metrics.json"
                if custom_path.exists():
                    custom = json.loads(custom_path.read_text())
                    # Handle both flat format and nested {"metrics": {...}}
                    custom_metrics = custom.get("metrics", custom)
                    for k, v in custom_metrics.items():
                        if isinstance(v, dict) and "value" in v:
                            metrics[k] = v
        except (subprocess.TimeoutExpired, Exception):
            pass

    score = composite_score(metrics, config)

    return {
        "score": score,
        "metrics": metrics,
        "trace_count": result.get("trace_count", 0),
        "run_id": result.get("run_id"),
    }


def ratchet_commit(iteration: int, score: float, prev_score: float | None = None) -> str | None:
    """Commit current changes as a ratchet iteration."""
    return git_ops.commit_iteration(iteration, score, prev_score)


def ratchet_revert() -> None:
    """Revert working tree to last commit."""
    git_ops.revert_to_last_commit()


def ratchet_log_iteration(
    eval_dir: str,
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
    """Append iteration to log and update summary."""
    eval_path = Path(eval_dir)
    log_path = eval_path / "ratchet_log.jsonl"
    summary_path = eval_path / "ratchet_summary.md"

    log.append_iteration(
        log_path,
        iteration=iteration,
        duration_s=duration_s,
        baseline_score=baseline_score,
        new_score=new_score,
        decision=decision,
        commit_hash=commit_hash,
        metrics=metrics,
        traces_count=traces_count,
    )
    log.write_summary(summary_path, log_path)


def ratchet_status(eval_dir: str, config: RatchetConfig) -> dict:
    """Get current ratchet status from the log."""
    log_path = Path(eval_dir) / "ratchet_log.jsonl"
    entries = log.load_log(log_path)

    if not entries:
        return {
            "iterations": 0,
            "best_score": None,
            "current_score": None,
            "plateau_count": 0,
            "keeps": 0,
            "reverts": 0,
        }

    keeps = [e for e in entries if e["decision"] == "keep"]
    best_score = max(e["new_score"] for e in keeps) if keeps else None

    # Count current plateau
    plateau = 0
    for e in reversed(entries):
        if e["decision"] == "revert":
            plateau += 1
        else:
            break

    return {
        "iterations": len(entries),
        "best_score": best_score,
        "current_score": entries[-1]["new_score"],
        "plateau_count": plateau,
        "keeps": len(keeps),
        "reverts": len(entries) - len(keeps),
        "max_iterations": config.max_iterations,
        "plateau_patience": config.plateau_patience,
    }
