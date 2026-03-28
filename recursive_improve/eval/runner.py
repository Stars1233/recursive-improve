"""Eval runner: load traces, run all detectors, aggregate metrics."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from recursive_improve.eval.detectors import (
    DetectorResult,
    detect_loops,
    detect_give_up,
    detect_errors,
    detect_recovery,
    detect_clean_success,
    detect_duration_outlier,
    detect_token_usage,
)

_ALL_DETECTORS = [
    detect_loops,
    detect_give_up,
    detect_errors,
    detect_recovery,
    detect_duration_outlier,
    detect_token_usage,
]


def load_trace_files(traces_dir: str | Path) -> list[dict]:
    traces = []
    for f in sorted(Path(traces_dir).glob("*.json")):
        try:
            data = json.loads(f.read_text())
            data["_file"] = f.name
            traces.append(data)
        except (json.JSONDecodeError, OSError):
            continue
    return traces


def _assign_confidence(denominator: int) -> str:
    return "full" if denominator >= 5 else "directional-only"


def run_eval(traces_dir: str | Path, branch: str | None = None) -> dict:
    """Run all detectors on all traces and return aggregated metrics."""
    traces = load_trace_files(traces_dir)

    if not traces:
        return {
            "run_id": uuid.uuid4().hex[:12],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "trace_count": 0,
            "branch": branch,
            "commit_hash": None,
            "success": None,
            "metrics": {},
        }

    # Aggregate per-detector results across all traces
    aggregated: dict[str, dict] = {}

    for trace in traces:
        results = [d(trace) for d in _ALL_DETECTORS]
        clean = detect_clean_success(trace, results)
        results.append(clean)

        for r in results:
            if r.name not in aggregated:
                aggregated[r.name] = {"numerator": 0, "denominator": 0}
            aggregated[r.name]["numerator"] += r.numerator
            aggregated[r.name]["denominator"] += r.denominator

    # Build final metrics with rates and confidence
    metrics = {}
    for name, agg in aggregated.items():
        num, den = agg["numerator"], agg["denominator"]
        value = num / den if den > 0 else 0.0
        metrics[name] = {
            "numerator": num,
            "denominator": den,
            "value": round(value, 4),
            "confidence": _assign_confidence(den),
        }

    # Infer branch/commit from traces if not provided
    branches = {t.get("git_branch") for t in traces if t.get("git_branch")}
    commits = {t.get("git_commit") for t in traces if t.get("git_commit")}
    inferred_branch = branch or (branches.pop() if len(branches) == 1 else None)
    inferred_commit = commits.pop() if len(commits) == 1 else None

    # Overall success = all traces succeeded
    all_success = all(t.get("success", False) for t in traces)

    return {
        "run_id": uuid.uuid4().hex[:12],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "trace_count": len(traces),
        "branch": inferred_branch,
        "commit_hash": inferred_commit,
        "success": all_success,
        "metrics": metrics,
    }
