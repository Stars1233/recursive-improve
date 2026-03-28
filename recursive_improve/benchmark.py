"""Benchmark: snapshot metric quality, store, compare against previous runs."""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from recursive_improve.store.json_store import JSONRunStore


def run_benchmark(
    *,
    label: str | None = None,
    traces_dir: str = "eval/traces",
    eval_dir: str = "eval",
) -> dict:
    """Run metric quality evaluation, store in RunStore, return results."""
    from recursive_improve.eval.runner import run_eval

    eval_path = Path(eval_dir)
    traces_path = Path(traces_dir)

    # Always run built-in detectors
    result = run_eval(traces_path)
    metrics = result.get("metrics", {})

    if not metrics and result.get("trace_count", 0) == 0:
        return {"error": f"No traces found in {traces_path}"}

    # Optionally layer in custom metrics if compute_baselines.py exists
    baselines_script = eval_path / "compute_baselines.py"
    if baselines_script.exists():
        try:
            output_path = eval_path / "custom_metrics.json"
            cp = subprocess.run(
                [sys.executable, str(baselines_script),
                 "--traces-dir", str(traces_path),
                 "--output", str(output_path)],
                capture_output=True, text=True, timeout=600,
            )
            if cp.returncode == 0 and output_path.exists():
                custom = json.loads(output_path.read_text())
                custom_metrics = custom.get("metrics", custom)
                for k, v in custom_metrics.items():
                    if isinstance(v, dict) and "value" in v:
                        metrics[k] = v
        except (subprocess.TimeoutExpired, Exception):
            pass

    # Also collect per-skill detector values
    metrics_dir = eval_path / "metrics"
    if metrics_dir.exists():
        for f in sorted(metrics_dir.glob("*.json")):
            if f.name in ("baseline_metrics.json", "custom_metrics.json"):
                continue
            try:
                data = json.loads(f.read_text())
                sid = data.get("skill_id", f.stem)
                if data.get("unmeasurable"):
                    continue
                if "value" in data and "denominator" in data:
                    metrics[f"skill:{sid}"] = {
                        "numerator": data.get("numerator", 0),
                        "denominator": data.get("denominator", 0),
                        "value": data.get("value", 0),
                        "confidence": data.get("confidence", "unknown"),
                    }
            except (json.JSONDecodeError, OSError):
                continue

    # Create run entry
    run_id = uuid.uuid4().hex[:12]
    timestamp = datetime.now(timezone.utc).isoformat()

    # Get git info
    branch = _git_branch()
    commit = _git_commit()

    # Compute composite score from rate metrics (0-1 range only)
    quality_metrics = {k: v for k, v in metrics.items() if not k.startswith("skill:")}
    if "composite_quality" in quality_metrics:
        composite = quality_metrics["composite_quality"].get("value", 0)
    else:
        rate_metrics = {k: v for k, v in quality_metrics.items()
                        if 0 <= v.get("value", -1) <= 1}
        if rate_metrics:
            composite = sum(m.get("value", 0) for m in rate_metrics.values()) / len(rate_metrics)
        else:
            composite = 0

    # Store in RunStore
    store = JSONRunStore(store_path=eval_path / "benchmark_results.json")
    store.insert_run(
        run_id=run_id,
        branch=branch,
        commit_hash=commit,
        timestamp=timestamp,
        traces_dir=str(traces_path),
        success=True,
        metadata={
            "label": label or f"benchmark-{run_id[:6]}",
            "type": "benchmark",
            "metric_count": len(quality_metrics),
            "trace_count": result.get("trace_count", 0),
        },
    )
    store.insert_metrics(run_id, metrics)

    return {
        "run_id": run_id,
        "label": label or f"benchmark-{run_id[:6]}",
        "timestamp": timestamp,
        "composite_score": composite,
        "metrics": metrics,
    }


def list_benchmarks(eval_dir: str = "eval") -> list[dict]:
    """List all stored benchmark runs."""
    store = JSONRunStore(store_path=Path(eval_dir) / "benchmark_results.json")
    runs = store.get_all_runs(require_metrics=True)

    results = []
    for run in runs:
        metrics = {m["metric_name"]: m for m in store.get_metrics(run["id"])}
        composite = metrics.get("composite_quality", {}).get("value", 0)

        meta = {}
        raw_meta = run.get("metadata")
        if raw_meta:
            if isinstance(raw_meta, dict):
                meta = raw_meta
            elif isinstance(raw_meta, str):
                try:
                    parsed = json.loads(raw_meta)
                    if isinstance(parsed, dict):
                        meta = parsed
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass

        label = meta.get("label", run["id"][:8]) if isinstance(meta, dict) else run["id"][:8]

        results.append({
            "run_id": run["id"],
            "label": label,
            "timestamp": run.get("timestamp", ""),
            "branch": run.get("branch"),
            "composite_score": composite,
            "metric_count": len(metrics),
        })

    return results


def format_benchmark_result(result: dict) -> str:
    """Format a single benchmark result for display."""
    if "error" in result:
        return f"  Error: {result['error']}"

    lines = [
        f"  Benchmark: {result['label']}",
        f"  Run ID:    {result['run_id']}",
        f"  Score:     {result['composite_score']:.1%}",
        "",
        f"  {'Metric':<35} {'Value':>8}  {'Detail':>20}",
        f"  {'─' * 35} {'─' * 8}  {'─' * 20}",
    ]

    # Quality metrics first
    for name, m in sorted(result["metrics"].items()):
        if name.startswith("skill:"):
            continue
        pct = f"{m['value'] * 100:.1f}%"
        detail = f"{m.get('numerator', 0)}/{m.get('denominator', 0)}"
        lines.append(f"  {name:<35} {pct:>8}  {detail:>20}")

    # Count skill metrics
    skill_metrics = {k: v for k, v in result["metrics"].items() if k.startswith("skill:")}
    if skill_metrics:
        lines.append(f"\n  Per-skill metrics: {len(skill_metrics)} stored")

    return "\n".join(lines)


def format_benchmark_list(benchmarks: list[dict]) -> str:
    """Format benchmark list as a table."""
    if not benchmarks:
        return "  No benchmarks stored yet."

    lines = [
        f"  {'Run ID':<14} {'Label':<25} {'Date':<22} {'Score':>8}  {'Delta':>8}",
        f"  {'─' * 14} {'─' * 25} {'─' * 22} {'─' * 8}  {'─' * 8}",
    ]

    prev_score = None
    for b in reversed(benchmarks):  # oldest first
        date = b["timestamp"][:19].replace("T", " ") if b["timestamp"] else "—"
        score = f"{b['composite_score'] * 100:.1f}%"

        if prev_score is not None:
            delta = b["composite_score"] - prev_score
            delta_str = f"{'+' if delta > 0 else ''}{delta * 100:.1f}%"
        else:
            delta_str = "—"

        lines.append(f"  {b['run_id']:<14} {b['label']:<25} {date:<22} {score:>8}  {delta_str:>8}")
        prev_score = b["composite_score"]

    return "\n".join(lines)


def format_comparison(current: dict, previous: dict, store: RunStore) -> str:
    """Format comparison between current and previous benchmark."""
    curr_metrics = {m["metric_name"]: m for m in store.get_metrics(current["run_id"])}
    prev_metrics = {m["metric_name"]: m for m in store.get_metrics(previous["run_id"])}

    # Only compare quality metrics (not per-skill)
    quality_names = sorted(
        k for k in set(curr_metrics) | set(prev_metrics) if not k.startswith("skill:")
    )

    prev_label = previous.get("label", previous["run_id"][:8])
    curr_label = current.get("label", current["run_id"][:8])

    lines = [
        f"  Comparing: {prev_label} → {curr_label}",
        "",
        f"  {'Metric':<35} {prev_label:>10} {curr_label:>10} {'Delta':>8}",
        f"  {'─' * 35} {'─' * 10} {'─' * 10} {'─' * 8}",
    ]

    for name in quality_names:
        lv = prev_metrics.get(name, {}).get("value", 0) or 0
        rv = curr_metrics.get(name, {}).get("value", 0) or 0
        delta = rv - lv
        sign = "+" if delta > 0 else ""
        lines.append(
            f"  {name:<35} {lv * 100:>9.1f}% {rv * 100:>9.1f}% {sign}{delta * 100:>6.1f}%"
        )

    return "\n".join(lines)


def _git_branch() -> str | None:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True,
        )
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


def _git_commit() -> str | None:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True,
        )
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None
