"""Built-in trace detectors for generic agent failure patterns."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class DetectorResult:
    name: str
    fired: bool = False
    numerator: int = 0
    denominator: int = 0
    value: float = 0.0
    details: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_tool_calls(trace: dict) -> list[dict]:
    calls = []
    for m in trace.get("messages", []):
        if m.get("role") == "assistant":
            for tc in m.get("tool_calls", []):
                calls.append(tc)
    return calls


def _get_tool_responses(trace: dict) -> list[dict]:
    return [m for m in trace.get("messages", []) if m.get("role") == "tool"]


def _get_assistant_messages(trace: dict) -> list[dict]:
    return [m for m in trace.get("messages", []) if m.get("role") == "assistant"]


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------

def detect_loops(trace: dict) -> DetectorResult:
    """Detect N+ consecutive calls to the same tool (stuck agent)."""
    calls = _get_tool_calls(trace)
    if not calls:
        return DetectorResult(name="loop_rate")

    names = [tc["function"]["name"] for tc in calls]
    max_run = 1
    current_run = 1
    for i in range(1, len(names)):
        if names[i] == names[i - 1]:
            current_run += 1
            max_run = max(max_run, current_run)
        else:
            current_run = 1

    fired = max_run >= 3
    return DetectorResult(
        name="loop_rate",
        fired=fired,
        numerator=1 if fired else 0,
        denominator=1,
        value=1.0 if fired else 0.0,
        details={"max_consecutive": max_run},
    )


_GIVE_UP_PATTERNS = [
    r"I'm unable to",
    r"I cannot (complete|fulfill|process|help with)",
    r"I'?m (sorry|afraid).{0,30}(can't|cannot|unable)",
    r"unfortunately.{0,30}(can't|cannot|unable)",
    r"not (able|possible) to",
    r"beyond my (ability|capabilities)",
]
_GIVE_UP_RE = re.compile("|".join(_GIVE_UP_PATTERNS), re.IGNORECASE)


def detect_give_up(trace: dict) -> DetectorResult:
    """Detect abandonment phrases in assistant messages."""
    assistant_msgs = _get_assistant_messages(trace)
    if not assistant_msgs:
        return DetectorResult(name="give_up_rate")

    hits = sum(1 for m in assistant_msgs if _GIVE_UP_RE.search(m.get("content", "")))
    return DetectorResult(
        name="give_up_rate",
        fired=hits > 0,
        numerator=hits,
        denominator=len(assistant_msgs),
        value=hits / len(assistant_msgs) if assistant_msgs else 0,
    )


_ERROR_PATTERNS = re.compile(
    r"(error|exception|traceback|failed|failure|timeout|refused|denied|"
    r"not found|unauthorized|forbidden|internal server error)",
    re.IGNORECASE,
)


def detect_errors(trace: dict) -> DetectorResult:
    """Detect error patterns in tool responses."""
    tool_msgs = _get_tool_responses(trace)
    if not tool_msgs:
        return DetectorResult(name="error_rate")

    hits = sum(1 for m in tool_msgs if _ERROR_PATTERNS.search(m.get("content", "")))
    return DetectorResult(
        name="error_rate",
        fired=hits > 0,
        numerator=hits,
        denominator=len(tool_msgs),
        value=hits / len(tool_msgs) if tool_msgs else 0,
    )


def detect_recovery(trace: dict) -> DetectorResult:
    """Detect error followed by successful retry on the same tool."""
    messages = trace.get("messages", [])
    error_tool_ids = set()
    recoveries = 0
    error_count = 0

    # Find tool responses that are errors
    for m in messages:
        if m.get("role") == "tool" and _ERROR_PATTERNS.search(m.get("content", "")):
            error_tool_ids.add(m.get("tool_call_id"))
            error_count += 1

    if not error_tool_ids:
        return DetectorResult(name="recovery_rate", denominator=0)

    # Check if a subsequent tool response for same tool type succeeded
    # Simplified: check if any tool response after an error is non-error
    saw_error = False
    for m in messages:
        if m.get("role") == "tool":
            is_error = _ERROR_PATTERNS.search(m.get("content", ""))
            if is_error:
                saw_error = True
            elif saw_error:
                recoveries += 1
                saw_error = False

    return DetectorResult(
        name="recovery_rate",
        fired=recoveries > 0,
        numerator=recoveries,
        denominator=error_count,
        value=recoveries / error_count if error_count else 0,
    )


def detect_clean_success(trace: dict, other_results: list[DetectorResult] | None = None) -> DetectorResult:
    """Trace is successful and no other detectors fired."""
    if not trace.get("success", False):
        return DetectorResult(name="clean_success_rate", denominator=1)

    if other_results and any(r.fired for r in other_results):
        return DetectorResult(name="clean_success_rate", denominator=1)

    return DetectorResult(
        name="clean_success_rate",
        fired=True,
        numerator=1,
        denominator=1,
        value=1.0,
    )


def detect_duration_outlier(trace: dict, threshold_s: float = 60.0) -> DetectorResult:
    """Flag traces exceeding a duration threshold."""
    duration = trace.get("duration_s", 0)
    fired = duration > threshold_s
    return DetectorResult(
        name="duration_outlier",
        fired=fired,
        numerator=1 if fired else 0,
        denominator=1,
        value=duration,
        details={"duration_s": duration, "threshold_s": threshold_s},
    )


def detect_token_usage(trace: dict) -> DetectorResult:
    """Sum token usage across assistant messages."""
    total = 0
    count = 0
    for m in _get_assistant_messages(trace):
        usage = m.get("usage")
        if usage:
            total += usage.get("total_tokens", 0)
            count += 1

    if count == 0:
        return DetectorResult(name="token_usage")

    return DetectorResult(
        name="token_usage",
        fired=True,
        numerator=total,
        denominator=count,
        value=total / count,
        details={"total_tokens": total, "message_count": count},
    )
