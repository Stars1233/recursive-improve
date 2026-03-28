"""Session context manager and TracedAgent wrapper for trace capture."""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from recursive_improve.capture.git import get_git_branch, get_git_commit
from recursive_improve.capture.normalize import (
    normalize_anthropic,
    normalize_litellm,
    normalize_openai,
)
from recursive_improve.capture.patcher import _current_session, apply_patches

_NORMALIZERS = {
    "openai": normalize_openai,
    "anthropic": normalize_anthropic,
    "litellm": normalize_litellm,
}


class Session:
    """Context manager that captures LLM calls into a trace file.

    Usage:
        with Session("./traces") as s:
            # LLM calls are auto-captured via patcher
            s.finish(output="done", success=True)
    """

    def __init__(self, traces_dir: str | Path = "./traces",
                 session_id: str | None = None,
                 metadata: dict | None = None):
        self.traces_dir = Path(traces_dir)
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self.metadata = metadata or {}
        self.messages: list[dict] = []
        self._start_time: float | None = None
        self._git_branch: str | None = None
        self._git_commit: str | None = None
        self._success: bool | None = None
        self._error: str | None = None
        self._output: str | None = None
        self._feedback: str | None = None
        self._token: object | None = None
        self._seen_input_hashes: set[int] = set()

    def __enter__(self):
        apply_patches()
        self._start_time = time.monotonic()
        self._git_branch = get_git_branch()
        self._git_commit = get_git_commit()
        self._token = _current_session.set(self)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        _current_session.reset(self._token)
        self._token = None

        if self._success is None:
            self._success = exc_type is None

        if exc_type is not None and self._error is None:
            self._error = f"{exc_type.__name__}: {exc_val}"

        duration = time.monotonic() - self._start_time if self._start_time else 0
        self._write_trace(duration)
        return False  # don't suppress exceptions

    def finish(self, output=None, success: bool = True, feedback: str | None = None):
        """Explicitly set session outcome."""
        self._success = success
        if output is not None:
            self._output = str(output)
        if feedback is not None:
            self._feedback = feedback

    def add_message(self, role: str, content: str, **kwargs):
        """Manually add a message to the trace."""
        msg = {
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **kwargs,
        }
        self.messages.append(msg)

    def _record_llm_call(self, provider: str, kwargs: dict, response):
        """Called by patcher when an LLM call completes."""
        normalizer = _NORMALIZERS.get(provider, normalize_openai)
        normalized = normalizer(kwargs, response)

        for msg in normalized:
            # Deduplicate all input messages (user, system, assistant, tool)
            # Only the new assistant response (with model field) is always kept
            if not msg.get("model"):
                h = hash((msg.get("role"), str(msg.get("content", ""))[:200],
                          msg.get("tool_call_id", "")))
                if h in self._seen_input_hashes:
                    continue
                self._seen_input_hashes.add(h)
            self.messages.append(msg)

    def _write_trace(self, duration: float):
        """Write the trace JSON file and register in run store."""
        self.traces_dir.mkdir(parents=True, exist_ok=True)

        trace = {
            "session_id": self.session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "duration_s": round(duration, 3),
            "success": self._success,
            "error": self._error,
            "output": self._output,
            "feedback": self._feedback,
            "git_branch": self._git_branch,
            "git_commit": self._git_commit,
            "metadata": self.metadata,
            "messages": self.messages,
        }

        trace_path = self.traces_dir / f"{self.session_id}.json"
        trace_path.write_text(json.dumps(trace, indent=2, default=str))

        # Best-effort insert into run store (only if benchmark_results.json already exists)
        try:
            from recursive_improve.store.json_store import JSONRunStore, _DEFAULT_STORE
            if _DEFAULT_STORE.exists():
                store = JSONRunStore()
                store.insert_run(
                    run_id=self.session_id,
                    branch=self._git_branch,
                    commit_hash=self._git_commit,
                    timestamp=trace["timestamp"],
                    traces_dir=str(self.traces_dir),
                    success=self._success,
                    duration=duration,
                    error=self._error,
                    output=self._output,
                    metadata=self.metadata,
                )
        except Exception:
            pass


class TracedAgentWrapper:
    """Wrap an agent function with automatic trace capture.

    Usage:
        agent = TracedAgentWrapper(my_fn, "./traces")
        result = agent.run("hello")
    """

    def __init__(self, fn, traces_dir: str | Path = "./traces", **session_kwargs):
        self.fn = fn
        self.traces_dir = traces_dir
        self.session_kwargs = session_kwargs

    def run(self, *args, **kwargs):
        with Session(traces_dir=self.traces_dir, **self.session_kwargs) as s:
            result = self.fn(*args, **kwargs)
            s.finish(output=result)
            return result

    def __call__(self, *args, **kwargs):
        return self.run(*args, **kwargs)
