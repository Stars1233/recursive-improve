---
name: ratchet
description: >
  Autonomous ratchet loop for agent improvement. Configures optimization targets,
  then loops: improve agent → run agent → eval → keep or revert. Uses the
  /recursive-improve pipeline internally with auto-approval. Invoke with /ratchet
  or "run the ratchet loop", "improve my agent overnight", "autonomous improvement".
---

# /ratchet — Autonomous Improvement Loop

An autoresearch-style ratchet that continuously improves your agent. Each iteration:
improve → run agent → eval → keep (if better) or revert (if worse) → repeat.

---

## Step 1: Configure (MANDATORY — do not skip)

You MUST ask the user to confirm the configuration before starting the loop. Never proceed without explicit confirmation.

### If `program.md` exists

Read it. If any values look like placeholders (e.g., "Describe what you want to improve", "your_agent.py"), treat them as empty.

Present the current configuration to the user and ask them to confirm or modify:

> **Here's the current ratchet configuration:**
>
> 1. **Objective:** {objective from file}
> 2. **Agent run command:** `{command from file}`
> 3. **Traces directory:** {traces_dir from file}
> 4. **Metrics to optimize:**
>    {list each metric with direction and weight}
> 5. **Stopping conditions:** {max_iterations} iterations, {max_duration_hours}h, plateau after {plateau_patience}
>
> **Does this look right, or would you like to change anything?**

Wait for the user to confirm or provide modifications. Update `program.md` with any changes.

### If `program.md` does not exist

Ask the user each question:

1. **Objective** — what do you want to improve about your agent?
2. **Agent run command** — the shell command that runs your agent and generates traces (e.g., `uv run python examples/technova_agent.py`)
3. **Traces directory** — where traces are written (default: `eval/traces`)
4. **Metrics to optimize** — which metrics to track. For each:
   - Name (e.g., `clean_success_rate`, `error_rate`, or custom metrics from `compute_baselines.py`)
   - Direction: `minimize` or `maximize`
   - Weight: relative importance (default: 1.0)
5. **Stopping conditions** — when to stop:
   - Max iterations (default: 20)
   - Max duration in hours (default: 8)
   - Plateau patience: stop after N iterations without improvement (default: 3)

### Write program.md

After confirmation, write `program.md`:

```markdown
# Improvement Goals

## Objective
{objective}

## Agent Run Command
{command}

## Traces Directory
{traces_dir}

## Metrics
- {metric_name}: {minimize|maximize} (weight: {weight})
...

## Stopping Conditions
- max_iterations: {N}
- max_duration_hours: {N}
- plateau_patience: {N}

## Time Budget
- minutes_per_iteration: 15
```

**Do NOT proceed to Step 2 until the user has confirmed the configuration.**

---

## Step 2: Create ratchet branch

Run:
```bash
recursive-improve ratchet branch
```

This creates `ri/ratchet-<timestamp>` so all changes happen on a dedicated branch.

---

## Step 3: Establish baseline

Run the eval to get the starting score:
```bash
recursive-improve ratchet eval --config program.md
```

Parse the JSON output. Record the `score` as the baseline. Show the user the initial metrics.

---

## Step 4: Ratchet Loop

Repeat the following for each iteration until a stopping condition is met.

### 4a. Run the improvement pipeline

Execute the full `/recursive-improve` pipeline (stages 0–7) with these modifications:

- **Stage 6 (HITL gate): AUTO-APPROVE.** Do not ask the user. Choose option [A] (approve all). Write `eval/stage6_decision.md` with:
  ```
  Mode: ratchet (auto-approve)
  Decision: [A] Approve all
  ```
- **Stage 7 (Fix Implementation): NO IMPROVEMENT BRANCH.** Apply fixes directly to the working tree. Do NOT create a `ri/improve-*` branch — the ratchet branch already exists.
- **Skip conditions for subsequent iterations:**
  - If `eval/stage2_domain_context.md` exists → skip Stages 0, 1, and 2
  - Always re-run Stages 3–5 (they use fresh traces) and Stage 7

Before starting the pipeline, read `eval/ratchet_log.jsonl` (if it exists) for context on what was tried in previous iterations and whether it was kept or reverted. Avoid repeating approaches that were reverted.

**IMPORTANT:** Follow the full stage instructions from the `/recursive-improve` skill (SKILL.md). The stages are:
- Stage 0: Trace Analysis (6-phase: discover, criteria, survey, categorize, deep-dive, synthesize)
- Stage 1: Skill Management (quality gates, skillbook)
- Stage 2: Domain Context Gathering
- Stage 3: Metrics and Programmatic Analysis
- Stage 4: Rubric Definition
- Stage 5: Action Plan
- Stage 6: Auto-Approve (ratchet mode)
- Stage 7: Fix Implementation (directly to working tree)

### 4b. Run the agent

Execute the agent run command to generate fresh traces:

```bash
# Clear old traces first
rm -f {traces_dir}/*.json
# Run the agent
{agent_run_command from program.md}
```

If the command fails or produces no traces, log as "skip" and continue to the next iteration.

### 4c. Evaluate

```bash
recursive-improve ratchet eval --config program.md
```

Parse the JSON output. The `score` field is the composite score.

### 4d. Keep or revert

Compare the new score to the baseline:

- **If new_score > baseline_score:**
  ```bash
  recursive-improve ratchet commit {iteration} {new_score} --prev-score {baseline_score}
  ```
  Update baseline_score = new_score. Report: **KEEP** with the commit hash.

- **If new_score <= baseline_score:**
  ```bash
  recursive-improve ratchet revert
  ```
  Report: **REVERT**. Increment plateau counter.

### 4e. Log the iteration

```bash
recursive-improve ratchet log {iteration} {new_score} {keep|revert} \
  --baseline {baseline_score} \
  --duration {seconds} \
  --commit-hash {hash_or_none} \
  --traces-count {N} \
  --metrics '{json_metrics}'
```

### 4f. Check stopping conditions

```bash
recursive-improve ratchet status --config program.md
```

Parse the JSON output. Stop if:
- `iterations >= max_iterations`
- Total elapsed time exceeds `max_duration_hours`
- `plateau_count >= plateau_patience`

If stopping, proceed to Step 5. Otherwise, go to 4a.

---

## Step 5: Summary

Read and display `eval/ratchet_summary.md`.

Tell the user:
- **Branch:** `ri/ratchet-<timestamp>` has all kept improvements
- **Review:** `git diff main...ri/ratchet-<timestamp>`
- **Merge:** `git merge ri/ratchet-<timestamp>` or open a PR
- **Discard:** `git branch -D ri/ratchet-<timestamp>`
- **Dashboard:** `recursive-improve dashboard` to visualize the run

---

## Rules

- Do NOT ask for approval during the loop — this is autonomous
- Do NOT create improvement branches inside the loop — the ratchet branch already exists
- Do NOT modify trace files
- ALWAYS revert on regression — never keep a worse score
- ALWAYS log every iteration, including skips and reverts
- Keep fixes small and targeted — smaller changes are less likely to regress
- Read the ratchet log before each improvement step to avoid repeating failed approaches
