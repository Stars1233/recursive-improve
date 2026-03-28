"""CLI entry point: eval, compare, dashboard, migrate."""

import argparse
import json
import sys
from pathlib import Path


def cmd_init(args):
    """Set up recursive-improve in the current project directory."""
    import importlib.resources

    pkg = importlib.resources.files("recursive_improve") / "data"

    # Install skill files — (source_filename, skill_dir_name)
    skills = [
        ("SKILL.md", "recursive-improve"),
        ("RATCHET_SKILL.md", "ratchet"),
        ("BENCHMARK_SKILL.md", "benchmark"),
    ]
    created = []

    for source_filename, skill_name in skills:
        try:
            content = (pkg / source_filename).read_text(encoding="utf-8")
        except Exception:
            continue

        for prefix in [".claude/skills", ".agents/skills"]:
            target = Path(f"{prefix}/{skill_name}/SKILL.md")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            created.append(target)

    # Create eval/traces/ directory
    Path("eval/traces").mkdir(parents=True, exist_ok=True)

    # Scaffold program.md if it doesn't exist
    program_md = Path("program.md")
    if not program_md.exists():
        program_md.write_text(_PROGRAM_MD_TEMPLATE, encoding="utf-8")
        created.append(program_md)

    print("  recursive-improve init")
    print()
    for t in created:
        print(f"  Created {t}")
    print("  Created eval/traces/")
    print()
    print("  Next: add traces to eval/traces/ and run /recursive-improve")
    print("  For autonomous loop: edit program.md and run recursive-improve ratchet")


_PROGRAM_MD_TEMPLATE = """\
# Improvement Goals

## Objective
Describe what you want to improve about your agent.

## Agent Run Command
uv run python your_agent.py

## Traces Directory
eval/traces

## Metrics
- clean_success_rate: maximize (weight: 2.0)
- error_rate: minimize (weight: 1.0)
- give_up_rate: minimize (weight: 1.0)

## Stopping Conditions
- max_iterations: 20
- max_duration_hours: 8
- plateau_patience: 3

## Time Budget
- minutes_per_iteration: 15
"""


def cmd_eval(args):
    """Run built-in detectors on traces and store results."""
    from recursive_improve.eval.runner import run_eval
    from recursive_improve.store.json_store import JSONRunStore

    traces_dir = Path(args.traces_dir)
    if not traces_dir.exists():
        print(f"Error: traces directory not found: {traces_dir}")
        sys.exit(1)

    print(f"\n  recursive-improve eval")
    print(f"  Traces: {traces_dir}")
    if args.branch:
        print(f"  Branch: {args.branch}")

    result = run_eval(traces_dir, branch=args.branch)

    print(f"\n  Evaluated {result['trace_count']} traces")
    print(f"  Run ID: {result['run_id']}")
    print(f"  Branch: {result.get('branch', 'unknown')}")
    print(f"\n  Metrics:")

    for name, m in sorted(result["metrics"].items()):
        pct = f"{m['value'] * 100:.1f}%"
        conf = m["confidence"]
        print(f"    {name:<30} {pct:>8}  ({m['numerator']}/{m['denominator']}, {conf})")

    # Store in JSON
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        store_path = output_dir / "benchmark_results.json"
        store = JSONRunStore(store_path=store_path)
        store.insert_run(
            run_id=result["run_id"],
            branch=result.get("branch"),
            commit_hash=result.get("commit_hash"),
            timestamp=result["timestamp"],
            traces_dir=str(traces_dir),
            success=result.get("success"),
        )
        store.insert_metrics(result["run_id"], result["metrics"])
        print(f"\n  Stored in {store_path}")
    except Exception as e:
        print(f"\n  Warning: could not store results: {e}")

    # Write eval_results.json
    results_path = output_dir / "eval_results.json"
    results_path.write_text(json.dumps(result, indent=2))
    print(f"  Written to {results_path}")


def cmd_compare(args):
    """Compare metrics between two runs/branches/commits."""
    from recursive_improve.eval.compare import compare_runs, format_comparison_table
    from recursive_improve.store.json_store import JSONRunStore

    store = JSONRunStore(store_path=Path(args.eval_dir) / "benchmark_results.json")
    result = compare_runs(args.left, args.right, store=store)
    print(f"\n{format_comparison_table(result)}\n")


def cmd_dashboard(args):
    """Launch the improvement dashboard."""
    try:
        from recursive_improve.dashboard.app import create_app
        import uvicorn
    except ImportError:
        print("Error: dashboard extras not installed. Run: pip install recursive-improve[dashboard]")
        sys.exit(1)

    eval_dir = Path(args.eval_dir)
    app = create_app(eval_dir)

    print(f"\n  recursive-improve dashboard")
    print(f"  Reading from: {eval_dir}/")
    print(f"  http://localhost:{args.port}\n")

    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")


def cmd_ratchet(args):
    """Ratchet subcommand dispatcher."""
    sub = args.ratchet_command
    if sub is None:
        print("Usage: recursive-improve ratchet {eval|commit|revert|log|status|branch}")
        sys.exit(1)

    from recursive_improve.ratchet.config import parse_program_md
    from recursive_improve.ratchet import engine, git_ops

    if sub == "eval":
        config_path = Path(args.config)
        if not config_path.exists():
            print(f"Error: config not found: {config_path}")
            sys.exit(1)
        config = parse_program_md(config_path)
        config.eval_dir = args.eval_dir
        result = engine.ratchet_eval(config)
        # Output as JSON for the skill to parse
        print(json.dumps(result, indent=2))

    elif sub == "commit":
        commit_hash = engine.ratchet_commit(
            args.iteration, args.score, args.prev_score,
        )
        print(commit_hash or "nothing-to-commit")

    elif sub == "revert":
        engine.ratchet_revert()
        print("reverted")

    elif sub == "log":
        engine.ratchet_log_iteration(
            args.eval_dir,
            iteration=args.iteration,
            duration_s=args.duration,
            baseline_score=args.baseline,
            new_score=args.score,
            decision=args.decision,
            commit_hash=args.commit_hash if args.commit_hash != "none" else None,
            metrics=json.loads(args.metrics) if args.metrics else {},
            traces_count=args.traces_count,
        )
        print("logged")

    elif sub == "status":
        config_path = Path(args.config)
        if config_path.exists():
            config = parse_program_md(config_path)
            config.eval_dir = args.eval_dir
        else:
            config = engine.RatchetConfig(eval_dir=args.eval_dir)
        status = engine.ratchet_status(args.eval_dir, config)
        print(json.dumps(status, indent=2))

    elif sub == "branch":
        branch = git_ops.create_ratchet_branch()
        print(branch)


def cmd_benchmark(args):
    """Run benchmark, store results, compare against previous."""
    from recursive_improve.benchmark import (
        run_benchmark, list_benchmarks, format_benchmark_result,
        format_benchmark_list, format_comparison,
    )
    from recursive_improve.store.json_store import JSONRunStore

    if args.benchmark_command == "list":
        benchmarks = list_benchmarks(eval_dir=args.eval_dir)
        print(f"\n{format_benchmark_list(benchmarks)}\n")
        return

    # Default: run a new benchmark
    print(f"\n  Running benchmark...")
    result = run_benchmark(
        label=args.label,
        traces_dir=args.traces_dir,
        eval_dir=args.eval_dir,
    )

    print(f"\n{format_benchmark_result(result)}")

    if "error" not in result:
        # Auto-compare against previous benchmark
        store = JSONRunStore(store_path=Path(args.eval_dir) / "benchmark_results.json")
        benchmarks = list_benchmarks(eval_dir=args.eval_dir)

        if len(benchmarks) >= 2:
            prev = benchmarks[1]  # second most recent (list is newest-first)
            meta = {}
            if prev.get("label"):
                meta["label"] = prev["label"]
            prev["label"] = prev.get("label", prev["run_id"][:8])
            current = {"run_id": result["run_id"], "label": result["label"]}
            print(f"\n{format_comparison(current, prev, store)}")

    print()


def cmd_store_baseline(args):
    """Read baseline_metrics.json and store as a benchmark run in the JSON store."""
    import subprocess
    import uuid
    from datetime import datetime, timezone
    from recursive_improve.store.json_store import JSONRunStore

    eval_dir = Path(args.eval_dir)
    baseline_path = eval_dir / "baseline_metrics.json"

    if not baseline_path.exists():
        print(f"Error: {baseline_path} not found")
        sys.exit(1)

    data = json.loads(baseline_path.read_text())
    raw_metrics = data.get("metrics", data)

    # Filter to actual metric dicts
    metrics = {}
    for k, v in raw_metrics.items():
        if isinstance(v, dict) and "value" in v:
            metrics[k] = {
                "numerator": v.get("numerator", 0),
                "denominator": v.get("denominator", 0),
                "value": v.get("value", 0),
                "confidence": v.get("confidence", "unknown"),
            }

    if not metrics:
        print("Error: no metrics found in baseline_metrics.json")
        sys.exit(1)

    # Git info
    branch = None
    commit = None
    try:
        r = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            branch = r.stdout.strip()
    except Exception:
        pass
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            commit = r.stdout.strip()
    except Exception:
        pass

    run_id = uuid.uuid4().hex[:12]
    timestamp = datetime.now(timezone.utc).isoformat()
    label = args.label or f"baseline-{branch or run_id[:6]}"
    trace_count = data.get("trace_count", 0)

    store = JSONRunStore(store_path=eval_dir / "benchmark_results.json")
    store.insert_run(
        run_id=run_id,
        branch=branch,
        commit_hash=commit,
        timestamp=timestamp,
        traces_dir=str(eval_dir / "traces"),
        success=True,
        metadata={
            "label": label,
            "type": "baseline",
            "metric_count": len(metrics),
            "trace_count": trace_count,
        },
    )
    store.insert_metrics(run_id, metrics)

    print(f"\n  Stored baseline as benchmark run")
    print(f"  Run ID:  {run_id}")
    print(f"  Branch:  {branch or '-'}")
    print(f"  Label:   {label}")
    print(f"  Metrics: {len(metrics)}")
    print(f"  File:    {eval_dir / 'benchmark_results.json'}\n")


def cmd_migrate(args):
    """Migrate existing eval/iterations/ data into JSON store."""
    from recursive_improve.store.json_store import JSONRunStore
    import json as _json

    eval_dir = Path(args.eval_dir)
    iterations_dir = eval_dir / "iterations"

    if not iterations_dir.exists():
        print(f"No iterations directory found at {iterations_dir}")
        return

    store = JSONRunStore(store_path=eval_dir / "benchmark_results.json")
    count = 0

    for d in sorted(iterations_dir.iterdir()):
        if not d.is_dir() or d.name == "latest":
            continue

        run_id = d.name
        timestamp = ""
        branch = None
        trace_count = 0

        manifest_path = d / "manifest.json"
        if manifest_path.exists():
            try:
                manifest = _json.loads(manifest_path.read_text())
                timestamp = manifest.get("timestamp", "")
                trace_count = manifest.get("trace_count", 0)
            except Exception:
                pass

        store.insert_run(
            run_id=run_id,
            branch=branch,
            timestamp=timestamp or d.name,
            traces_dir=str(eval_dir / "traces"),
            success=True,
            metadata={"migrated": True, "trace_count": trace_count},
        )

        # Migrate baseline metrics if present
        metrics_path = d / "baseline_metrics.json"
        if metrics_path.exists():
            try:
                data = _json.loads(metrics_path.read_text())
                metrics = {}
                for k, v in data.items():
                    if k in ("warnings", "unmeasurable") or not isinstance(v, dict):
                        continue
                    metrics[k] = {
                        "numerator": v.get("numerator", 0),
                        "denominator": v.get("denominator", 0),
                        "value": v.get("value", v.get("rate", 0)),
                        "confidence": v.get("confidence", "unknown"),
                    }
                if metrics:
                    store.insert_metrics(run_id, metrics)
            except Exception:
                pass

        count += 1

    print(f"  Migrated {count} iterations to eval/benchmark_results.json")


def main():
    parser = argparse.ArgumentParser(
        prog="recursive-improve",
        description="Recursively improve AI agents from their traces",
    )
    subparsers = parser.add_subparsers(dest="command")

    # init
    subparsers.add_parser("init", help="Set up recursive-improve in the current project")

    # eval
    p_eval = subparsers.add_parser("eval", help="Run built-in detectors on traces")
    p_eval.add_argument("traces_dir", type=str, help="Directory containing trace files")
    p_eval.add_argument("--branch", "-b", type=str, default=None, help="Branch name for this eval run")
    p_eval.add_argument("--output-dir", "-o", type=str, default="./eval", help="Output directory (default: ./eval)")

    # compare
    p_compare = subparsers.add_parser("compare", help="Compare metrics between runs/branches")
    p_compare.add_argument("left", help="Left reference (run_id, branch, or commit)")
    p_compare.add_argument("right", help="Right reference (run_id, branch, or commit)")
    p_compare.add_argument("-o", "--eval-dir", type=str, default="./eval", help="Eval directory")

    # dashboard
    p_dash = subparsers.add_parser("dashboard", help="Launch the improvement dashboard")
    p_dash.add_argument("-p", "--port", type=int, default=8420, help="Port (default: 8420)")
    p_dash.add_argument("-o", "--eval-dir", type=str, default="./eval", help="Eval directory")

    # ratchet (with subcommands)
    p_ratchet = subparsers.add_parser("ratchet", help="Ratchet loop utilities")
    ratchet_sub = p_ratchet.add_subparsers(dest="ratchet_command")

    # ratchet eval — run eval + composite score
    p_re = ratchet_sub.add_parser("eval", help="Eval traces and compute composite score")
    p_re.add_argument("--config", "-c", default="program.md", help="Config file")
    p_re.add_argument("-o", "--eval-dir", default="./eval", help="Eval directory")

    # ratchet commit — git commit iteration
    p_rc = ratchet_sub.add_parser("commit", help="Commit ratchet iteration")
    p_rc.add_argument("iteration", type=int, help="Iteration number")
    p_rc.add_argument("score", type=float, help="New score")
    p_rc.add_argument("--prev-score", type=float, default=None, help="Previous score")

    # ratchet revert — revert to last commit
    ratchet_sub.add_parser("revert", help="Revert working tree to last commit")

    # ratchet log — append iteration to log
    p_rl = ratchet_sub.add_parser("log", help="Log a ratchet iteration")
    p_rl.add_argument("iteration", type=int)
    p_rl.add_argument("score", type=float)
    p_rl.add_argument("decision", choices=["keep", "revert", "skip"])
    p_rl.add_argument("--baseline", type=float, required=True)
    p_rl.add_argument("--duration", type=float, default=0)
    p_rl.add_argument("--commit-hash", default="none")
    p_rl.add_argument("--metrics", default="{}", help="JSON metrics string")
    p_rl.add_argument("--traces-count", type=int, default=0)
    p_rl.add_argument("-o", "--eval-dir", default="./eval")

    # ratchet status — show current progress
    p_rs = ratchet_sub.add_parser("status", help="Show ratchet progress")
    p_rs.add_argument("--config", "-c", default="program.md")
    p_rs.add_argument("-o", "--eval-dir", default="./eval")

    # ratchet branch — create ratchet branch
    ratchet_sub.add_parser("branch", help="Create a ratchet branch")

    # benchmark
    p_bench = subparsers.add_parser("benchmark", help="Snapshot and compare metric quality")
    bench_sub = p_bench.add_subparsers(dest="benchmark_command")
    bench_sub.add_parser("list", help="List all stored benchmarks")
    p_bench.add_argument("--label", "-l", type=str, default=None, help="Label for this benchmark run")
    p_bench.add_argument("--traces-dir", "-t", type=str, default="eval/traces", help="Traces directory")
    p_bench.add_argument("-o", "--eval-dir", type=str, default="./eval", help="Eval directory")

    # store-baseline
    p_store = subparsers.add_parser("store-baseline",
        help="Store baseline_metrics.json as a benchmark run in the JSON store")
    p_store.add_argument("--label", "-l", type=str, default=None, help="Label for this run")
    p_store.add_argument("-o", "--eval-dir", type=str, default="./eval", help="Eval directory")

    # migrate
    p_migrate = subparsers.add_parser("migrate", help="Migrate iterations to JSON store")
    p_migrate.add_argument("-o", "--eval-dir", type=str, default="./eval", help="Eval directory")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    handlers = {
        "init": cmd_init,
        "eval": cmd_eval,
        "compare": cmd_compare,
        "dashboard": cmd_dashboard,
        "ratchet": cmd_ratchet,
        "benchmark": cmd_benchmark,
        "store-baseline": cmd_store_baseline,
        "migrate": cmd_migrate,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
