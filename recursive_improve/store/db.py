"""RunStore: SQLite-backed storage for eval runs and metrics."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"
_DEFAULT_DB = Path("eval/runs.db")


class RunStore:
    """SQLite store for eval runs and their metrics."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else _DEFAULT_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self):
        with self._conn() as conn:
            conn.executescript(_SCHEMA_PATH.read_text())

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def insert_run(self, run_id: str, *, branch: str | None = None,
                   commit_hash: str | None = None, timestamp: str,
                   traces_dir: str | None = None, success: bool | None = None,
                   duration: float | None = None, error: str | None = None,
                   output: str | None = None, config: dict | None = None,
                   metadata: dict | None = None):
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO runs
                   (id, branch, commit_hash, timestamp, traces_dir, success,
                    duration, error, output, config, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (run_id, branch, commit_hash, timestamp, traces_dir,
                 int(success) if success is not None else None,
                 duration, error, output,
                 json.dumps(config) if config else None,
                 json.dumps(metadata) if metadata else None),
            )

    def insert_metrics(self, run_id: str, metrics_dict: dict):
        """Bulk insert metrics from eval results.

        metrics_dict: {"metric_name": {"numerator": N, "denominator": N,
                        "value": float, "confidence": str, ...}, ...}
        """
        with self._conn() as conn:
            for name, m in metrics_dict.items():
                conn.execute(
                    """INSERT OR REPLACE INTO metrics
                       (run_id, metric_name, numerator, denominator, value,
                        confidence, details)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (run_id, name,
                     m.get("numerator"), m.get("denominator"),
                     m.get("value"), m.get("confidence"),
                     json.dumps(m) if m else None),
                )

    def run_has_metrics(self, run_id: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM metrics WHERE run_id = ? LIMIT 1",
                (run_id,),
            ).fetchone()
            return row is not None

    def get_run(self, run_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            return dict(row) if row else None

    def get_runs_by_branch(self, branch: str, require_metrics: bool = False) -> list[dict]:
        with self._conn() as conn:
            query = "SELECT * FROM runs WHERE branch = ?"
            if require_metrics:
                query += " AND EXISTS (SELECT 1 FROM metrics WHERE metrics.run_id = runs.id)"
            query += " ORDER BY timestamp DESC"
            rows = conn.execute(query, (branch,)).fetchall()
            return [dict(r) for r in rows]

    def get_latest_run(self, branch: str | None = None, require_metrics: bool = False) -> dict | None:
        with self._conn() as conn:
            where = []
            params = []
            if branch:
                where.append("branch = ?")
                params.append(branch)
            if require_metrics:
                where.append("EXISTS (SELECT 1 FROM metrics WHERE metrics.run_id = runs.id)")

            query = "SELECT * FROM runs"
            if where:
                query += " WHERE " + " AND ".join(where)
            query += " ORDER BY timestamp DESC LIMIT 1"
            row = conn.execute(query, params).fetchone()
            return dict(row) if row else None

    def get_metrics(self, run_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM metrics WHERE run_id = ?", (run_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_all_runs(self, require_metrics: bool = False) -> list[dict]:
        with self._conn() as conn:
            query = "SELECT * FROM runs"
            if require_metrics:
                query += " WHERE EXISTS (SELECT 1 FROM metrics WHERE metrics.run_id = runs.id)"
            query += " ORDER BY timestamp DESC"
            rows = conn.execute(query).fetchall()
            return [dict(r) for r in rows]

    def get_branches(self, require_metrics: bool = False) -> list[str]:
        with self._conn() as conn:
            query = "SELECT DISTINCT branch FROM runs WHERE branch IS NOT NULL"
            if require_metrics:
                query += " AND EXISTS (SELECT 1 FROM metrics WHERE metrics.run_id = runs.id)"
            query += " ORDER BY branch"
            rows = conn.execute(query).fetchall()
            return [r["branch"] for r in rows]
