"""CLI integration tests for output-dir and compare wiring."""

from __future__ import annotations

import json
from argparse import Namespace

from recursive_improve.cli import cmd_eval
from recursive_improve.store.json_store import JSONRunStore


def test_cmd_eval_writes_store_to_output_dir(tmp_path):
    traces_dir = tmp_path / "traces"
    traces_dir.mkdir()
    trace = {
        "session_id": "t1",
        "timestamp": "2026-01-01T00:00:00Z",
        "duration_s": 1.0,
        "success": True,
        "git_branch": "main",
        "git_commit": "abc123",
        "messages": [{"role": "assistant", "content": "ok"}],
    }
    (traces_dir / "t1.json").write_text(json.dumps(trace))

    output_dir = tmp_path / "custom-eval"
    cmd_eval(Namespace(traces_dir=str(traces_dir), branch=None, output_dir=str(output_dir)))

    store = JSONRunStore(store_path=output_dir / "benchmark_results.json")
    runs = store.get_all_runs(require_metrics=True)
    assert len(runs) == 1
    assert runs[0]["branch"] == "main"
    assert runs[0]["commit_hash"] == "abc123"
