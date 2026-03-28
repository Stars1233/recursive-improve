"""JSONRunStore: Git-committable JSON file storage for eval runs and metrics."""

from __future__ import annotations

import json
import threading
from pathlib import Path

_DEFAULT_STORE = Path("eval/benchmark_results.json")


def _empty_store() -> dict:
    return {"version": 1, "runs": []}


class JSONRunStore:
    """Drop-in replacement for RunStore backed by a single JSON file.

    All metric values are normalized to 0-1 on ingest.
    """

    def __init__(self, store_path: str | Path | None = None):
        self.store_path = Path(store_path) if store_path else _DEFAULT_STORE
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # Auto-migrate from SQLite if runs.db exists but JSON doesn't
        if not self.store_path.exists():
            db_path = self.store_path.parent / "runs.db"
            if db_path.exists():
                self._migrate_from_sqlite(db_path)

    def _load(self) -> dict:
        if not self.store_path.exists():
            return _empty_store()
        try:
            data = json.loads(self.store_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or "runs" not in data:
                return _empty_store()
            return data
        except (json.JSONDecodeError, OSError):
            return _empty_store()

    def _save(self, data: dict) -> None:
        self.store_path.write_text(
            json.dumps(data, indent=2, default=str) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _normalize_value(name: str, value) -> float:
        """Normalize metric values to 0-1 range."""
        if value is None:
            return 0.0
        v = float(value)
        # If it looks like a percentage (>1) and is a rate metric, divide by 100
        if v > 1.0 and ("rate" in name or "ratio" in name or "pct" in name
                        or "percent" in name or "success" in name
                        or "recovery" in name or "violation" in name):
            v = v / 100.0
        return v

    def insert_run(self, run_id: str, *, branch: str | None = None,
                   commit_hash: str | None = None, timestamp: str,
                   traces_dir: str | None = None, success: bool | None = None,
                   duration: float | None = None, error: str | None = None,
                   output: str | None = None, config: dict | None = None,
                   metadata: dict | None = None):
        with self._lock:
            data = self._load()
            # Upsert: remove existing run with same id
            data["runs"] = [r for r in data["runs"] if r["id"] != run_id]
            data["runs"].append({
                "id": run_id,
                "branch": branch,
                "commit_hash": commit_hash,
                "timestamp": timestamp,
                "traces_dir": traces_dir,
                "success": int(success) if success is not None else None,
                "duration": duration,
                "error": error,
                "output": output,
                "config": config,
                "metadata": json.dumps(metadata) if metadata else None,
                "metrics": {},
            })
            self._save(data)

    def insert_metrics(self, run_id: str, metrics_dict: dict):
        """Bulk insert metrics. Normalizes values to 0-1 range.

        metrics_dict: {"metric_name": {"numerator": N, "denominator": N,
                        "value": float, "confidence": str, ...}, ...}
        """
        with self._lock:
            data = self._load()
            for run in data["runs"]:
                if run["id"] == run_id:
                    if "metrics" not in run:
                        run["metrics"] = {}
                    for name, m in metrics_dict.items():
                        run["metrics"][name] = {
                            "numerator": m.get("numerator"),
                            "denominator": m.get("denominator"),
                            "value": self._normalize_value(name, m.get("value")),
                            "confidence": m.get("confidence"),
                            "details": m if m else None,
                        }
                    break
            self._save(data)

    def run_has_metrics(self, run_id: str) -> bool:
        data = self._load()
        for run in data["runs"]:
            if run["id"] == run_id:
                return bool(run.get("metrics"))
        return False

    def get_run(self, run_id: str) -> dict | None:
        data = self._load()
        for run in data["runs"]:
            if run["id"] == run_id:
                # Return without the embedded metrics (matches SQLite API)
                result = {k: v for k, v in run.items() if k != "metrics"}
                return result
        return None

    def get_runs_by_branch(self, branch: str, require_metrics: bool = False) -> list[dict]:
        data = self._load()
        runs = [r for r in data["runs"] if r.get("branch") == branch]
        if require_metrics:
            runs = [r for r in runs if r.get("metrics")]
        runs.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
        return [{k: v for k, v in r.items() if k != "metrics"} for r in runs]

    def get_latest_run(self, branch: str | None = None,
                       require_metrics: bool = False) -> dict | None:
        data = self._load()
        runs = data["runs"]
        if branch:
            runs = [r for r in runs if r.get("branch") == branch]
        if require_metrics:
            runs = [r for r in runs if r.get("metrics")]
        if not runs:
            return None
        runs.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
        run = runs[0]
        return {k: v for k, v in run.items() if k != "metrics"}

    def get_metrics(self, run_id: str) -> list[dict]:
        """Return metrics as list-of-dicts matching SQLite format."""
        data = self._load()
        for run in data["runs"]:
            if run["id"] == run_id:
                metrics = run.get("metrics", {})
                return [
                    {
                        "run_id": run_id,
                        "metric_name": name,
                        "numerator": m.get("numerator"),
                        "denominator": m.get("denominator"),
                        "value": m.get("value"),
                        "confidence": m.get("confidence"),
                        "details": json.dumps(m.get("details")) if m.get("details") else None,
                    }
                    for name, m in metrics.items()
                ]
        return []

    def get_all_runs(self, require_metrics: bool = False) -> list[dict]:
        data = self._load()
        runs = data["runs"]
        if require_metrics:
            runs = [r for r in runs if r.get("metrics")]
        runs.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
        return [{k: v for k, v in r.items() if k != "metrics"} for r in runs]

    def get_branches(self, require_metrics: bool = False) -> list[str]:
        data = self._load()
        runs = data["runs"]
        if require_metrics:
            runs = [r for r in runs if r.get("metrics")]
        branches = sorted({r["branch"] for r in runs if r.get("branch")})
        return branches

    def _migrate_from_sqlite(self, db_path: Path) -> None:
        """Auto-migrate data from runs.db to JSON format."""
        try:
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row

            data = _empty_store()
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY timestamp DESC"
            ).fetchall()

            for row in rows:
                run = dict(row)
                run_id = run.pop("id", run.pop("rowid", None))
                if not run_id:
                    continue

                # Fetch metrics for this run
                metrics_rows = conn.execute(
                    "SELECT * FROM metrics WHERE run_id = ?", (run_id,)
                ).fetchall()
                metrics = {}
                for m in metrics_rows:
                    md = dict(m)
                    metrics[md["metric_name"]] = {
                        "numerator": md.get("numerator"),
                        "denominator": md.get("denominator"),
                        "value": self._normalize_value(
                            md["metric_name"], md.get("value")
                        ),
                        "confidence": md.get("confidence"),
                        "details": json.loads(md["details"]) if md.get("details") else None,
                    }

                data["runs"].append({
                    "id": run_id,
                    "branch": run.get("branch"),
                    "commit_hash": run.get("commit_hash"),
                    "timestamp": run.get("timestamp", ""),
                    "traces_dir": run.get("traces_dir"),
                    "success": run.get("success"),
                    "duration": run.get("duration"),
                    "error": run.get("error"),
                    "output": run.get("output"),
                    "config": run.get("config"),
                    "metadata": run.get("metadata"),
                    "metrics": metrics,
                })

            conn.close()
            self._save(data)
        except Exception:
            pass
