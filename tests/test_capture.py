"""Tests for trace capture: patcher, session, normalize, TracedAgent."""

import json
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from recursive_improve.capture.patcher import _current_session, apply_patches, _patched
from recursive_improve.capture.session import Session, TracedAgentWrapper
from recursive_improve.capture.normalize import (
    normalize_openai,
    normalize_anthropic,
    normalize_litellm,
)
from recursive_improve.capture import git as git_mod


# ---------------------------------------------------------------------------
# Normalize tests
# ---------------------------------------------------------------------------

class TestNormalizeOpenai:
    def test_basic_response(self):
        kwargs = {"messages": [{"role": "user", "content": "hello"}], "model": "gpt-4o"}
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = "hi there"
        response.choices[0].message.tool_calls = None
        response.model = "gpt-4o"
        response.usage.prompt_tokens = 10
        response.usage.completion_tokens = 5
        response.usage.total_tokens = 15

        messages = normalize_openai(kwargs, response)
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "hello"
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"] == "hi there"
        assert messages[1]["provider"] == "openai"
        assert messages[1]["usage"]["total_tokens"] == 15

    def test_with_tool_calls(self):
        kwargs = {"messages": [{"role": "user", "content": "search"}], "model": "gpt-4o"}
        tc = MagicMock()
        tc.id = "call_1"
        tc.function.name = "search"
        tc.function.arguments = '{"q": "test"}'

        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = ""
        response.choices[0].message.tool_calls = [tc]
        response.model = "gpt-4o"
        response.usage = None

        messages = normalize_openai(kwargs, response)
        assert messages[-1]["tool_calls"][0]["function"]["name"] == "search"

    def test_preserves_tool_response_metadata(self):
        kwargs = {
            "messages": [
                {"role": "assistant", "content": "", "tool_calls": []},
                {"role": "tool", "tool_call_id": "call_1", "content": "tool output"},
            ],
            "model": "gpt-4o",
        }
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = "done"
        response.choices[0].message.tool_calls = None
        response.model = "gpt-4o"
        response.usage = None

        messages = normalize_openai(kwargs, response)
        tool_msg = next(msg for msg in messages if msg["role"] == "tool")
        assert tool_msg["tool_call_id"] == "call_1"
        assert tool_msg["content"] == "tool output"


class TestNormalizeAnthropic:
    def test_basic_response(self):
        kwargs = {"messages": [{"role": "user", "content": "hello"}], "model": "claude-3"}

        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "hi from claude"

        response = MagicMock()
        response.content = [text_block]
        response.model = "claude-3"
        response.usage.input_tokens = 10
        response.usage.output_tokens = 8

        messages = normalize_anthropic(kwargs, response)
        assert len(messages) == 2
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"] == "hi from claude"
        assert messages[1]["provider"] == "anthropic"

    def test_with_tool_use(self):
        kwargs = {"messages": [{"role": "user", "content": "do it"}], "model": "claude-3"}

        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.id = "tu_1"
        tool_block.name = "calculator"
        tool_block.input = '{"expr": "2+2"}'

        response = MagicMock()
        response.content = [tool_block]
        response.model = "claude-3"
        response.usage = None

        messages = normalize_anthropic(kwargs, response)
        assert messages[-1]["tool_calls"][0]["function"]["name"] == "calculator"

    def test_tool_result_blocks_become_tool_messages(self):
        kwargs = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "use the tool result"},
                        {"type": "tool_result", "tool_use_id": "tu_1", "content": "404 not found"},
                    ],
                }
            ],
            "model": "claude-3",
        }

        response = MagicMock()
        response.content = []
        response.model = "claude-3"
        response.usage = None

        messages = normalize_anthropic(kwargs, response)
        tool_msg = next(msg for msg in messages if msg["role"] == "tool")
        assert tool_msg["tool_call_id"] == "tu_1"
        assert "404" in tool_msg["content"]


class TestNormalizeLitellm:
    def test_overrides_provider(self):
        kwargs = {"messages": [{"role": "user", "content": "hi"}], "model": "gpt-4o"}
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = "hello"
        response.choices[0].message.tool_calls = None
        response.model = "gpt-4o"
        response.usage = None

        messages = normalize_litellm(kwargs, response)
        assert messages[-1]["provider"] == "litellm"


# ---------------------------------------------------------------------------
# Git helper tests
# ---------------------------------------------------------------------------

class TestGitHelpers:
    def test_get_git_branch(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="main\n")
            assert git_mod.get_git_branch() == "main"

    def test_get_git_branch_not_repo(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=128, stdout="")
            assert git_mod.get_git_branch() is None

    def test_get_git_commit(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="abc1234\n")
            assert git_mod.get_git_commit() == "abc1234"


# ---------------------------------------------------------------------------
# Patcher tests
# ---------------------------------------------------------------------------

class TestPatcher:
    def test_patch_idempotent(self):
        import recursive_improve.capture.patcher as patcher_mod
        original = patcher_mod._patched
        patcher_mod._patched = False
        # Patching with no LLM libraries installed should not error
        patcher_mod.apply_patches()
        assert patcher_mod._patched is True
        # Second call is a no-op
        patcher_mod.apply_patches()
        assert patcher_mod._patched is True
        patcher_mod._patched = original

    def test_apply_patches_still_attempts_openai_and_anthropic(self):
        import recursive_improve.capture.patcher as patcher_mod

        original = patcher_mod._patched
        patcher_mod._patched = False
        with patch.object(patcher_mod, "_try_patch_litellm", return_value=True) as mock_litellm, \
                patch.object(patcher_mod, "_try_patch_openai", return_value=True) as mock_openai, \
                patch.object(patcher_mod, "_try_patch_anthropic", return_value=True) as mock_anthropic:
            patcher_mod.apply_patches()

        assert mock_litellm.called
        assert mock_openai.called
        assert mock_anthropic.called
        patcher_mod._patched = original

    def test_contextvar_default_none(self):
        assert _current_session.get() is None


# ---------------------------------------------------------------------------
# Session tests
# ---------------------------------------------------------------------------

class TestSession:
    def test_writes_trace_file(self, tmp_path):
        traces_dir = tmp_path / "traces"
        with Session(traces_dir=traces_dir, session_id="test123") as s:
            s.add_message("user", "hello")
            s.add_message("assistant", "hi there")
            s.finish(output="done", success=True)

        trace_path = traces_dir / "test123.json"
        assert trace_path.exists()

        data = json.loads(trace_path.read_text())
        assert data["session_id"] == "test123"
        assert data["success"] is True
        assert data["output"] == "done"
        assert len(data["messages"]) == 2
        assert data["messages"][0]["role"] == "user"

    def test_error_inference(self, tmp_path):
        traces_dir = tmp_path / "traces"
        try:
            with Session(traces_dir=traces_dir, session_id="err1") as s:
                raise ValueError("test error")
        except ValueError:
            pass

        data = json.loads((traces_dir / "err1.json").read_text())
        assert data["success"] is False
        assert "ValueError" in data["error"]

    def test_success_inference(self, tmp_path):
        traces_dir = tmp_path / "traces"
        with Session(traces_dir=traces_dir, session_id="ok1") as s:
            pass

        data = json.loads((traces_dir / "ok1.json").read_text())
        assert data["success"] is True

    def test_session_auto_applies_patches(self, tmp_path):
        traces_dir = tmp_path / "traces"
        with patch("recursive_improve.capture.session.apply_patches") as mock_patch:
            with Session(traces_dir=traces_dir, session_id="autopatch"):
                pass
        mock_patch.assert_called_once()

    def test_trace_compatible_with_analyze_load(self, tmp_path):
        """Verify captured traces can be loaded by analyze.py's load_traces format."""
        traces_dir = tmp_path / "traces"
        with Session(traces_dir=traces_dir, session_id="compat1") as s:
            s.add_message("user", "test")
            s.finish(output="ok")

        # Simulate what load_traces does: glob *.json, parse, wrap
        trace_path = traces_dir / "compat1.json"
        raw = trace_path.read_text(encoding="utf-8")
        content = json.loads(raw)
        step = {"role": "conversation", "id": trace_path.name, "content": content}
        assert step["content"]["session_id"] == "compat1"
        assert isinstance(step["content"]["messages"], list)


# ---------------------------------------------------------------------------
# TracedAgent tests
# ---------------------------------------------------------------------------

class TestTracedAgent:
    def test_wraps_function(self, tmp_path):
        traces_dir = tmp_path / "traces"

        def my_agent(x):
            return x * 2

        agent = TracedAgentWrapper(my_agent, traces_dir=traces_dir, session_id="agent1")
        result = agent.run(21)
        assert result == 42

        data = json.loads((traces_dir / "agent1.json").read_text())
        assert data["success"] is True
        assert data["output"] == "42"

    def test_callable(self, tmp_path):
        traces_dir = tmp_path / "traces"

        def my_agent(x):
            return x + 1

        agent = TracedAgentWrapper(my_agent, traces_dir=traces_dir, session_id="agent2")
        result = agent(10)
        assert result == 11
