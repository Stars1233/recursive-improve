CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    branch TEXT,
    commit_hash TEXT,
    timestamp TEXT NOT NULL,
    traces_dir TEXT,
    success INTEGER,
    duration REAL,
    error TEXT,
    output TEXT,
    config TEXT,
    metadata TEXT
);

CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id),
    metric_name TEXT NOT NULL,
    numerator INTEGER,
    denominator INTEGER,
    value REAL,
    confidence TEXT,
    details TEXT,
    UNIQUE(run_id, metric_name)
);

CREATE INDEX IF NOT EXISTS idx_runs_branch ON runs(branch);
CREATE INDEX IF NOT EXISTS idx_runs_timestamp ON runs(timestamp);
CREATE INDEX IF NOT EXISTS idx_metrics_run_id ON metrics(run_id);
