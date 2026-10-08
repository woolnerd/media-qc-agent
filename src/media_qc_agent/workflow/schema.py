"""SQLite schema for workflow state, artifacts, and worker coordination.

A run points at its current immutable plan version, which is the only record of
the current plan, sources, recovery key, and targets. The pointer is NULL only
while a run awaits a creative choice. The run row keeps mutable execution state
and the sources its finding was observed on.
"""

import sqlite3

SCHEMA_VERSION = 2

_NOW = "(strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))"

SCHEMA = f"""
CREATE TABLE artifact_versions (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    external_job_id TEXT UNIQUE,
    created_at TEXT NOT NULL DEFAULT {_NOW}
);
CREATE TABLE artifact_dependencies (
    artifact_id TEXT NOT NULL REFERENCES artifact_versions(id),
    source_kind TEXT NOT NULL,
    source_id TEXT NOT NULL REFERENCES artifact_versions(id),
    PRIMARY KEY (artifact_id, source_kind)
);
CREATE TABLE script_versions (
    version_id TEXT PRIMARY KEY REFERENCES artifact_versions(id),
    authored_text TEXT NOT NULL CHECK (trim(authored_text) <> ''),
    environment TEXT NOT NULL,
    evidence_phrase TEXT
);
CREATE TABLE avatar_versions (
    version_id TEXT PRIMARY KEY REFERENCES artifact_versions(id),
    environment TEXT NOT NULL
);
CREATE TABLE tts_input_versions (
    version_id TEXT PRIMARY KEY REFERENCES artifact_versions(id),
    script_version_id TEXT NOT NULL REFERENCES artifact_versions(id),
    authored_text TEXT NOT NULL,
    candidate_text TEXT NOT NULL,
    spoken_text TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    supported_notation TEXT NOT NULL
);
CREATE TABLE caption_contents (
    version_id TEXT PRIMARY KEY REFERENCES artifact_versions(id),
    cues_json TEXT NOT NULL
);

CREATE TABLE workflow_runs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    plan_version_id TEXT REFERENCES repair_plan_versions(id),
    clarification TEXT,
    observed_script_version_id TEXT NOT NULL REFERENCES artifact_versions(id),
    observed_tts_input_version_id TEXT NOT NULL REFERENCES artifact_versions(id),
    observed_avatar_version_id TEXT NOT NULL REFERENCES artifact_versions(id),
    observed_voice_version_id TEXT NOT NULL REFERENCES artifact_versions(id),
    external_job_id TEXT,
    active_video_version_id TEXT REFERENCES artifact_versions(id),
    active_caption_version_id TEXT REFERENCES artifact_versions(id),
    created_at TEXT NOT NULL DEFAULT {_NOW},
    updated_at TEXT NOT NULL DEFAULT {_NOW}
);
CREATE TABLE repair_plan_versions (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES workflow_runs(id),
    revision INTEGER NOT NULL,
    snapshot TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT {_NOW},
    UNIQUE (run_id, revision)
);
CREATE TABLE plan_approvals (
    plan_version_id TEXT PRIMARY KEY REFERENCES repair_plan_versions(id),
    created_at TEXT NOT NULL DEFAULT {_NOW}
);
CREATE TRIGGER immutable_plan_update BEFORE UPDATE ON repair_plan_versions
BEGIN SELECT RAISE(ABORT, 'plan versions are immutable'); END;
CREATE TRIGGER immutable_plan_delete BEFORE DELETE ON repair_plan_versions
BEGIN SELECT RAISE(ABORT, 'plan versions are immutable'); END;
CREATE TRIGGER plan_pointer_same_run BEFORE UPDATE OF plan_version_id ON workflow_runs
WHEN NEW.plan_version_id IS NOT NULL AND NEW.id IS NOT
    (SELECT run_id FROM repair_plan_versions WHERE id = NEW.plan_version_id)
BEGIN SELECT RAISE(ABORT, 'a run can only point at its own plan versions'); END;
CREATE TRIGGER immutable_approval_update BEFORE UPDATE ON plan_approvals
BEGIN SELECT RAISE(ABORT, 'approvals are immutable'); END;
CREATE TRIGGER immutable_approval_delete BEFORE DELETE ON plan_approvals
BEGIN SELECT RAISE(ABORT, 'approvals are immutable'); END;

CREATE TABLE quality_findings (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE REFERENCES workflow_runs(id),
    artifact_version_id TEXT NOT NULL REFERENCES artifact_versions(id),
    kind TEXT NOT NULL,
    explanation TEXT NOT NULL,
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    created_at TEXT NOT NULL DEFAULT {_NOW}
);
CREATE TABLE quality_evidence (
    id TEXT PRIMARY KEY,
    finding_id TEXT NOT NULL REFERENCES quality_findings(id),
    ordinal INTEGER NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('fact', 'inference', 'uncertainty')),
    artifact_version_id TEXT NOT NULL REFERENCES artifact_versions(id),
    statement TEXT NOT NULL CHECK (trim(statement) <> ''),
    observed TEXT,
    limit_value TEXT,
    created_at TEXT NOT NULL DEFAULT {_NOW},
    UNIQUE (finding_id, ordinal)
);

CREATE TABLE provider_jobs (
    external_job_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES workflow_runs(id),
    plan_version_id TEXT NOT NULL REFERENCES repair_plan_versions(id),
    idempotency_key TEXT NOT NULL UNIQUE,
    traceparent TEXT,
    created_at TEXT NOT NULL DEFAULT {_NOW}
);
CREATE TABLE provider_events (
    external_event_id TEXT PRIMARY KEY,
    external_job_id TEXT NOT NULL REFERENCES provider_jobs(external_job_id),
    event_type TEXT NOT NULL CHECK (event_type = 'completed'),
    result_status TEXT NOT NULL,
    disposition TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT {_NOW}
);

CREATE TABLE worker_policy (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    max_in_flight INTEGER NOT NULL,
    max_attempts INTEGER NOT NULL,
    lease_seconds REAL NOT NULL,
    backoff_seconds REAL NOT NULL,
    max_backoff_seconds REAL NOT NULL
);
CREATE TABLE worker_attempts (
    plan_version_id TEXT PRIMARY KEY REFERENCES repair_plan_versions(id),
    run_id TEXT NOT NULL REFERENCES workflow_runs(id),
    attempts INTEGER NOT NULL,
    lease_owner TEXT,
    lease_until REAL,
    next_attempt_at REAL NOT NULL DEFAULT 0,
    stopped INTEGER NOT NULL DEFAULT 0,
    error_type TEXT,
    first_claimed_at TEXT
);
"""


class IncompatibleDatabase(RuntimeError):
    def __init__(self, found: int) -> None:
        if found > SCHEMA_VERSION:
            message = (
                f"database schema version {found} is newer than this application "
                f"({SCHEMA_VERSION}); upgrade the application."
            )
        else:
            message = (
                f"database schema version {found} is not {SCHEMA_VERSION}; delete "
                "this demo database and its .provider.sqlite3 ledger, then restart."
            )
        super().__init__(message)


def initialize_schema(connection: sqlite3.Connection) -> None:
    """Create the schema in an empty database; reject any other version."""

    connection.execute("PRAGMA foreign_keys = ON")
    version, has_tables = _state(connection)
    if version == 0 and not has_tables:
        try:
            connection.executescript(
                f"BEGIN IMMEDIATE; {SCHEMA}"
                f"PRAGMA user_version = {SCHEMA_VERSION}; COMMIT;"
            )
            version = SCHEMA_VERSION
        except sqlite3.OperationalError:
            connection.rollback()
            # Accept only the schema another connection created first.
            version = _state(connection)[0]
            if version != SCHEMA_VERSION:
                raise
    if version != SCHEMA_VERSION:
        raise IncompatibleDatabase(version)


def _state(connection: sqlite3.Connection) -> tuple[int, bool]:
    """Read the version and table presence from one snapshot."""

    with connection:
        connection.execute("BEGIN")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' LIMIT 1"
        ).fetchone()
    return int(version), table is not None
