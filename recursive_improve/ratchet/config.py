"""Parse program.md into a RatchetConfig."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class MetricSpec:
    direction: str  # "minimize" or "maximize"
    weight: float = 1.0


@dataclass
class RatchetConfig:
    objective: str = ""
    agent_run_command: str = ""
    traces_dir: str = "eval/traces"
    metrics: dict[str, MetricSpec] = field(default_factory=dict)
    max_iterations: int = 20
    max_duration_hours: float = 8.0
    plateau_patience: int = 3
    time_budget_minutes: int = 15
    improve_command: str = (
        'claude -p "Run /recursive-improve in ratchet mode. '
        "Auto-approve all fixes (choose [A]). "
        "Apply changes directly to the working tree, do not create an improvement branch. "
        'Read eval/ratchet_log.jsonl for context on past iterations." '
        "--allowedTools Edit,Write,Bash,Read,Glob,Grep"
    )
    eval_dir: str = "eval"


_METRIC_RE = re.compile(
    r"^-\s+(\w+)\s*:\s*(minimize|maximize)"
    r"(?:\s*\(weight\s*:\s*([\d.]+)\))?\s*$",
    re.IGNORECASE,
)

_KV_RE = re.compile(r"^-\s+(\w+)\s*:\s*(.+)$")


def parse_program_md(path: str | Path) -> RatchetConfig:
    """Parse a program.md file into a RatchetConfig."""
    text = Path(path).read_text(encoding="utf-8")
    sections = _split_sections(text)
    cfg = RatchetConfig()

    if "Objective" in sections:
        cfg.objective = sections["Objective"].strip()

    if "Agent Run Command" in sections:
        # Extract the first non-empty line or code block
        cfg.agent_run_command = _extract_command(sections["Agent Run Command"])

    if "Traces Directory" in sections:
        val = sections["Traces Directory"].strip()
        if val:
            cfg.traces_dir = val

    if "Metrics" in sections:
        cfg.metrics = _parse_metrics(sections["Metrics"])

    if "Stopping Conditions" in sections:
        kvs = _parse_kv_list(sections["Stopping Conditions"])
        if "max_iterations" in kvs:
            cfg.max_iterations = int(kvs["max_iterations"])
        if "max_duration_hours" in kvs:
            cfg.max_duration_hours = float(kvs["max_duration_hours"])
        if "plateau_patience" in kvs:
            cfg.plateau_patience = int(kvs["plateau_patience"])

    if "Time Budget" in sections:
        kvs = _parse_kv_list(sections["Time Budget"])
        if "minutes_per_iteration" in kvs:
            cfg.time_budget_minutes = int(kvs["minutes_per_iteration"])

    if "Improve Command" in sections:
        cmd = _extract_command(sections["Improve Command"])
        if cmd:
            cfg.improve_command = cmd

    return cfg


def _split_sections(text: str) -> dict[str, str]:
    """Split markdown by ## headings into {heading: body}."""
    sections: dict[str, str] = {}
    current_heading = None
    lines: list[str] = []

    for line in text.splitlines():
        m = re.match(r"^##\s+(.+)$", line)
        if m:
            if current_heading is not None:
                sections[current_heading] = "\n".join(lines)
            current_heading = m.group(1).strip()
            lines = []
        else:
            lines.append(line)

    if current_heading is not None:
        sections[current_heading] = "\n".join(lines)

    return sections


def _extract_command(section: str) -> str:
    """Extract a command from a section — first code block or first non-empty line."""
    # Try code block first
    m = re.search(r"```\w*\n(.+?)\n```", section, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Fall back to first non-empty line
    for line in section.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return ""


def _parse_metrics(section: str) -> dict[str, MetricSpec]:
    metrics: dict[str, MetricSpec] = {}
    for line in section.splitlines():
        m = _METRIC_RE.match(line.strip())
        if m:
            name = m.group(1)
            direction = m.group(2).lower()
            weight = float(m.group(3)) if m.group(3) else 1.0
            metrics[name] = MetricSpec(direction=direction, weight=weight)
    return metrics


def _parse_kv_list(section: str) -> dict[str, str]:
    kvs: dict[str, str] = {}
    for line in section.splitlines():
        m = _KV_RE.match(line.strip())
        if m:
            kvs[m.group(1)] = m.group(2).strip()
    return kvs
