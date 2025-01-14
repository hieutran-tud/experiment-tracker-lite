"""SQLite-backed storage for reproducible experiment metadata."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

STATUSES = {"running", "completed", "failed", "cancelled"}
FINAL_STATUSES = STATUSES - {"running"}
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")


class ExperimentError(RuntimeError):
    """Raised for invalid run operations or storage failures."""


@dataclass(frozen=True)
class RunRecord:
    id: int
    project: str
    name: str
    status: str
    started_at: str
    ended_at: str | None
    notes: str


@dataclass(frozen=True)
class MetricRecord:
    name: str
    value: float
    step: int
    logged_at: str


@dataclass(frozen=True)
class ArtifactRecord:
    id: int
    name: str
    path: str
    sha256: str
    size_bytes: int
    created_at: str


class ExperimentStore:
    """A small transactional experiment registry stored in one SQLite file."""

    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)
        if self.database.parent != Path("."):
            self.database.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.connection = sqlite3.connect(self.database)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA foreign_keys = ON")
            self.connection.execute("PRAGMA journal_mode = WAL")
            self._initialize()
        except sqlite3.Error as error:
            raise ExperimentError(f"Could not open experiment database: {error}") from error

    def __enter__(self) -> "ExperimentStore":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def create_run(
        self,
        project: str,
        name: str,
        *,
        notes: str = "",
        started_at: str | None = None,
    ) -> RunRecord:
        _validate_name(project, "project")
        _validate_name(name, "run name")
        timestamp = started_at or _utc_now()
        try:
            cursor = self.connection.execute(
                """
                INSERT INTO runs(project, name, status, started_at, notes)
                VALUES (?, ?, 'running', ?, ?)
                """,
                (project, name, timestamp, notes),
            )
            self.connection.commit()
        except sqlite3.Error as error:
            self.connection.rollback()
            raise ExperimentError(f"Could not create run: {error}") from error
        return self.get_run(int(cursor.lastrowid))

    def get_run(self, run_id: int) -> RunRecord:
        row = self.connection.execute(
            "SELECT id, project, name, status, started_at, ended_at, notes FROM runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise ExperimentError(f"Run #{run_id} does not exist.")
        return RunRecord(**dict(row))

    def log_metric(
        self,
        run_id: int,
        name: str,
        value: float,
        *,
        step: int = 0,
        logged_at: str | None = None,
    ) -> MetricRecord:
        self.get_run(run_id)
        _validate_name(name, "metric name")
        if step < 0:
            raise ExperimentError("Metric step must be zero or greater.")
        numeric_value = float(value)
        if not math.isfinite(numeric_value):
            raise ExperimentError("Metric value must be finite.")
        timestamp = logged_at or _utc_now()
        try:
            self.connection.execute(
                """
                INSERT INTO metrics(run_id, name, value, step, logged_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id, name, step) DO UPDATE SET
                    value = excluded.value,
                    logged_at = excluded.logged_at
                """,
                (run_id, name, numeric_value, step, timestamp),
            )
            self.connection.commit()
        except sqlite3.Error as error:
            self.connection.rollback()
            raise ExperimentError(f"Could not log metric: {error}") from error
        return MetricRecord(name=name, value=numeric_value, step=step, logged_at=timestamp)

    def log_param(self, run_id: int, key: str, value: Any) -> None:
        self.get_run(run_id)
        _validate_name(key, "parameter name")
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
        try:
            self.connection.execute(
                """
                INSERT INTO params(run_id, key, value)
                VALUES (?, ?, ?)
                ON CONFLICT(run_id, key) DO UPDATE SET value = excluded.value
                """,
                (run_id, key, encoded),
            )
            self.connection.commit()
        except (TypeError, ValueError) as error:
            raise ExperimentError(f"Parameter {key!r} is not JSON serializable.") from error
        except sqlite3.Error as error:
            self.connection.rollback()
            raise ExperimentError(f"Could not log parameter: {error}") from error

    def log_params(self, run_id: int, values: Mapping[str, Any]) -> None:
        for key, value in values.items():
            self.log_param(run_id, key, value)

    def add_artifact(
        self,
        run_id: int,
        path: str | Path,
        *,
        name: str | None = None,
    ) -> ArtifactRecord:
        self.get_run(run_id)
        artifact_path = Path(path).expanduser().resolve()
        if not artifact_path.is_file():
            raise ExperimentError(f"Artifact does not exist or is not a file: {path}")
        digest = hashlib.sha256()
        size = 0
        with artifact_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
        timestamp = _utc_now()
        artifact_name = name or artifact_path.name
        _validate_name(artifact_name, "artifact name")
        try:
            cursor = self.connection.execute(
                """
                INSERT INTO artifacts(run_id, name, path, sha256, size_bytes, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (run_id, artifact_name, str(artifact_path), digest.hexdigest(), size, timestamp),
            )
            self.connection.commit()
        except sqlite3.Error as error:
            self.connection.rollback()
            raise ExperimentError(f"Could not record artifact: {error}") from error
        row = self.connection.execute(
            "SELECT id, name, path, sha256, size_bytes, created_at FROM artifacts WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
        return ArtifactRecord(**dict(row))

    def finish_run(
        self,
        run_id: int,
        *,
        status: str = "completed",
        ended_at: str | None = None,
    ) -> RunRecord:
        self.get_run(run_id)
        if status not in FINAL_STATUSES:
            raise ExperimentError(
                f"Final status must be one of {sorted(FINAL_STATUSES)}."
            )
        try:
            self.connection.execute(
                "UPDATE runs SET status = ?, ended_at = ? WHERE id = ?",
                (status, ended_at or _utc_now(), run_id),
            )
            self.connection.commit()
        except sqlite3.Error as error:
            self.connection.rollback()
            raise ExperimentError(f"Could not finish run: {error}") from error
        return self.get_run(run_id)

    def list_runs(self, *, project: str | None = None, limit: int = 50) -> list[RunRecord]:
        if limit < 1:
            raise ExperimentError("limit must be at least 1.")
        if project:
            rows = self.connection.execute(
                """
                SELECT id, project, name, status, started_at, ended_at, notes
                FROM runs WHERE project = ? ORDER BY started_at DESC, id DESC LIMIT ?
                """,
                (project, limit),
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT id, project, name, status, started_at, ended_at, notes
                FROM runs ORDER BY started_at DESC, id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [RunRecord(**dict(row)) for row in rows]

    def best_run(
        self,
        project: str,
        metric: str,
        *,
        minimize: bool = False,
    ) -> tuple[RunRecord, float] | None:
        _validate_name(project, "project")
        _validate_name(metric, "metric name")
        order = "ASC" if minimize else "DESC"
        row = self.connection.execute(
            f"""
            SELECT r.id, r.project, r.name, r.status, r.started_at, r.ended_at, r.notes,
                   m.value
            FROM runs AS r
            JOIN metrics AS m ON m.run_id = r.id
            WHERE r.project = ? AND r.status = 'completed' AND m.name = ?
              AND m.step = (
                  SELECT MAX(m2.step) FROM metrics AS m2
                  WHERE m2.run_id = r.id AND m2.name = m.name
              )
            ORDER BY m.value {order}, r.id ASC
            LIMIT 1
            """,
            (project, metric),
        ).fetchone()
        if row is None:
            return None
        data = dict(row)
        value = float(data.pop("value"))
        return RunRecord(**data), value

    def summary(self, run_id: int) -> dict[str, Any]:
        run = self.get_run(run_id)
        metrics = self.connection.execute(
            "SELECT name, value, step, logged_at FROM metrics WHERE run_id = ? ORDER BY name, step",
            (run_id,),
        ).fetchall()
        params = self.connection.execute(
            "SELECT key, value FROM params WHERE run_id = ? ORDER BY key",
            (run_id,),
        ).fetchall()
        artifacts = self.connection.execute(
            """
            SELECT id, name, path, sha256, size_bytes, created_at
            FROM artifacts WHERE run_id = ? ORDER BY id
            """,
            (run_id,),
        ).fetchall()
        return {
            "run": asdict(run),
            "metrics": [dict(row) for row in metrics],
            "params": {row["key"]: json.loads(row["value"]) for row in params},
            "artifacts": [dict(row) for row in artifacts],
        }

    def _initialize(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL,
                name TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed', 'cancelled')),
                started_at TEXT NOT NULL,
                ended_at TEXT,
                notes TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS metrics (
                run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                value REAL NOT NULL,
                step INTEGER NOT NULL DEFAULT 0 CHECK (step >= 0),
                logged_at TEXT NOT NULL,
                PRIMARY KEY (run_id, name, step)
            );
            CREATE TABLE IF NOT EXISTS params (
                run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY (run_id, key)
            );
            CREATE TABLE IF NOT EXISTS artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                path TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_runs_project ON runs(project, started_at);
            CREATE INDEX IF NOT EXISTS idx_metrics_name ON metrics(name, value);
            """
        )
        self.connection.commit()


def _validate_name(value: str, label: str) -> None:
    if not isinstance(value, str) or not NAME_PATTERN.fullmatch(value):
        raise ExperimentError(
            f"Invalid {label}: use 1-128 letters, numbers, dots, underscores, colons, slashes, or hyphens."
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
