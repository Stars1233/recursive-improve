---
name: recursive-improve
description: >
  End-to-end agent improvement pipeline. Analyzes raw execution traces, extracts
  insights, manages a skillbook, gathers domain context, defines metrics, builds a
  rubric, creates a prioritized action plan, presents it for review, and implements
  approved fixes. Trigger when the user says "improve my agent", "run the improvement
  pipeline", "apply insights", "/recursive-improve", or when eval/traces/ contains trace files.
---

# recursive-improve: Agent Improvement Pipeline

End-to-end pipeline: trace analysis → skill extraction → domain context → metrics → rubric → action plan → review → fixes.

## Prerequisites

Traces must exist in `eval/traces/`. If they don't:
- Ask the user for their traces directory
- Copy `.json`, `.md`, and `.toon` files into `eval/traces/`

**Skip condition:** If `eval/stage1_insights_summary.md` already exists (from a prior run or from `recursive-improve analyze`), skip Stages 0 and 1 — go directly to Stage 2.

---

## Stage 0: Trace Analysis

Analyze raw execution traces to extract learnings. This stage adapts ACE's recursive reflector methodology — a structured 6-phase strategy that moves from data discovery through verified deep-dives to synthesized, evidence-backed insights.

### Inputs

- `eval/traces/` — raw trace files (`.json`, `.md`, `.toon`)

### Phase 1: Discover

Map the data shape and inventory. Do NOT judge outcomes yet — just catalog what you have.

1. Read 2-3 trace files. Identify:
   - Top-level keys and message schema (3 levels deep)
   - Message format: `role`, `content`, `tool_calls`, `turn_idx`, etc.
   - Total trace count and per-trace message counts

2. Search for **agent operating rules, policy, or instructions** embedded in the traces — these are often in large strings (>500 chars). Check:
   - `role: "system"` messages
   - `info.environment_info.policy` or similar fields
   - Large embedded strings in any field

3. Build an inventory table:
   ```
   File                  Messages  Has system prompt?  Has tool calls?
   trace_001.json        42        yes                 yes
   trace_002.json        18        yes                 no
   ...
   ```

4. Record discovered rules/policy verbatim — understanding what the agent was *supposed* to do is essential for evaluating what it *actually* did.

### Phase 2: Derive Evaluation Criteria

Based on your discovery (schema, rules, patterns), define specific evaluation criteria to apply to every trace during the survey phase.

For each criterion, state:
- What to look for
- What a violation looks like

Example criteria (adapt to what you discovered):
- "Agent must verify customer identity before account changes" → violation: account change without prior verification tool call
- "Agent must not hallucinate policy details" → violation: agent states a policy that contradicts the embedded rules

### Phase 3: Survey

Read ALL traces (if ≤ 20) or a stratified sample (if > 20, target ~15 or 30%, whichever is larger — sample by outcome, length, and complexity).

For each trace, record:
1. What was requested
2. What the agent did (key decisions, tool calls, reasoning)
3. How it ended (success / failure / partial)
4. Evaluation criteria results (pass / fail / not applicable per criterion)

Process in batches of ~3 traces at a time to manage context.

### Phase 4: Categorize

Review all survey summaries. Group by task type and outcome.

Select **2-3 deep-dive targets**, prioritizing:
1. **Divergent outcomes** — same task type, one succeeded, one failed. What made the difference?
2. **Longest/most complex traces with mistakes** — most decision points, most learning potential
3. **Most common failure pattern** — highest impact to fix
4. **Confident-but-wrong** — traces where the agent's stated reasoning seems worth cross-checking against the data it received
5. **Rule/criteria violations** that appeared across traces (even successful ones)

Group targets by root cause — max 2 deep-dives per root cause. Prioritize **breadth over depth**.

Skip short, simple, routine traces — they rarely yield learnings.

### Phase 5: Deep-dive

For each deep-dive target, **re-read the FULL raw trace** — not your survey summary. Deep-dives that analyze summaries of summaries produce shallow, unverified conclusions.

**Two passes per target:**

**Pass 1 — Verification:** Separate what the agent *claimed* from what data it *received*. For each key claim or conclusion:
- What did the agent claim?
- What does the actual data/tool response show?
- Does it comply with the discovered rules?
- List any **incorrect claims**: what was claimed, what data shows, impact

This catches "confident but wrong" errors — where the agent proceeds without hesitation based on incorrect reasoning — that behavioral analysis alone misses.

**Pass 2 — Root cause analysis:** Given the verification findings and the full trace:
- What should the agent do differently?
- What is the root cause (not just the symptom)?
- Is this a missing instruction, a wrong instruction, a code limitation, or a reasoning failure?

For **divergent outcomes**: compare success and failure traces side by side. What specifically made the difference?

### Phase 6: Synthesize

Combine ALL survey summaries with ALL deep-dive results. Do not omit deep-dive findings — they contain your best evidence.

Produce a list of **atomic learnings**. For each:
- **Learning**: one specific, actionable insight (one concept only)
- **Atomicity score** (0.0–1.0): base 1.0, deduct 0.15 per "and/also/plus", 0.20 per vague term, 0.05 per word over 15
- **Evidence**: cite specific trace details (file name, message index, exact data)
- **Severity**: high (directly causes wrong outcomes), medium (degrades quality), low (minor inefficiency)
- **Category**: `code_fix` | `prompt_fix` | `process_fix`

**Verification findings are high-severity** — when the agent's reasoning contradicted the data it received, this directly causes wrong outcomes regardless of correct procedure.

### Output

Write to `eval/stage0_trace_analysis.md`:

```markdown
# Trace Analysis

## Discovery
### Trace Format
### Schema
### Agent Rules
### Inventory

## Evaluation Criteria
1. [criterion]: [violation description]
...

## Survey
### [trace_file.json]
- Requested: ...
- Agent did: ...
- Outcome: success/failure/partial
- Criteria: ...

## Categories
### Success patterns
### Failure patterns
### Partial completions

## Deep-dive Targets
### [Target 1: description]
#### Verification findings
#### Root cause analysis
### [Target 2: description]
...

## Extracted Learnings
| # | Learning | Atomicity | Evidence | Severity | Category |
|---|----------|-----------|----------|----------|----------|
| 1 | ...      | 0.92      | ...      | high     | prompt_fix |
| 2 | ...      | 0.87      | ...      | medium   | code_fix   |
```

---

## Stage 1: Skill Management

Transform raw learnings from Stage 0 into a structured skillbook with quality gates.

### Inputs

- `eval/stage0_trace_analysis.md` — extracted learnings from Stage 0
- `eval/skillbook.json` — existing skillbook from a prior improvement cycle (if it exists, load and update it; if not, start fresh)

### Step 1: Quality gate — Atomicity

For each learning from Stage 0, verify the atomicity score:

| Score | Level | Action |
|-------|-------|--------|
| 0.95–1.00 | Excellent | Accept as-is |
| 0.85–0.94 | Good | Accept, minor tightening optional |
| 0.70–0.84 | Fair | Split into multiple atomic learnings |
| 0.40–0.69 | Poor | Must split before proceeding |
| < 0.40 | Rejected | Discard — too vague or compound |

Splitting example:
- Compound: "Tool X worked in 4 steps with 95% accuracy" (0.55)
- Split into: "Use Tool X for task type Y" (0.95) + "Tool X completes in ~4 steps" (0.92) + "Expect 95% accuracy from Tool X" (0.90)

### Step 2: Format as imperative commands

Every skill must be an **imperative command**, not an observation.

- BAD: "The agent accurately answers factual questions" (observation)
- GOOD: "Answer factual questions directly and concisely" (imperative)
- BAD: "Missing verification step caused errors" (observation)
- GOOD: "Verify customer identity before making account changes" (imperative)

### Step 3: Deduplication

If `eval/skillbook.json` exists, load it. For each new learning, check whether any existing skill has >70% semantic overlap.

Semantic duplicates (use UPDATE, not ADD):

| Existing skill | Duplicate (don't add) |
|---------------|----------------------|
| "Answer directly" | "Use direct answers" |
| "Break into steps" | "Decompose into parts" |
| "Verify calculations" | "Double-check results" |

### Step 4: Determine operations

For each learning, select the operation:

| Situation | Operation |
|-----------|-----------|
| New error pattern or missing capability | **ADD** new skill |
| Existing skill needs refinement | **UPDATE** with improved content |
| Existing skill contributed to success in traces | **TAG** as helpful |
| Existing skill caused or contributed to error | **TAG** as harmful |
| Strategies contradict each other | **REMOVE** one or **UPDATE** to resolve |
| Skill tagged harmful 3+ times | **REMOVE** |
| No actionable insight | **SKIP** |

Default to UPDATE over ADD when a similar skill exists.

### Step 5: Rejection filter

Reject any skill that contains:
- **Meta-commentary** (not actionable): "be careful", "consider", "think about", "remember", "make sure"
- **Observations** (not commands): "the agent", "the model" — write commands to follow, not descriptions of behavior
- **Vague terms**: "appropriate", "proper", "various" — too vague to act on
- **Overgeneralizations**: "always", "never" without specific context

### Step 6: Skillbook size management

If the skillbook exceeds 50 skills:
- Prioritize UPDATE over ADD
- Merge skills with >70% overlap
- Remove lowest-performing skills (most harmful tags, least helpful tags)

### Outputs

**`eval/skillbook.json`:**

```json
{
  "skills": {
    "section-00001": {
      "id": "section-00001",
      "section": "error_handling",
      "content": "Verify customer identity before making account changes",
      "evidence": "In trace_003.json, agent changed account without verification (msg 12)",
      "justification": "Prevents unauthorized account modifications",
      "helpful": 0,
      "harmful": 0,
      "status": "active"
    }
  },
  "sections": {
    "error_handling": ["section-00001"]
  },
  "next_id": 2
}
```

**`eval/stage1_insights_summary.md`:**

```markdown
# Insights Summary

Generated by: recursive-improve (Stage 1)
Total insights: N

---

## Insight: {skill_id} — {section}

**Status:** active
**Helpful/Harmful:** 0/0

**Content:**
{imperative skill text}

**Evidence:**
{specific trace evidence}

**Justification:**
{why this improves the agent}

---
```

Write both files, then proceed to Stage 2.

---

## Stage 2: Domain Context Gathering

Understand the agent's world — what it does, what tools it has, and what "success" looks like.

### 0. Detect trace format

Read 1 trace file from `eval/traces/` and identify the framework:

| Signal | Framework |
|--------|-----------|
| `info.agent_info.implementation`, `simulation.messages[]` with `role`/`tool_calls`/`turn_idx` | **tau2-bench** |
| `runs[].steps[]` with `type: "tool"`, `lc_kwargs` | **LangChain / LangSmith** |
| `events[]` with `event_type`, `span_id`, `parent_id` | **LlamaIndex** |
| `choices[].message.tool_calls[]` at top level | **Raw OpenAI API logs** |
| `trace.spans[]` with `attributes`, `trace_id` | **OpenTelemetry / Arize / Langfuse** |

Record the detected format. If unrecognized, note top-level keys and proceed best-effort.

### 1. Detect architecture

Read 2-3 traces. Determine single-agent vs multi-agent:

- **Single agent**: one conversation thread, tool calls from one identity
- **Multi-agent**: multiple `agent_info` entries, routing tool calls (`transfer_to_*`, `delegate_to_*`), distinct system prompts per agent

If multi-agent: document each agent separately and note routing logic.

### 2. Find the system prompt

Fallback chain — stop at first hit:

1. **Config files** — grep for: `system_prompt`, `system_message`, `instructions`, `AGENT_INSTRUCTION`, `SYSTEM_PROMPT`
2. **Source code** — search for prompt template strings, f-strings building system messages
3. **Trace extraction** — check `info.environment_info.policy`, first `role: "system"` message, `raw_data` fields
4. **Not found** — record `SYSTEM_PROMPT_STATUS: NOT_FOUND`

Record both content and source location.

### 3. Extract tool definitions

**Pass 1 — Source code:** Search for `@tool`, `@is_tool`, function schema arrays, `tools=[]`. For each: name, params, return type, side effects (READ/WRITE/GENERIC), unvalidated rules.

**Pass 2 — Traces:** Read ALL traces (if ≤ 20) or stratified sample. Extract every unique `tool_calls[].name` and `role: "tool"` response. Record one example input/output per tool.

**Reconcile:** Tools in source but not traces = "available but unused". Tools in traces but not source = investigate.

### 4. Find domain documentation

READMEs, policy files, inline comments, test files describing expected behavior.

### 5. Catalogue behavior patterns

**Trace selection — stratified sampling** (if > 20 traces):
- 2+ per unique `termination_reason`
- Shortest, longest, 2 median by message count
- Lowest and highest by tool call count
- 3+ of each pass/fail outcome
- Target: ~15 traces or 30%, whichever is larger

For each trace, document: function call frequency, tool call sequences, success patterns, failure patterns, error patterns, policy violations, user feedback signals.

### 6. Write findings

Write to `eval/stage2_domain_context.md`:

```markdown
# Domain Context

## Trace Format
## Architecture
## Agent Purpose
## System Prompt
## Tools
## Domain Rules
## Behavior Patterns
### Success patterns
### Failure patterns
### Policy violation patterns
### Error patterns
### User feedback signals
```

---

## Stage 3: Metrics and Programmatic Analysis

Define metrics from insights, implement as code, run, review, iterate.

### Step 0: Run built-in eval first

Before writing custom detectors, run the built-in eval:
```bash
recursive-improve eval eval/traces --branch main
```
Review the generic metrics (loops, give-ups, errors, recovery, clean success).
Only write custom detectors in `eval/compute_baselines.py` for domain-specific
metrics the built-in detectors cannot capture.

### Inputs

- `eval/stage1_insights_summary.md`
- `eval/stage2_domain_context.md`

Read both before starting.

### Process

This stage is iterative with a **3 iteration cap**. A metric set is "clean" when:
1. No small-sample metrics in priority set (denominator ≥ 5 for priority ranking)
2. No unexplained 0%/100% extremes
3. No redundant pairs (>70% denominator overlap)
4. Script runs without errors

### Step 1: Define metrics

For each insight, identify observable trace signals. Classify by detector pattern:

- **Recovery detectors** — consecutive calls: first error, next success
- **Loop detectors** — N+ consecutive calls to same function (stuck agent)
- **Give-up detectors** — regex for abandonment phrases ("I'm unable to", "cannot complete")
- **Error classifiers** — match outputs against domain-specific error patterns
- **Over-exploration detectors** — ratio of explore vs action calls exceeding threshold
- **Ground-truth comparison** — agent claims value vs preceding tool response (regex extraction: dollar amounts, IDs, flight numbers, compare against JSON fields)
- **Ordering/sequencing detectors** — tool call A before B when B should come first
- **Clean success** — threads with no errors and no other tags

Validate each detector against 2-3 traces where you know ground truth before coding at scale.

### Step 2: Implement and run

Write `eval/compute_baselines.py` with:
- CLI args: `--traces-dir` (required), `--output` (default: `eval/baseline_metrics.json`)
- `load_traces(traces_dir)` — loads all JSON trace files
- `tag_thread(thread)` — combines all detectors
- One measurement function per metric (numerator / denominator)
- `compute_all_baselines(traces_dir)` — runs all, returns dict

Run it:
```bash
python eval/compute_baselines.py --traces-dir eval/traces --output eval/baseline_metrics.json
```

Then store the baseline as a benchmark run (for the dashboard):
```bash
recursive-improve store-baseline
```

### Step 3: Review and iterate

**Check A — Script health.** Errors or null values? Fix, re-run (doesn't count toward cap).

**Check B — Small-sample guard.** Denominator ≥ 5 → full confidence. 1-4 → `"directional-only"`. 0 → broken or genuinely absent.

**Check C — Extreme-value triage.** 0% or 100%: plausible non-extreme case exists → detector broken. Otherwise → write justification. Add `"at_ceiling": true` or `"at_floor": true` for already-optimal metrics.

**Check D — Overlap audit.** For every pair: `|denom_A ∩ denom_B| / min(|denom_A|, |denom_B|)`. If > 0.70, keep the sharper one, merge or drop the other.

**Check E — Coverage.** Every Stage 1 insight needs a metric. Try hard before classifying as unmeasurable. Only valid unmeasurable reasons: `qualitative-only`, `insufficient-data`, `needs-ground-truth`.

### Design principles

- One metric per insight. Fewer metrics than insights = too conservative.
- Express as ratio or percentage. No absolute counts.
- Prefer per-event denominators over per-thread.
- Build a metric for EVERY insight. "Unmeasurable" is a last resort.

### Outputs

- `eval/compute_baselines.py`
- `eval/baseline_metrics.json`
- `eval/benchmark_results.json` (stored via `recursive-improve store-baseline`)

---

## Stage 4: Rubric Definition

Organize metrics into a tiered evaluation rubric.

### Inputs

- `eval/baseline_metrics.json`
- `eval/compute_baselines.py`
- `eval/stage1_insights_summary.md`
- `eval/stage2_domain_context.md`

### Process

#### 1. Quantitative redundancy check

For every metric pair, check:
- Denominator overlap > 70%
- Same skill set
- Logical subsumption

For each candidate pair: explicit decision (keep both / merge / drop) with reasoning.

Target: 5-7 metrics after redundancy resolution.

#### 2. Tier each metric

```
Q1: Can a SINGLE skill/instruction change directly move this? → LEADING
Q2: Does it require MULTIPLE skills adopted together? → LAGGING
Q3: Does it require domain reasoning beyond instructions? → QUALITY
```

If ambiguous, pick the lower tier. Record which question determined the tier.

#### 3. Flag low-confidence baselines

Denominator < 5 → marked with `**Confidence: low** (n=X)`, excluded from priority sorting.

#### 4. Set direction

Up or down. **Ceiling guard:** 100% baseline → `"↑ maintain"`, never bare `"↑"`. Same for 0% floor.

#### 5. Map insights to metrics

Every insight → Mapped / Indirectly mapped / Qualitative-only. Report coverage counts.

#### 6. Invalidation notes

Per metric: "What would make this tier assignment wrong?"

#### 7. Write the rubric

Write to `eval/baseline_metrics.md` with summary table, tier definitions, metric details, redundancy analysis, insight coverage.

---

## Stage 5: Action Plan

Triage each insight into discard/code-fix/prompt-fix and produce a prioritized plan.

### Inputs

- `eval/stage1_insights_summary.md`
- `eval/stage2_domain_context.md`
- `eval/baseline_metrics.md`
- `eval/baseline_metrics.json`
- `eval/compute_baselines.py`

### Process

#### 1. Triage each insight

**1a. Validity check** — real recurring problem or one-off noise?

**1b. "Already handled" verification** — grep codebase for key terms, read existing system prompt. Partially covered → keep as strengthening fix. Fully covered AND baseline ≥ 95% → discard.

**1c. Code-vs-prompt decision:**

For each insight, judge the best fix type on its own merits:

| Signal | Fix type |
|--------|----------|
| Agent has the information but reasons incorrectly about it | **PROMPT FIX** — clarify reasoning guidance |
| Agent lacks instructions for a scenario it hasn't seen | **PROMPT FIX** — add instructions for the new case |
| Agent has the instruction but ignores or violates it | **CODE FIX** — enforce via validation, guardrails, or forced sequencing |
| Agent lacks a tool, schema, validation, or infrastructure capability | **CODE FIX** — add or modify code |
| Agent has partial info and a prompt workaround exists, but a code fix would be more robust | **CODE FIX** — prefer the durable solution |
| Fix involves both new instructions and supporting code | **BOTH** — implement both, note the dependency |

Choose the approach that most directly and durably solves the root cause. There is no default — evaluate each case independently.

#### 2. Consolidate related insights

Merge when ALL hold: same target behavior, overlapping fix text (>50%), fixing one fixes >80% of the other.

#### 3. Write specific recommendations

- Discards: one sentence why.
- Code fixes: file, function, specific change.
- Prompt fixes: exact instruction text, where it goes, why this wording.

#### 4. Assess risk per fix

| Risk | Definition |
|------|-----------|
| None | Additive, no existing behavior affected |
| Low | Targets currently-failing behavior |
| Medium | Modifies behavior where some cases already work |
| High | Rewrites behavior that mostly works |

Medium/High: add one-sentence mitigation.

#### 5. Handle qualitative-only insights

Still produce fixes. Use confidence = 0.5 and estimate impact from severity. Note that improvement should be verified via manual trace review.

#### 6. Link to metrics

Each fix → which metric(s) would move.

#### 7. Prioritize

```
Priority Score = Impact × Confidence × Tier Bonus ÷ Risk Factor
```

- Impact = estimated gap closure
- Confidence: n≥20 → 1.0, 10-19 → 0.8, 5-9 → 0.6, <5 → 0.3
- Tier Bonus: leading → 1.5x, lagging/quality → 1.0x
- Risk Factor: None/Low → 1.0, Medium → 1.5, High → 2.0

After scoring, promote prerequisites even if their standalone score is lower.

### Output

Write to `eval/action_plan.md` with summary, implementation priority table, per-fix entries, consolidated prompt skills, monitor items.

---

## Stage 6: Human-In-The-Loop Gate

Present the action plan for informed approval.

### Inputs

- `eval/action_plan.md`
- `eval/baseline_metrics.md`
- `eval/baseline_metrics.json`
- `eval/stage1_insights_summary.md`

### Process

#### 1. Executive summary

Counts: total insights, distinct after dedup, prompt/code/discards, discard reasons.

#### 2. Top 3 highest-impact changes

For each: before/after behavior from actual traces, target metric delta, risk rating.

#### 3. Full prioritized fix list

Table: priority, name, type, target metrics, risk, effort (Low/Medium/High).

#### 4. "What we are NOT fixing and why"

Every discard with: ID, name, reason, what would change your mind.

#### 5. Flag small-sample items

Call out metrics with denominator < 5.

#### 6. Traceability chain

Per fix: insight → metric → fix → expected improvement.

#### 7. Collect decision

Explain the branch workflow: approved fixes will be applied on a **dedicated branch** (`ri/improve-<YYYYMMDD-HHMMSS>`), not directly on the current branch. This means:
- The user's current branch stays untouched
- All changes can be reviewed with `git diff main...ri/improve-<timestamp>`
- The user can open a PR, get CI feedback, and merge when ready
- Easy to discard with `git branch -D` if the fixes aren't right

Three options:
- **[A] Approve all** — create branch, implement all fixes
- **[B] Approve with modifications** — walk through each fix individually (approve/skip/modify), then create branch and implement
- **[C] Reject** — collect feedback, re-run Stage 5

If [B]: update `eval/action_plan.md` with modifications. Write `eval/stage6_decision.md`.

### Rules

- Do NOT auto-approve
- Do NOT proceed to fixes until clear approval is recorded
- Always present small-sample warnings
- Always present the "not fixing" section
- Always explain the branch workflow before collecting the decision

### Output

- `eval/stage6_decision.md`
- `eval/action_plan.md` (updated if modifications)

---

## Stage 7: Fix Implementation

Implement every approved fix from the action plan.

### Inputs

- `eval/action_plan.md`
- `eval/stage6_decision.md` (if exists)
- `eval/baseline_metrics.json`

### Pre-flight: Create Improvement Branch

All fixes are applied on a dedicated branch, keeping the user's current branch clean.

1. `git status` — verify clean working tree. If there are uncommitted changes, ask the user to commit or stash them first before proceeding.
2. Record the current branch name (e.g. `main`) as the base branch.
3. Create and switch to the improvement branch:
   ```bash
   git checkout -b ri/improve-$(date +%Y%m%d-%H%M%S)
   ```
4. Record the branch name in `eval/changes_log.md`.

### Pre-flight: HITL Modification Check

If `eval/stage6_decision.md` exists, identify user-modified items. Tag them `[HITL-MODIFIED]` in the changes log.

### Pre-flight: Conflict Scan

Build `file_path → [fix IDs]` map. Flag co-located and overlapping fixes. Plan sequential application in priority order.

### Process

For each non-discarded fix in priority order:

1. **Understand** — read the recommendation and referenced files
2. **Implement** — minimal, targeted change. Code fixes: find file, make the change. Prompt fixes: find system prompt, add instruction. No refactoring beyond what's needed.
3. **Log** — append to `eval/changes_log.md`: type, verdict, files modified, before/after snippets, linked metrics, conflict notes
4. **Handle uncertainty** — if unsure, log as `NEEDS REVIEW` with what's unclear and continue

### Post-Fix: Next Steps

After all fixes are applied on the improvement branch:

1. Ensure `eval/benchmark_results.json` is present (it was created by `store-baseline` in Stage 3). If missing, run: `recursive-improve store-baseline`
2. Commit all changes on the improvement branch with a descriptive message. Include `eval/benchmark_results.json` in the commit.
3. Stay on the improvement branch (do NOT switch back to the base branch).

### Rules

- Do NOT modify trace files
- Do NOT make changes beyond what was recommended
- Do NOT run compute_baselines.py (baselines reflect old traces)
- Do NOT apply fixes directly on the user's current branch — always use the improvement branch
- Make minimal, targeted changes

### Output

- `eval/changes_log.md`
- The improvement branch with all code/prompt changes
