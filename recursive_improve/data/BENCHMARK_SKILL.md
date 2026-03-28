---
name: benchmark
description: >
  Run metric quality benchmark, store results, and compare against previous runs.
  Invoke with /benchmark, "run benchmark", "benchmark metrics", "check metric quality".
---

# /benchmark — Metric Quality Benchmark

Snapshot the current state of metric detectors, store results, and compare against previous benchmarks.

## Usage

### Run a new benchmark
```bash
recursive-improve benchmark --label "description-of-changes"
```

This will:
1. Run all metric detectors against the traces
2. Evaluate metric quality (detector runs, count parity, denominator quality, spread, coverage)
3. Store results in `eval/benchmark_results.json`
4. Auto-compare against the most recent previous benchmark
5. Print a summary table

### List all benchmarks
```bash
recursive-improve benchmark list
```

Shows the history of all stored benchmarks with scores and deltas.

### When to run

- **Before making changes** — label it with the current state (e.g., `--label v1-baseline`)
- **After improving metrics** — label it with what changed (e.g., `--label fix-denominator-scoping`)
- **After each ratchet iteration** — automatic if using the ratchet loop

### Reading the output

Quality signals (0-1, higher = better):
- **detector_run_rate** — fraction of metrics whose detector code runs without errors
- **count_parity_rate** — fraction where len(event_ids) matches denominator
- **denominator_quality** — average denominator sanity score (not too small, no extreme values)
- **spread_quality** — failures spread across traces, not concentrated in 1-2
- **skill_coverage_rate** — fraction of skills with a metric or unmeasurable classification
- **composite_quality** — weighted overall score

Per-skill metrics are also stored for detailed analysis.
