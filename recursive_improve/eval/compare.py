"""Compare metrics between two eval runs."""

from __future__ import annotations

from recursive_improve.store.json_store import JSONRunStore


def resolve_run(ref: str, store: JSONRunStore) -> dict | None:
    """Resolve a reference to a run.

    Tries in order: exact run_id, branch (latest with metrics), commit prefix.
    """
    # 1. Exact run_id
    run = store.get_run(ref)
    if run and store.run_has_metrics(run["id"]):
        return run

    # 2. Branch name (latest run with metrics)
    runs = store.get_runs_by_branch(ref, require_metrics=True)
    if runs:
        return runs[0]

    # 3. Commit prefix match
    all_runs = store.get_all_runs(require_metrics=True)
    for r in all_runs:
        if r.get("commit_hash") and r["commit_hash"].startswith(ref):
            return r

    return None


def compare_runs(left_ref: str, right_ref: str, *, store: JSONRunStore) -> dict:
    """Compare metrics between two runs identified by ref strings."""
    left_run = resolve_run(left_ref, store)
    if not left_run:
        return {"error": f"Could not resolve left reference: {left_ref}"}

    right_run = resolve_run(right_ref, store)
    if not right_run:
        return {"error": f"Could not resolve right reference: {right_ref}"}

    left_metrics = {m["metric_name"]: m for m in store.get_metrics(left_run["id"])}
    right_metrics = {m["metric_name"]: m for m in store.get_metrics(right_run["id"])}

    all_names = sorted(set(left_metrics) | set(right_metrics))
    comparisons = []
    for name in all_names:
        lv = left_metrics.get(name, {}).get("value", 0.0) or 0.0
        rv = right_metrics.get(name, {}).get("value", 0.0) or 0.0
        comparisons.append({
            "metric": name,
            "left_value": lv,
            "right_value": rv,
            "delta": round(rv - lv, 4),
        })

    return {
        "left": {"run_id": left_run["id"], "branch": left_run.get("branch")},
        "right": {"run_id": right_run["id"], "branch": right_run.get("branch")},
        "comparisons": comparisons,
    }


def format_comparison_table(result: dict) -> str:
    """Format comparison result as a text table."""
    if "error" in result:
        return f"Error: {result['error']}"

    left_id = result["left"]["run_id"]
    right_id = result["right"]["run_id"]

    lines = [
        f"  {'Metric':<30} {left_id:>12} {right_id:>12} {'Delta':>10}",
        f"  {'─' * 30} {'─' * 12} {'─' * 12} {'─' * 10}",
    ]

    for c in result["comparisons"]:
        lv = f"{c['left_value'] * 100:.1f}%"
        rv = f"{c['right_value'] * 100:.1f}%"
        delta = c["delta"]
        sign = "+" if delta > 0 else ""
        dv = f"{sign}{delta * 100:.1f}%"
        lines.append(f"  {c['metric']:<30} {lv:>12} {rv:>12} {dv:>10}")

    return "\n".join(lines)
