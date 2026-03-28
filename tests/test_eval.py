"""Tests for built-in detectors and eval runner."""

import json
from pathlib import Path

import pytest

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
from recursive_improve.eval.runner import run_eval, load_trace_files


# ---------------------------------------------------------------------------
# Fixture traces
# ---------------------------------------------------------------------------

def _make_trace(messages=None, success=True, duration_s=5.0):
    return {
        "session_id": "test",
        "timestamp": "2026-01-01T00:00:00Z",
        "duration_s": duration_s,
        "success": success,
        "error": None,
        "messages": messages or [],
    }


def _tool_call(name, call_id="c1"):
    return {"id": call_id, "function": {"name": name, "arguments": "{}"}}


LOOP_TRACE = _make_trace(messages=[
    {"role": "user", "content": "do something"},
    {"role": "assistant", "content": "", "tool_calls": [
        _tool_call("search", "c1"), _tool_call("search", "c2"), _tool_call("search", "c3"),
    ]},
    {"role": "tool", "tool_call_id": "c1", "content": "result1"},
    {"role": "tool", "tool_call_id": "c2", "content": "result2"},
    {"role": "tool", "tool_call_id": "c3", "content": "result3"},
])

GIVE_UP_TRACE = _make_trace(messages=[
    {"role": "user", "content": "book a flight"},
    {"role": "assistant", "content": "I'm unable to complete this request."},
    {"role": "assistant", "content": "Let me try another approach."},
])

ERROR_TRACE = _make_trace(
    messages=[
        {"role": "user", "content": "do it"},
        {"role": "assistant", "content": "", "tool_calls": [_tool_call("api_call", "c1")]},
        {"role": "tool", "tool_call_id": "c1", "content": "Error: connection refused"},
    ],
    success=False,
)

RECOVERY_TRACE = _make_trace(messages=[
    {"role": "user", "content": "try it"},
    {"role": "assistant", "content": "", "tool_calls": [_tool_call("api_call", "c1")]},
    {"role": "tool", "tool_call_id": "c1", "content": "Error: timeout"},
    {"role": "assistant", "content": "", "tool_calls": [_tool_call("api_call", "c2")]},
    {"role": "tool", "tool_call_id": "c2", "content": '{"status": "ok"}'},
])

CLEAN_TRACE = _make_trace(messages=[
    {"role": "user", "content": "hello"},
    {"role": "assistant", "content": "Hi! How can I help?"},
])

TOKEN_TRACE = _make_trace(messages=[
    {"role": "user", "content": "hi"},
    {"role": "assistant", "content": "hello", "usage": {
        "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
    }},
    {"role": "assistant", "content": "anything else?", "usage": {
        "prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30,
    }},
])


# ---------------------------------------------------------------------------
# Detector tests
# ---------------------------------------------------------------------------

class TestDetectLoops:
    def test_detects_loop(self):
        result = detect_loops(LOOP_TRACE)
        assert result.fired is True
        assert result.numerator >= 1
        assert result.name == "loop_rate"

    def test_no_loop_in_clean_trace(self):
        result = detect_loops(CLEAN_TRACE)
        assert result.fired is False

    def test_empty_trace(self):
        result = detect_loops(_make_trace())
        assert result.fired is False


class TestDetectGiveUp:
    def test_detects_give_up(self):
        result = detect_give_up(GIVE_UP_TRACE)
        assert result.fired is True
        assert result.numerator == 1
        assert result.denominator == 2

    def test_no_give_up(self):
        result = detect_give_up(CLEAN_TRACE)
        assert result.fired is False


class TestDetectErrors:
    def test_detects_errors(self):
        result = detect_errors(ERROR_TRACE)
        assert result.fired is True
        assert result.numerator >= 1

    def test_no_errors_in_clean(self):
        result = detect_errors(CLEAN_TRACE)
        assert result.fired is False


class TestDetectRecovery:
    def test_detects_recovery(self):
        result = detect_recovery(RECOVERY_TRACE)
        assert result.fired is True
        assert result.numerator >= 1
        assert result.denominator >= 1

    def test_no_recovery_without_errors(self):
        result = detect_recovery(CLEAN_TRACE)
        assert result.fired is False
        assert result.denominator == 0


class TestDetectCleanSuccess:
    def test_clean_trace(self):
        other_results = [
            DetectorResult(name="loop_rate", fired=False),
            DetectorResult(name="error_rate", fired=False),
        ]
        result = detect_clean_success(CLEAN_TRACE, other_results)
        assert result.fired is True
        assert result.value == 1.0

    def test_not_clean_when_others_fired(self):
        other_results = [
            DetectorResult(name="loop_rate", fired=True, numerator=1, denominator=1),
        ]
        result = detect_clean_success(CLEAN_TRACE, other_results)
        assert result.fired is False

    def test_not_clean_when_failed(self):
        trace = _make_trace(success=False)
        result = detect_clean_success(trace, [])
        assert result.fired is False


class TestDetectDurationOutlier:
    def test_detects_outlier(self):
        trace = _make_trace(duration_s=120.0)
        result = detect_duration_outlier(trace)
        assert result.fired is True

    def test_normal_duration(self):
        trace = _make_trace(duration_s=5.0)
        result = detect_duration_outlier(trace)
        assert result.fired is False

    def test_custom_threshold(self):
        trace = _make_trace(duration_s=10.0)
        result = detect_duration_outlier(trace, threshold_s=5.0)
        assert result.fired is True


class TestDetectTokenUsage:
    def test_counts_tokens(self):
        result = detect_token_usage(TOKEN_TRACE)
        assert result.fired is True
        assert result.numerator == 45  # 15 + 30
        assert result.denominator == 2
        assert result.value == 22.5

    def test_no_usage(self):
        result = detect_token_usage(CLEAN_TRACE)
        assert result.fired is False


# ---------------------------------------------------------------------------
# Runner tests
# ---------------------------------------------------------------------------

class TestRunEval:
    def test_run_eval_on_fixtures(self, tmp_path):
        traces_dir = tmp_path / "traces"
        traces_dir.mkdir()

        for i, trace in enumerate([CLEAN_TRACE, ERROR_TRACE, LOOP_TRACE]):
            t = dict(trace)
            t["session_id"] = f"trace_{i}"
            (traces_dir / f"trace_{i}.json").write_text(json.dumps(t))

        result = run_eval(traces_dir, branch="test")
        assert result["trace_count"] == 3
        assert result["branch"] == "test"
        assert "loop_rate" in result["metrics"]
        assert "error_rate" in result["metrics"]
        assert "clean_success_rate" in result["metrics"]

    def test_confidence_assignment(self, tmp_path):
        traces_dir = tmp_path / "traces"
        traces_dir.mkdir()

        # Create 5+ traces for full confidence
        for i in range(6):
            trace = _make_trace()
            trace["session_id"] = f"t{i}"
            (traces_dir / f"t{i}.json").write_text(json.dumps(trace))

        result = run_eval(traces_dir)
        # clean_success_rate has per-trace denominator of 1, so 6 traces -> denom 6 -> full
        assert result["metrics"]["clean_success_rate"]["confidence"] == "full"

    def test_infers_uniform_branch_commit_and_success(self, tmp_path):
        traces_dir = tmp_path / "traces"
        traces_dir.mkdir()

        for i in range(2):
            trace = _make_trace()
            trace["session_id"] = f"t{i}"
            trace["git_branch"] = "main"
            trace["git_commit"] = "abc123"
            (traces_dir / f"t{i}.json").write_text(json.dumps(trace))

        result = run_eval(traces_dir)
        assert result["branch"] == "main"
        assert result["commit_hash"] == "abc123"
        assert result["success"] is True

    def test_empty_traces_dir(self, tmp_path):
        traces_dir = tmp_path / "empty"
        traces_dir.mkdir()

        result = run_eval(traces_dir)
        assert result["trace_count"] == 0
        assert result["metrics"] == {}


class TestLoadTraceFiles:
    def test_loads_json_files(self, tmp_path):
        (tmp_path / "a.json").write_text('{"session_id": "a"}')
        (tmp_path / "b.json").write_text('{"session_id": "b"}')
        (tmp_path / "not_json.txt").write_text("hello")

        traces = load_trace_files(tmp_path)
        assert len(traces) == 2

    def test_skips_invalid_json(self, tmp_path):
        (tmp_path / "good.json").write_text('{"session_id": "ok"}')
        (tmp_path / "bad.json").write_text("not json {{{")

        traces = load_trace_files(tmp_path)
        assert len(traces) == 1
