"""Dashboard: list → detail view for improvement runs and pipeline analysis."""

from __future__ import annotations

import json
import time
from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from recursive_improve.store.json_store import JSONRunStore
from recursive_improve.store import git_reader


def create_app(eval_dir: Path, db_path: str | None = None, cwd: str | None = None) -> Starlette:
    store = JSONRunStore(store_path=eval_dir / "benchmark_results.json")
    _cwd = cwd or str(Path.cwd())
    _store_rel = "eval/benchmark_results.json"

    # In-memory cache for git results (15s TTL)
    _cache: dict = {"runs": None, "branches": None, "ts": 0}
    _CACHE_TTL = 15

    def _get_all_runs_cached():
        now = time.time()
        if _cache["runs"] is not None and (now - _cache["ts"]) < _CACHE_TTL:
            return _cache["runs"]
        runs = git_reader.load_runs_from_all_branches(_store_rel, _cwd)
        _cache["runs"] = runs
        _cache["ts"] = now
        return runs

    def _get_branches_cached():
        now = time.time()
        if _cache["branches"] is not None and (now - _cache["ts"]) < _CACHE_TTL:
            return _cache["branches"]
        branches = git_reader.list_branches(_cwd)
        _cache["branches"] = branches
        return branches

    async def api_runs(request):
        runs = _get_all_runs_cached()
        # Only show runs with metrics
        result = []
        for run in runs:
            metrics = run.get("metrics", {})
            if not metrics:
                continue
            # Build flat run dict for list view (metrics as name->value)
            r = {k: v for k, v in run.items() if k != "metrics"}
            r["metrics"] = {name: m.get("value") for name, m in metrics.items()}
            result.append(r)
        return JSONResponse({"runs": result})

    async def api_run_detail(request):
        run_id = request.path_params["run_id"]
        runs = _get_all_runs_cached()
        run = next((r for r in runs if r.get("id") == run_id), None)
        if not run:
            return JSONResponse({"error": "not found"}, status_code=404)
        # Convert embedded metrics to list-of-dicts format
        metrics_dict = run.get("metrics", {})
        metrics_list = [
            {
                "run_id": run_id,
                "metric_name": name,
                "numerator": m.get("numerator"),
                "denominator": m.get("denominator"),
                "value": m.get("value"),
                "confidence": m.get("confidence"),
                "details": json.dumps(m.get("details")) if m.get("details") else None,
            }
            for name, m in metrics_dict.items()
        ]
        run_without_metrics = {k: v for k, v in run.items() if k != "metrics"}
        return JSONResponse({"run": run_without_metrics, "metrics": metrics_list})

    async def api_compare(request):
        left = request.query_params.get("left")
        right = request.query_params.get("right")
        if not left or not right:
            return JSONResponse({"error": "left and right params required"}, status_code=400)
        from recursive_improve.eval.compare import compare_runs
        return JSONResponse(compare_runs(left, right, store=store))

    async def api_branches(request):
        branches = _get_branches_cached()
        return JSONResponse({"branches": branches})

    _STAGE_FILES = [
        ("trace_analysis", "stage0_trace_analysis.md", "Trace Analysis"),
        ("insights", "stage1_insights_summary.md", "Insights"),
        ("domain_context", "stage2_domain_context.md", "Domain Context"),
        ("rubric", "baseline_metrics.md", "Evaluation Rubric"),
        ("action_plan", "action_plan.md", "Action Plan"),
        ("decision", "stage6_decision.md", "Review Decision"),
        ("changes_log", "changes_log.md", "Changes Log"),
    ]

    async def api_analysis(request):
        branch = request.query_params.get("branch")
        stages = {}
        for key, filename, label in _STAGE_FILES:
            content = None
            if branch:
                content = git_reader.read_file_from_branch(
                    branch, f"eval/{filename}", _cwd)
            if content is None:
                path = eval_dir / filename
                if path.exists():
                    content = path.read_text(encoding="utf-8", errors="replace")
            if content:
                stages[key] = {"label": label, "content": content}
        return JSONResponse({"stages": stages})

    async def api_cycles(request):
        """Return improvement cycles grouped by branch."""
        runs = _get_all_runs_cached()

        by_branch: dict[str, list] = {}
        for run in runs:
            branch = run.get("branch") or "unknown"
            by_branch.setdefault(branch, []).append(run)

        cycles = []
        for branch, branch_runs in by_branch.items():
            baseline_run = None
            postfix_run = None

            for r in branch_runs:
                meta = r.get("metadata")
                if isinstance(meta, str):
                    try:
                        meta = json.loads(meta)
                    except Exception:
                        meta = {}
                meta = meta or {}
                if meta.get("type") == "baseline":
                    if baseline_run is None or r.get("timestamp", "") < baseline_run.get("timestamp", ""):
                        baseline_run = r
                elif r.get("metrics"):
                    # Only consider runs with metrics for post-fix comparison
                    if postfix_run is None or r.get("timestamp", "") > postfix_run.get("timestamp", ""):
                        postfix_run = r

            if not baseline_run:
                sorted_r = sorted(branch_runs, key=lambda x: x.get("timestamp", ""))
                baseline_run = sorted_r[0]
                if len(sorted_r) > 1:
                    postfix_run = sorted_r[-1]
                else:
                    postfix_run = None

            if postfix_run and postfix_run.get("id") == baseline_run.get("id"):
                postfix_run = None

            b_metrics = baseline_run.get("metrics", {})
            p_metrics = postfix_run.get("metrics", {}) if postfix_run else {}

            metric_names = sorted(b_metrics.keys()) if b_metrics else sorted(p_metrics.keys())

            comparisons = []
            improved_count = 0
            healthy_count = 0

            for name in metric_names:
                bm = b_metrics.get(name, {})
                pm = p_metrics.get(name, {})
                b_val = bm.get("value") if isinstance(bm, dict) else None
                p_val = pm.get("value") if isinstance(pm, dict) else None

                is_good = "success" in name or "recovery" in name
                healthy = False
                if b_val is not None:
                    healthy = (b_val >= 0.5) if is_good else (b_val <= 0.2)
                if healthy:
                    healthy_count += 1

                status = "pending"
                improvement_pct = None
                if b_val is not None and p_val is not None:
                    if is_good:
                        raw_delta = p_val - b_val
                        improvement_pct = (raw_delta / b_val * 100) if b_val > 0 else (100.0 if raw_delta > 0 else 0.0)
                    else:
                        raw_delta = b_val - p_val
                        improvement_pct = (raw_delta / b_val * 100) if b_val > 0 else (100.0 if raw_delta > 0 else 0.0)

                    if improvement_pct >= 50:
                        status = "improved"
                        improved_count += 1
                    elif improvement_pct > 0:
                        status = "slightly_improved"
                    elif improvement_pct < -10:
                        status = "regressed"
                    else:
                        status = "unchanged"

                comp: dict = {
                    "name": name,
                    "baseline": b_val,
                    "postfix": p_val,
                    "status": status,
                    "improvement_pct": round(improvement_pct, 1) if improvement_pct is not None else None,
                    "healthy": healthy,
                }
                if isinstance(bm, dict) and bm.get("denominator"):
                    comp["baseline_frac"] = f"{bm.get('numerator')}/{bm.get('denominator')}"
                if isinstance(pm, dict) and pm.get("denominator"):
                    comp["postfix_frac"] = f"{pm.get('numerator')}/{pm.get('denominator')}"
                comparisons.append(comp)

            meta = baseline_run.get("metadata")
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}

            cycles.append({
                "branch": branch,
                "timestamp": baseline_run.get("timestamp"),
                "label": (meta or {}).get("label"),
                "has_postfix": postfix_run is not None,
                "comparisons": comparisons,
                "score": {
                    "healthy": healthy_count,
                    "improved": improved_count,
                    "total": len(metric_names),
                },
            })

        cycles.sort(key=lambda c: c.get("timestamp", ""), reverse=True)
        return JSONResponse({"cycles": cycles})

    async def api_baseline_metrics(request):
        branch = request.query_params.get("branch")
        if branch:
            content = git_reader.read_file_from_branch(
                branch, "eval/baseline_metrics.json", _cwd)
            if content:
                try:
                    return JSONResponse(json.loads(content))
                except Exception:
                    pass
            return JSONResponse({"metrics": {}, "per_trace": {}})
        path = eval_dir / "baseline_metrics.json"
        if not path.exists():
            return JSONResponse({"metrics": {}, "per_trace": {}})
        try:
            return JSONResponse(json.loads(path.read_text()))
        except Exception:
            return JSONResponse({"metrics": {}, "per_trace": {}})

    async def api_eval_results(request):
        path = eval_dir / "eval_results.json"
        if not path.exists():
            return JSONResponse({"metrics": {}, "per_trace": {}})
        try:
            return JSONResponse(json.loads(path.read_text()))
        except Exception:
            return JSONResponse({"metrics": {}, "per_trace": {}})

    async def api_improvement(request):
        """Compare baseline vs post-fix metrics side by side."""
        baseline_path = eval_dir / "baseline_metrics.json"
        postfix_path = eval_dir / "post_fix_metrics.json"
        result = {"baseline": {}, "post_fix": {}, "has_comparison": False}
        if baseline_path.exists():
            try:
                data = json.loads(baseline_path.read_text())
                result["baseline"] = data.get("metrics", data)
            except Exception:
                pass
        if postfix_path.exists():
            try:
                data = json.loads(postfix_path.read_text())
                result["post_fix"] = data.get("metrics", data)
                result["has_comparison"] = True
            except Exception:
                pass
        # Also pull action plan targets if available
        action_path = eval_dir / "action_plan.md"
        if action_path.exists():
            import re
            content = action_path.read_text(encoding="utf-8", errors="replace")
            # Extract target metrics like "fabrication_rate (21.4% → ~5%)"
            targets = {}
            for m in re.finditer(r'(\w+_rate)\s*\([\d.]+%\s*→\s*~?([\d.]+)%\)', content):
                targets[m.group(1)] = float(m.group(2)) / 100
            result["targets"] = targets
        return JSONResponse(result)

    async def api_changes(request):
        """Return changes log parsed into structured fixes."""
        import re
        branch = request.query_params.get("branch")
        raw = None
        if branch:
            raw = git_reader.read_file_from_branch(
                branch, "eval/changes_log.md", _cwd)
        if raw is None:
            path = eval_dir / "changes_log.md"
            if not path.exists():
                return JSONResponse({"fixes": [], "raw": ""})
            raw = path.read_text(encoding="utf-8", errors="replace")
        fixes = []
        sections = re.split(r'^## ', raw, flags=re.MULTILINE)[1:]
        for section in sections:
            if section.strip().lower().startswith('conflict'):
                continue
            lines = section.strip().split('\n')
            title = lines[0].strip() if lines else ''
            body = '\n'.join(lines[1:])
            fix = {"title": title, "type": "", "verdict": "", "files": "",
                   "metrics": "", "before": "", "after": "", "notes": ""}
            for line in lines[1:]:
                line = line.strip().lstrip('- ')
                if line.startswith('**Type'):
                    fix["type"] = line.split(':', 1)[1].strip().strip('*') if ':' in line else ''
                elif line.startswith('**Verdict'):
                    fix["verdict"] = line.split(':', 1)[1].strip().strip('*') if ':' in line else ''
                elif line.startswith('**File'):
                    fix["files"] = line.split(':', 1)[1].strip().strip('*').strip('`') if ':' in line else ''
                elif line.startswith('**Linked'):
                    fix["metrics"] = line.split(':', 1)[1].strip().strip('*') if ':' in line else ''
            before_m = re.search(r'\*\*Before\*\*.*?```[^\n]*\n(.*?)```', body, re.DOTALL)
            after_m = re.search(r'\*\*After\*\*.*?```[^\n]*\n(.*?)```', body, re.DOTALL)
            if before_m:
                fix["before"] = before_m.group(1).strip()
            if after_m:
                fix["after"] = after_m.group(1).strip()
            fixes.append(fix)
        return JSONResponse({"fixes": fixes, "raw": raw})

    async def index(request):
        return HTMLResponse(DASHBOARD_HTML)

    return Starlette(routes=[
        Route("/", index),
        Route("/api/runs", api_runs),
        Route("/api/cycles", api_cycles),
        Route("/api/runs/{run_id}", api_run_detail),
        Route("/api/compare", api_compare),
        Route("/api/branches", api_branches),
        Route("/api/analysis", api_analysis),
        Route("/api/baseline-metrics", api_baseline_metrics),
        Route("/api/eval-results", api_eval_results),
        Route("/api/changes", api_changes),
        Route("/api/improvement", api_improvement),
    ])


DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>recursive-improve</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
  :root {
    --bg: #141820;
    --bg-surface: #1A1E28;
    --bg-row: #1A1E28;
    --bg-row-hover: #232834;
    --bg-detail: #111318;
    --border: rgba(255,255,255,0.08);
    --border-light: rgba(255,255,255,0.12);
    --text: #A0ABBE;
    --text-dim: #8896A6;
    --text-bright: #E4E8F0;
    --green: #059669;
    --green-bg: rgba(5,150,105,0.14);
    --orange: #D4A017;
    --orange-bg: rgba(212,160,23,0.14);
    --red: #E54D4D;
    --red-bg: rgba(229,77,77,0.14);
    --blue: #6B8BA8;
    --blue-bg: rgba(107,139,168,0.14);
    --purple: #8878A8;
    --yellow: #B8A060;
    --primary: #6B8BA8;
    --r: 6px;
    --r-sm: 4px;
  }
  * { margin:0; padding:0; box-sizing:border-box; }
  body {
    font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', sans-serif;
    background: var(--bg); color: var(--text);
    line-height: 1.55; -webkit-font-smoothing: antialiased; font-weight: 500;
    position: relative;
  }
  body::before {
    content: '';
    position: fixed; inset: 0; z-index: 0; pointer-events: none;
    background-image:
      radial-gradient(circle at 1px 1px, rgba(255,255,255,0.15) 1px, transparent 0);
    background-size: 20px 20px;
    -webkit-mask-image:
      radial-gradient(ellipse 70% 50% at 30% 75%, rgba(0,0,0,0.8) 0%, rgba(0,0,0,0.15) 70%),
      radial-gradient(ellipse 60% 45% at 70% 25%, rgba(0,0,0,0.6) 0%, rgba(0,0,0,0.12) 65%),
      radial-gradient(ellipse 50% 40% at 50% 95%, rgba(0,0,0,0.7) 0%, rgba(0,0,0,0.1) 60%);
    mask-image:
      radial-gradient(ellipse 70% 50% at 30% 75%, rgba(0,0,0,0.8) 0%, rgba(0,0,0,0.15) 70%),
      radial-gradient(ellipse 60% 45% at 70% 25%, rgba(0,0,0,0.6) 0%, rgba(0,0,0,0.12) 65%),
      radial-gradient(ellipse 50% 40% at 50% 95%, rgba(0,0,0,0.7) 0%, rgba(0,0,0,0.1) 60%);
  }
  body::after {
    content: '';
    position: fixed; inset: 0; z-index: 0; pointer-events: none;
    background:
      radial-gradient(ellipse 70% 50% at 30% 75%, rgba(80,160,200,0.18) 0%, transparent 65%),
      radial-gradient(ellipse 60% 45% at 70% 25%, rgba(60,100,200,0.15) 0%, transparent 60%),
      radial-gradient(ellipse 50% 40% at 50% 95%, rgba(80,210,190,0.14) 0%, transparent 55%);
  }
  body > * { position: relative; z-index: 1; }
  .mono { font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace; }

  /* Top bar */
  .topbar {
    display: flex; align-items: center; justify-content: space-between;
    padding: 0.75rem 1.5rem; border-bottom: 1px solid var(--border);
    background: var(--bg-surface); position: sticky; top: 0; z-index: 100;
  }
  .topbar-left { display: flex; align-items: center; gap: 1rem; }
  .topbar h1 { font-size: 0.95rem; color: var(--text-bright); font-weight: 600; }
  .topbar h1 span { color: var(--primary); }
  .logo-art { height: 36px; width: auto; }
  .topbar-right { display: flex; align-items: center; gap: 0.75rem; }
  select {
    background: var(--bg-row); color: var(--text); border: 1px solid var(--border);
    padding: 0.35rem 0.6rem; border-radius: var(--r-sm); font-size: 0.78rem;
    cursor: pointer; outline: none; transition: border-color 0.2s;
  }
  select:focus { border-color: var(--primary); box-shadow: 0 0 0 3px rgba(107,139,168,0.2); }
  .filter-label { font-size: 0.72rem; color: var(--text-dim); }

  /* Score badge */
  .score {
    display: inline-flex; align-items: center; justify-content: center; gap: 0.3rem;
    padding: 0.2rem 0.55rem; border-radius: 999px;
    font-size: 0.72rem; font-weight: 600; font-family: 'SF Mono', monospace; white-space: nowrap;
  }
  .score-good { background: var(--green-bg); color: var(--green); }
  .score-warn { background: var(--orange-bg); color: var(--orange); }
  .score-bad { background: var(--red-bg); color: var(--red); }
  .score-info { background: var(--blue-bg); color: var(--blue); }

  /* Tag badge */
  .tag {
    display: inline-block; padding: 0.15rem 0.55rem; border-radius: 999px;
    font-size: 0.7rem; font-weight: 500; background: var(--blue-bg); color: var(--blue);
    max-width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .tag-green { background: var(--green-bg); color: var(--green); }
  .tag-orange { background: var(--orange-bg); color: var(--orange); }
  .tag-purple { background: rgba(136,120,168,0.14); color: var(--purple); }
  .tag-dim { background: rgba(255,255,255,0.05); color: var(--text-dim); }

  /* ===== LIST VIEW ===== */
  #list-view { min-height: 100vh; }
  .runs-table { width: 100%; border-collapse: collapse; }
  .runs-table th {
    position: sticky; top: 49px; z-index: 50;
    text-align: left; padding: 0.6rem 0.85rem; font-size: 0.68rem;
    text-transform: uppercase; letter-spacing: 0.06em; font-weight: 600;
    color: var(--text-dim); background: var(--bg-surface);
    border-bottom: 1px solid var(--border);
  }
  .runs-table td {
    padding: 0.65rem 0.85rem; border-bottom: 1px solid var(--border);
    font-size: 0.8rem; vertical-align: middle;
  }
  .runs-table tr.run-row { cursor: pointer; background: var(--bg); transition: all 0.2s; }
  .runs-table tr.run-row:hover { background: var(--bg-row-hover); }
  .scores-cell { display: flex; gap: 0.35rem; flex-wrap: wrap; }
  .time-ago { color: var(--text-dim); font-size: 0.78rem; }
  .duration { color: var(--text-dim); font-size: 0.78rem; font-family: 'SF Mono', monospace; }
  .message-text { color: var(--text-bright); font-size: 0.8rem; max-width: 350px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .empty-state { padding: 3rem; text-align: center; color: var(--text-dim); font-size: 0.85rem; }

  /* ===== DETAIL VIEW ===== */
  #detail-view { display: none; }
  .detail-topbar {
    display: flex; align-items: center; gap: 0.85rem;
    padding: 0.85rem 1.5rem; border-bottom: 1px solid var(--border);
    background: var(--bg-surface); position: sticky; top: 0; z-index: 100;
  }
  .back-btn {
    background: none; border: 1px solid var(--border); color: var(--text);
    padding: 0.3rem 0.7rem; border-radius: var(--r-sm); cursor: pointer;
    font-size: 0.78rem; transition: all 0.2s;
  }
  .back-btn:hover { border-color: var(--primary); color: var(--text-bright); }
  .detail-title { font-size: 0.9rem; color: var(--text-bright); font-weight: 500; }
  .detail-meta { display: flex; gap: 0.5rem; margin-left: auto; }
  .detail-body { padding: 1.5rem; max-width: 1100px; margin: 0 auto; }

  /* Metrics row in detail */
  .metrics-strip { display: flex; gap: 0.5rem; flex-wrap: wrap; margin-bottom: 1.75rem; }
  .metric-pill {
    background: var(--bg-surface); border: 1px solid var(--border); border-radius: var(--r);
    padding: 0.85rem 1.1rem; min-width: 140px;
  }
  .metric-pill-label { font-size: 0.68rem; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 0.25rem; }
  .metric-pill-value { font-size: 1.5rem; font-weight: 700; letter-spacing: -0.03em; }

  /* Per-trace grid */
  .section-label { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-dim); font-weight: 600; margin-bottom: 0.65rem; margin-top: 1.75rem; }
  .trace-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 0.5rem; }
  .trace-card {
    background: var(--bg-surface); border: 1px solid var(--border); border-radius: 10px;
    padding: 0.85rem;
  }
  .trace-card-name { font-size: 0.78rem; font-weight: 600; color: var(--text-bright); margin-bottom: 0.4rem; }
  .trace-card-tags { display: flex; flex-wrap: wrap; gap: 0.3rem; }
  .trace-tag {
    font-size: 0.66rem; padding: 0.12rem 0.4rem; border-radius: 3px;
    background: rgba(255,255,255,0.04); color: var(--text-dim);
  }
  .trace-tag.fired { background: var(--red-bg); color: var(--red); }
  .trace-tag.clean { background: var(--green-bg); color: var(--green); }

  /* Analysis panels */
  .analysis-panel {
    background: var(--bg-surface); border: 1px solid var(--border);
    border-radius: 10px; margin-bottom: 0.5rem; overflow: hidden;
  }
  .panel-header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 0.75rem 1rem; cursor: pointer; user-select: none;
  }
  .panel-header:hover { background: var(--bg-row-hover); }
  .panel-header h3 { font-size: 0.82rem; font-weight: 500; color: var(--text-bright); }
  .panel-chevron { font-size: 0.7rem; color: var(--text-dim); transition: transform 0.2s; }
  .analysis-panel.open .panel-chevron { transform: rotate(180deg); }
  .panel-body { max-height: 0; overflow: hidden; transition: max-height 0.35s ease; }
  .analysis-panel.open .panel-body { max-height: 50000px; }

  /* Rendered markdown */
  .md {
    padding: 0 1rem 1rem; font-size: 0.8rem; line-height: 1.7; color: var(--text);
  }
  .md h1 { font-size: 1.05rem; margin: 1.2rem 0 0.5rem; color: var(--text-bright); font-weight: 600; }
  .md h2 { font-size: 0.92rem; margin: 1rem 0 0.4rem; color: var(--text-bright); font-weight: 600; padding-bottom: 0.3rem; border-bottom: 1px solid var(--border); }
  .md h3 { font-size: 0.84rem; margin: 0.8rem 0 0.3rem; color: var(--text-bright); font-weight: 600; }
  .md h4 { font-size: 0.8rem; margin: 0.6rem 0 0.25rem; color: var(--text-bright); font-weight: 600; }
  .md p { margin: 0.4rem 0; }
  .md ul, .md ol { margin: 0.35rem 0 0.35rem 1.5rem; }
  .md li { margin: 0.15rem 0; }
  .md strong { color: var(--text-bright); }
  .md em { color: var(--text-dim); }
  .md code {
    background: rgba(255,255,255,0.06); padding: 0.12rem 0.4rem;
    border-radius: 3px; font-size: 0.76rem; font-family: 'SF Mono', 'Fira Code', monospace;
  }
  .md pre {
    background: #181B22; border: 1px solid var(--border);
    border-radius: 8px; padding: 0.85rem; margin: 0.5rem 0;
    overflow-x: auto; font-size: 0.76rem; line-height: 1.5;
  }
  .md pre code { background: none; padding: 0; }
  .md blockquote {
    border-left: 3px solid var(--primary); padding: 0.4rem 0 0.4rem 0.85rem;
    margin: 0.5rem 0; color: var(--text-dim); background: rgba(107,139,168,0.06);
    border-radius: 0 var(--r-sm) var(--r-sm) 0;
  }
  /* Markdown tables */
  .md table {
    width: 100%; border-collapse: separate; border-spacing: 0;
    margin: 0.6rem 0; font-size: 0.76rem;
    border: 1px solid var(--border); border-radius: 8px; overflow: hidden;
  }
  .md thead { background: rgba(0,0,0,0.25); }
  .md th {
    text-align: left; padding: 0.55rem 0.75rem; font-weight: 600;
    color: var(--text-dim); font-size: 0.7rem; text-transform: uppercase;
    letter-spacing: 0.03em; border-bottom: 1px solid var(--border-light);
  }
  .md td {
    padding: 0.5rem 0.75rem; border-bottom: 1px solid var(--border);
    vertical-align: top;
  }
  .md tbody tr:last-child td { border-bottom: none; }
  .md tbody tr:hover { background: rgba(255,255,255,0.02); }
  .md hr { border: none; border-top: 1px solid var(--border); margin: 1rem 0; }

  /* Compare section in detail */
  .compare-section { margin-top: 1.75rem; }
  .compare-bar { display: flex; gap: 0.5rem; align-items: center; margin-bottom: 0.75rem; flex-wrap: wrap; }
  .btn {
    padding: 0.35rem 0.85rem; background: var(--primary); color: #fff;
    border: none; border-radius: var(--r-sm); cursor: pointer;
    font-size: 0.78rem; font-weight: 600; transition: all 0.2s;
  }
  .btn:hover { opacity: 0.9; box-shadow: 0 1px 3px rgba(0,0,0,0.3); }
  .compare-table { width: 100%; border-collapse: collapse; font-size: 0.8rem; }
  .compare-table th { text-align: left; padding: 0.5rem 0.75rem; color: var(--text-dim); font-size: 0.7rem; text-transform: uppercase; border-bottom: 1px solid var(--border-light); }
  .compare-table td { padding: 0.45rem 0.75rem; border-bottom: 1px solid var(--border); }
  .delta-up { color: var(--red); }
  .delta-down { color: var(--green); }

  /* Improvement table */
  .improvement-table {
    width: 100%; border-collapse: separate; border-spacing: 0;
    background: var(--bg-surface); border: 1px solid var(--border);
    border-radius: 10px; overflow: hidden; font-size: 0.82rem;
  }
  .improvement-table th {
    text-align: left; padding: 0.6rem 0.85rem; font-size: 0.68rem;
    text-transform: uppercase; letter-spacing: 0.05em; font-weight: 600;
    color: var(--text-dim); background: rgba(0,0,0,0.2);
    border-bottom: 1px solid var(--border-light);
  }
  .improvement-table td {
    padding: 0.6rem 0.85rem; border-bottom: 1px solid var(--border);
    vertical-align: middle;
  }
  .improvement-table tr:last-child td { border-bottom: none; }
  .improvement-table .metric-name { font-weight: 500; color: var(--text-bright); }
  .improvement-table .val-baseline { color: var(--text-dim); }
  .improvement-table .val-target { color: var(--blue); font-style: italic; }
  .improvement-table .val-postfix { font-weight: 600; }
  .delta-badge {
    display: inline-block; padding: 0.15rem 0.5rem; border-radius: 999px;
    font-size: 0.72rem; font-weight: 600; font-family: 'SF Mono', monospace;
  }
  .delta-improved { background: var(--green-bg); color: var(--green); }
  .delta-regressed { background: var(--red-bg); color: var(--red); }
  .delta-unchanged { background: rgba(255,255,255,0.04); color: var(--text-dim); }
  .delta-pending { background: rgba(255,255,255,0.03); color: var(--text-dim); font-style: italic; font-family: inherit; }
  .improvement-note {
    margin-top: 0.5rem; font-size: 0.76rem; color: var(--text-dim);
    padding: 0.6rem 0.85rem; background: var(--bg-surface);
    border: 1px solid var(--border); border-radius: var(--r);
  }
  .improvement-note code { background: rgba(255,255,255,0.06); padding: 0.1rem 0.35rem; border-radius: 3px; font-size: 0.74rem; }

  /* Fix cards */
  .fixes-list { display: flex; flex-direction: column; gap: 0.5rem; }
  .fix-card {
    background: var(--bg-surface); border: 1px solid var(--border);
    border-radius: 10px; overflow: hidden; transition: box-shadow 0.2s;
  }
  .fix-card:hover { box-shadow: 0 2px 8px rgba(0,0,0,0.15); }
  .fix-header {
    display: flex; align-items: center; gap: 0.65rem; padding: 0.75rem 1rem;
    cursor: pointer; user-select: none;
  }
  .fix-header:hover { background: var(--bg-row-hover); }
  .fix-num {
    font-size: 0.7rem; font-weight: 700; width: 24px; height: 24px;
    display: flex; align-items: center; justify-content: center;
    border-radius: 50%; background: var(--green-bg); color: var(--green);
    flex-shrink: 0;
  }
  .fix-card.code-fix .fix-num { background: var(--blue-bg); color: var(--blue); }
  .fix-title-text { font-size: 0.82rem; color: var(--text-bright); font-weight: 500; flex: 1; }
  .fix-tags { display: flex; gap: 0.35rem; align-items: center; }
  .fix-chevron { font-size: 0.7rem; color: var(--text-dim); transition: transform 0.2s; }
  .fix-card.open .fix-chevron { transform: rotate(180deg); }
  .fix-body { max-height: 0; overflow: hidden; transition: max-height 0.3s ease; }
  .fix-card.open .fix-body { max-height: 5000px; }
  .fix-content { padding: 0 1rem 1rem; }
  .fix-meta { display: flex; gap: 1.5rem; margin-bottom: 0.75rem; font-size: 0.76rem; }
  .fix-meta-item { color: var(--text-dim); }
  .fix-meta-item strong { color: var(--text); }
  .diff-block { margin-bottom: 0.65rem; }
  .diff-label { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--text-dim); margin-bottom: 0.25rem; font-weight: 600; }
  .diff-code {
    font-family: 'SF Mono', 'Fira Code', monospace; font-size: 0.76rem;
    padding: 0.65rem 0.85rem; border-radius: var(--r-sm); line-height: 1.55;
    overflow-x: auto; white-space: pre-wrap; word-break: break-word;
  }
  .diff-before { background: rgba(248,113,113,0.06); border: 1px solid rgba(248,113,113,0.15); color: var(--red); }
  .diff-after { background: rgba(74,222,128,0.06); border: 1px solid rgba(74,222,128,0.15); color: var(--green); }

  /* Chart */
  .chart-wrap {
    background: var(--bg-surface); border: 1px solid var(--border);
    border-radius: var(--r); padding: 1rem; height: 280px; margin-bottom: 1.75rem;
  }
</style>
</head>
<body>

<!-- ===== LIST VIEW ===== -->
<div id="list-view">
  <div class="topbar">
    <div class="topbar-left">
      <svg class="logo-art" viewBox="0 0 1250 180" xmlns="http://www.w3.org/2000/svg"><g font-family="'SFMono-Regular',Consolas,'Liberation Mono',Menlo,monospace" font-size="12" fill="#E4E8F0"><text x="0" y="16" xml:space="preserve">                                                           ███</text><text x="0" y="32" xml:space="preserve">                                                          ░░░</text><text x="0" y="48" xml:space="preserve"> ████████   ██████   ██████  █████ ████ ████████   █████  ████  █████ █████  ██████</text><text x="0" y="64" xml:space="preserve">░░███░░███ ███░░███ ███░░███░░███ ░███ ░░███░░███ ███░░  ░░███ ░░███ ░░███  ███░░███</text><text x="0" y="80" xml:space="preserve"> ░███ ░░░ ░███████ ░███ ░░░  ░███ ░███  ░███ ░░░ ░░█████  ░███  ░███  ░███ ░███████</text><text x="0" y="96" xml:space="preserve"> ░███     ░███░░░  ░███  ███ ░███ ░███  ░███      ░░░░███ ░███  ░░███ ███  ░███░░░</text><text x="0" y="112" xml:space="preserve"> █████    ░░██████ ░░██████  ░░████████ █████     ██████  █████  ░░█████   ░░██████</text><text x="0" y="128" xml:space="preserve">░░░░░      ░░░░░░   ░░░░░░    ░░░░░░░░ ░░░░░     ░░░░░░  ░░░░░    ░░░░░     ░░░░░░</text><text x="590" y="80" xml:space="preserve"> ░░░░░░</text><text x="590" y="96" xml:space="preserve"> ██████</text><text x="590" y="112" xml:space="preserve"> ░░░░░░</text><text x="640" y="16" xml:space="preserve">     ███</text><text x="640" y="32" xml:space="preserve">    ░░░</text><text x="640" y="48" xml:space="preserve">    ████  █████████████   ████████  ████████   ██████  █████ █████  ██████</text><text x="640" y="64" xml:space="preserve">   ░░███ ░░███░░███░░███ ░░███░░███░░███░░███ ███░░███░░███ ░░███  ███░░███</text><text x="640" y="80" xml:space="preserve">    ░███  ░███ ░███ ░███  ░███ ░███ ░███ ░░░ ░███ ░███ ░███  ░███ ░███████</text><text x="640" y="96" xml:space="preserve">    ░███  ░███ ░███ ░███  ░███ ░███ ░███     ░███ ░███ ░░███ ███  ░███░░░</text><text x="640" y="112" xml:space="preserve">    █████ █████░███ █████ ░███████  █████    ░░██████   ░░█████   ░░██████</text><text x="640" y="128" xml:space="preserve">   ░░░░░ ░░░░░ ░░░ ░░░░░  ░███░░░  ░░░░░      ░░░░░░     ░░░░░     ░░░░░░</text><text x="640" y="144" xml:space="preserve">                          ░███</text><text x="640" y="160" xml:space="preserve">                          █████</text><text x="640" y="176" xml:space="preserve">                         ░░░░░</text></g></svg>
    </div>
    <div class="topbar-right">
      <span class="filter-label">Branch</span>
      <select id="branch-filter" onchange="filterList()">
        <option value="">All</option>
      </select>
    </div>
  </div>
  <table class="runs-table">
    <thead>
      <tr>
        <th>Branch</th>
        <th>Score</th>
        <th>Top Issue</th>
        <th>Status</th>
        <th style="text-align:right"></th>
      </tr>
    </thead>
    <tbody id="runs-body"></tbody>
  </table>
</div>

<!-- ===== DETAIL VIEW ===== -->
<div id="detail-view">
  <div class="detail-topbar">
    <button class="back-btn" onclick="showList()">&larr; Back</button>
    <span class="detail-title" id="detail-title"></span>
    <div class="detail-meta" id="detail-meta"></div>
  </div>
  <div class="detail-body" id="detail-body"></div>
</div>

<script>
let cycles = [];

// ---- Helpers ----
function fmtPct(v) {
  if (v == null) return '-';
  const pct = v > 1.0 ? v : v * 100;
  return pct.toFixed(1) + '%';
}
function prettyName(s) { return s.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()); }
function escHtml(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
function timeAgo(ts) {
  if (!ts) return '';
  const diff = (Date.now() - new Date(ts).getTime()) / 1000;
  if (diff < 60) return Math.floor(diff) + 's';
  if (diff < 3600) return Math.floor(diff / 60) + 'm';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h';
  return Math.floor(diff / 86400) + 'd';
}

// ---- Load ----
async function loadAll() {
  const [cyclesRes, branchRes] = await Promise.all([
    fetch('/api/cycles').then(r => r.json()),
    fetch('/api/branches').then(r => r.json()),
  ]);
  cycles = cyclesRes.cycles || [];

  const sel = document.getElementById('branch-filter');
  const branches = branchRes.branches || [];
  sel.innerHTML = '<option value="">All</option>' + branches.map(b => `<option>${b}</option>`).join('');

  renderList(cycles);
}

// ---- LIST VIEW ----
function renderList(data) {
  const tbody = document.getElementById('runs-body');
  if (!data.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="empty-state">No improvement cycles yet.<br>Run <code>recursive-improve store-baseline</code> after computing baselines.</td></tr>';
    return;
  }

  tbody.innerHTML = data.map(c => {
    const score = c.score || {};
    const total = score.total || 0;

    // Score badge
    let scoreHtml = '';
    if (c.has_postfix) {
      const n = score.improved || 0;
      const pct = total > 0 ? n / total : 0;
      const cls = pct >= 0.6 ? 'score-good' : pct >= 0.3 ? 'score-warn' : 'score-bad';
      scoreHtml = `<span class="score ${cls}">${n}/${total} improved</span>`;
    } else {
      const n = score.healthy || 0;
      const pct = total > 0 ? n / total : 0;
      const cls = pct >= 0.6 ? 'score-good' : pct >= 0.3 ? 'score-warn' : 'score-bad';
      scoreHtml = `<span class="score ${cls}">${n}/${total} healthy</span>`;
    }

    // Top issue: worst unhealthy metric
    const worst = (c.comparisons || [])
      .filter(m => !m.healthy && m.baseline != null)
      .sort((a, b) => {
        const aV = (a.name.includes('success') || a.name.includes('recovery')) ? (1 - a.baseline) : a.baseline;
        const bV = (b.name.includes('success') || b.name.includes('recovery')) ? (1 - b.baseline) : b.baseline;
        return bV - aV;
      })[0];
    const topIssue = worst
      ? `<span class="score score-bad"><small style="opacity:0.7">${prettyName(worst.name).replace(' Rate', '')}</small> ${fmtPct(worst.baseline)}</span>`
      : '<span class="score score-good">all healthy</span>';

    const statusHtml = c.has_postfix
      ? '<span class="tag tag-green">compared</span>'
      : '<span class="tag tag-orange">awaiting re-run</span>';

    const br = c.branch || 'unknown';
    const shortBr = br.length > 40 ? br.slice(0, 40) + '\u2026' : br;

    return `<tr class="run-row" onclick="showDetail('${escHtml(c.branch)}')">
      <td>
        <div style="font-weight:500;color:var(--text-bright)">${shortBr}</div>
        ${c.label ? `<div style="font-size:0.72rem;color:var(--text-dim);margin-top:2px">${escHtml(c.label)}</div>` : ''}
      </td>
      <td>${scoreHtml}</td>
      <td>${topIssue}</td>
      <td>${statusHtml}</td>
      <td style="text-align:right"><span class="time-ago">${timeAgo(c.timestamp)}</span></td>
    </tr>`;
  }).join('');
}

function filterList() {
  const branch = document.getElementById('branch-filter').value;
  renderList(branch ? cycles.filter(c => c.branch === branch) : cycles);
}

// ---- DETAIL VIEW ----
async function showDetail(branch) {
  document.getElementById('list-view').style.display = 'none';
  document.getElementById('detail-view').style.display = 'block';

  const cycle = cycles.find(c => c.branch === branch);
  if (!cycle) return;

  // Load analysis + changes for this specific branch
  const [analysisRes, changesRes] = await Promise.all([
    fetch(`/api/analysis?branch=${encodeURIComponent(branch)}`).then(r => r.json()),
    fetch(`/api/changes?branch=${encodeURIComponent(branch)}`).then(r => r.json()),
  ]);

  // Header
  document.getElementById('detail-title').textContent = branch;
  const score = cycle.score || {};
  const total = score.total || 0;
  let scoreBadge;
  if (cycle.has_postfix) {
    const n = score.improved || 0;
    const pct = total > 0 ? n / total : 0;
    const cls = pct >= 0.6 ? 'tag-green' : pct >= 0.3 ? 'tag-orange' : 'tag-dim';
    scoreBadge = `<span class="tag ${cls}">${n}/${total} improved</span>`;
  } else {
    const n = score.healthy || 0;
    const pct = total > 0 ? n / total : 0;
    const cls = pct >= 0.6 ? 'tag-green' : pct >= 0.3 ? 'tag-orange' : 'tag-dim';
    scoreBadge = `<span class="tag ${cls}">${n}/${total} healthy</span>`;
  }
  document.getElementById('detail-meta').innerHTML =
    scoreBadge + `<span class="tag tag-dim">${timeAgo(cycle.timestamp)}</span>`;

  let html = '';

  // -- Score hero --
  if (cycle.has_postfix) {
    const n = score.improved || 0;
    const pct = total > 0 ? Math.round(n / total * 100) : 0;
    const col = pct >= 60 ? 'var(--green)' : pct >= 30 ? 'var(--orange)' : 'var(--red)';
    html += `<div style="background:var(--bg-surface);border:1px solid var(--border);border-radius:var(--r);padding:1.2rem;margin-bottom:1.75rem;text-align:center">
      <div style="font-size:2.5rem;font-weight:700;color:${col}">${n}/${total}</div>
      <div style="font-size:0.82rem;color:var(--text-dim);margin-top:0.3rem">metrics improved by &ge;50%</div>
    </div>`;
  } else {
    const n = score.healthy || 0;
    const pct = total > 0 ? Math.round(n / total * 100) : 0;
    const col = pct >= 60 ? 'var(--green)' : pct >= 30 ? 'var(--orange)' : 'var(--red)';
    html += `<div style="background:var(--bg-surface);border:1px solid var(--border);border-radius:var(--r);padding:1.2rem;margin-bottom:1.75rem;text-align:center">
      <div style="font-size:2.5rem;font-weight:700;color:${col}">${n}/${total}</div>
      <div style="font-size:0.82rem;color:var(--text-dim);margin-top:0.3rem">metrics in healthy range</div>
    </div>`;
  }

  // -- Metrics comparison table --
  const comparisons = cycle.comparisons || [];
  if (comparisons.length) {
    html += '<div class="section-label">Metric Comparison</div>';
    html += '<table class="improvement-table"><thead><tr>';
    html += '<th>Metric</th><th>Baseline</th>';
    if (cycle.has_postfix) html += '<th>Post-Fix</th><th>Change</th>';
    html += '<th>Status</th>';
    html += '</tr></thead><tbody>';

    for (const m of comparisons) {
      const bDisp = m.baseline != null ? fmtPct(m.baseline) : '-';
      const bFrac = m.baseline_frac ? ` <span style="color:var(--text-dim);font-size:0.72rem">(${m.baseline_frac})</span>` : '';

      html += '<tr>';
      html += `<td class="metric-name">${prettyName(m.name)}</td>`;
      html += `<td class="val-baseline">${bDisp}${bFrac}</td>`;

      if (cycle.has_postfix) {
        const pDisp = m.postfix != null ? fmtPct(m.postfix) : '-';
        const pFrac = m.postfix_frac ? ` <span style="color:var(--text-dim);font-size:0.72rem">(${m.postfix_frac})</span>` : '';
        html += `<td>${pDisp}${pFrac}</td>`;

        if (m.improvement_pct != null) {
          const isImp = m.status === 'improved' || m.status === 'slightly_improved';
          const isReg = m.status === 'regressed';
          const cls = isImp ? 'delta-improved' : isReg ? 'delta-regressed' : 'delta-unchanged';
          const sign = m.improvement_pct > 0 ? '+' : '';
          html += `<td><span class="delta-badge ${cls}">${sign}${m.improvement_pct}%</span></td>`;
        } else {
          html += '<td>-</td>';
        }
      }

      // Status
      let st = '';
      if (m.status === 'improved') st = '<span class="delta-badge delta-improved">improved</span>';
      else if (m.status === 'slightly_improved') st = '<span class="delta-badge delta-improved" style="opacity:0.7">slight &uarr;</span>';
      else if (m.status === 'regressed') st = '<span class="delta-badge delta-regressed">regressed</span>';
      else if (m.status === 'unchanged') st = '<span class="delta-badge delta-unchanged">unchanged</span>';
      else if (m.healthy) st = '<span class="delta-badge delta-improved">healthy</span>';
      else st = '<span class="delta-badge delta-regressed">concerning</span>';
      html += `<td>${st}</td></tr>`;
    }
    html += '</tbody></table>';
  }

  // -- Per-trace breakdown from baseline_metrics --
  const baselineRes = await fetch(`/api/baseline-metrics?branch=${encodeURIComponent(branch)}`).then(r => r.json());
  function normalizePerTrace(pt) {
    if (!pt) return {};
    if (Array.isArray(pt)) {
      const result = {};
      for (const entry of pt) {
        const name = entry.file || entry.name || 'unknown';
        const metrics = {};
        for (const [k, v] of Object.entries(entry)) {
          if (k === 'file' || k === 'name') continue;
          if (typeof v === 'object' && v !== null) {
            if ('numerator' in v && 'denominator' in v) metrics[k] = v;
            else if ('passed' in v || 'applicable' in v) {
              if (v.applicable !== false) metrics[k] = { numerator: v.passed ? 1 : 0, denominator: 1 };
            } else if ('value' in v) {
              metrics[k] = { numerator: v.value > 0 ? 1 : 0, denominator: 1 };
            }
          }
        }
        result[name] = metrics;
      }
      return result;
    }
    return pt;
  }
  const perTrace = normalizePerTrace(baselineRes.per_trace);
  if (Object.keys(perTrace).length) {
    html += '<div class="section-label">Per-Trace Breakdown</div>';
    html += '<div class="trace-grid">';
    for (const [traceName, traceMetrics] of Object.entries(perTrace)) {
      let tags = '';
      if (typeof traceMetrics === 'object' && !Array.isArray(traceMetrics)) {
        for (const [mn, mv] of Object.entries(traceMetrics)) {
          if (mn === 'token_usage' || mn === 'duration_outlier_rate') continue;
          if (typeof mv === 'object' && mv !== null && 'numerator' in mv && 'denominator' in mv) {
            const cls = mv.numerator > 0 ? 'fired' : 'clean';
            tags += `<span class="trace-tag ${cls}">${mn.replace(/_/g, ' ')} ${mv.numerator}/${mv.denominator}</span>`;
          }
        }
      }
      html += `<div class="trace-card">
        <div class="trace-card-name">${traceName}</div>
        <div class="trace-card-tags">${tags}</div>
      </div>`;
    }
    html += '</div>';
  }

  // -- Implemented fixes --
  const fixes = (changesRes || {}).fixes || [];
  if (fixes.length) {
    html += '<div class="section-label">Implemented Fixes</div>';
    html += '<div class="fixes-list">';
    fixes.forEach((f, i) => {
      const isCode = (f.type || '').toLowerCase().includes('code');
      const typeTag = isCode
        ? '<span class="tag tag-purple">code</span>'
        : '<span class="tag tag-green">prompt</span>';
      const verdictTag = (f.verdict || '').toLowerCase().includes('applied')
        ? '<span class="tag tag-green">applied</span>'
        : `<span class="tag tag-orange">${f.verdict || 'pending'}</span>`;

      let body = '<div class="fix-content"><div class="fix-meta">';
      if (f.files) body += `<span class="fix-meta-item"><strong>File:</strong> <code>${f.files}</code></span>`;
      if (f.metrics) body += `<span class="fix-meta-item"><strong>Target:</strong> ${f.metrics}</span>`;
      body += '</div>';
      if (f.before) body += `<div class="diff-block"><div class="diff-label">Before</div><div class="diff-code diff-before">${escHtml(f.before)}</div></div>`;
      if (f.after) body += `<div class="diff-block"><div class="diff-label">After</div><div class="diff-code diff-after">${escHtml(f.after)}</div></div>`;
      body += '</div>';

      html += `<div class="fix-card ${isCode ? 'code-fix' : ''}" onclick="this.classList.toggle('open')">
        <div class="fix-header">
          <span class="fix-num">${i + 1}</span>
          <span class="fix-title-text">${escHtml(f.title)}</span>
          <div class="fix-tags">${typeTag}${verdictTag}</div>
          <span class="fix-chevron">&#9660;</span>
        </div>
        <div class="fix-body">${body}</div>
      </div>`;
    });
    html += '</div>';
  }

  // -- Pipeline analysis --
  const stages = (analysisRes || {}).stages || {};
  const stageKeys = Object.keys(stages);
  if (stageKeys.length) {
    html += '<div class="section-label">Pipeline Analysis</div>';
    for (const key of stageKeys) {
      const stage = stages[key];
      const rendered = marked.parse(stage.content);
      html += `<div class="analysis-panel" onclick="this.classList.toggle('open')">
        <div class="panel-header">
          <h3>${stage.label}</h3>
          <span class="panel-chevron">&#9660;</span>
        </div>
        <div class="panel-body"><div class="md">${rendered}</div></div>
      </div>`;
    }
  }

  document.getElementById('detail-body').innerHTML = html;
}

function showList() {
  document.getElementById('detail-view').style.display = 'none';
  document.getElementById('list-view').style.display = 'block';
}

// ---- Init ----
marked.setOptions({ gfm: true, breaks: false });
loadAll();
setInterval(loadAll, 15000);
</script>
</body>
</html>
"""
