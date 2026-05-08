"""SQLite-backed experiment store for the production harness."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

from .models import (
    AdoptionRecord,
    AttemptSummary,
    AttemptStatus,
    ExperimentResult,
    PlannerValidationReport,
    PromotionRecord,
    SearchNode,
    TaskSpec,
    WorkOrder,
    utc_now,
)


class ExperimentStore:
    """Persistent SQLite store plus artifact directory helpers."""

    def __init__(self, harness_dir: Path):
        self.harness_dir = Path(harness_dir)
        self.harness_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir = self.harness_dir / "artifacts"
        self.workspaces_dir = self.harness_dir / "workspaces"
        self.git_dir = self.harness_dir / "git"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.workspaces_dir.mkdir(parents=True, exist_ok=True)
        self.git_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.harness_dir / "state.sqlite"
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS work_orders (
                    work_order_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    parent_attempt_id TEXT,
                    status TEXT NOT NULL DEFAULT 'queued',
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS attempts (
                    attempt_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    work_order_id TEXT NOT NULL,
                    parent_attempt_id TEXT,
                    branch_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    metric_value REAL,
                    higher_is_better INTEGER,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    payload TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_attempts_task_status
                    ON attempts(task_id, status);

                CREATE TABLE IF NOT EXISTS search_nodes (
                    node_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    attempt_id TEXT,
                    work_order_id TEXT,
                    parent_node_id TEXT,
                    parent_attempt_id TEXT,
                    depth INTEGER NOT NULL,
                    score REAL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_nodes_task_status
                    ON search_nodes(task_id, status);

                CREATE TABLE IF NOT EXISTS planner_validations (
                    validation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    round_index INTEGER NOT NULL,
                    planner_name TEXT NOT NULL,
                    valid INTEGER NOT NULL,
                    repaired INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_planner_validations_task_round
                    ON planner_validations(task_id, round_index);

                CREATE TABLE IF NOT EXISTS promotions (
                    promotion_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    round_index INTEGER NOT NULL,
                    planner_name TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_promotions_task_round
                    ON promotions(task_id, round_index);

                CREATE TABLE IF NOT EXISTS adoptions (
                    adoption_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    attempt_id TEXT NOT NULL,
                    adopted_branch TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_adoptions_task
                    ON adoptions(task_id, created_at);

                CREATE TABLE IF NOT EXISTS attempt_summaries (
                    attempt_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_attempt_summaries_task
                    ON attempt_summaries(task_id, created_at);
                """
            )

    def artifact_dir(self, attempt_id: str) -> Path:
        path = self.artifacts_dir / attempt_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def workspace_dir(self, attempt_id: str) -> Path:
        path = self.workspaces_dir / attempt_id
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def task_git_repo(self, task_id: str) -> Path:
        path = self.git_dir / task_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def upsert_task(self, task: TaskSpec) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tasks(task_id, payload, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET payload=excluded.payload
                """,
                (task.task_id, task.to_json_text(), task.created_at.isoformat()),
            )

    def get_task(self, task_id: str) -> TaskSpec | None:
        with self._connect() as conn:
            row = conn.execute("SELECT payload FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        return TaskSpec.model_validate_json(row["payload"]) if row else None

    def append_work_order(self, work_order: WorkOrder) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO work_orders(
                    work_order_id, task_id, parent_attempt_id, status, payload, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    work_order.work_order_id,
                    work_order.task_id,
                    work_order.parent_attempt_id,
                    "queued",
                    work_order.to_json_text(),
                    work_order.created_at.isoformat(),
                ),
            )

    def update_work_order_status(self, work_order_id: str, status: AttemptStatus | str) -> None:
        value = status.value if isinstance(status, AttemptStatus) else status
        with self._connect() as conn:
            conn.execute(
                "UPDATE work_orders SET status=? WHERE work_order_id=?",
                (value, work_order_id),
            )

    def get_work_order(self, work_order_id: str) -> WorkOrder | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM work_orders WHERE work_order_id=?", (work_order_id,)
            ).fetchone()
        return WorkOrder.model_validate_json(row["payload"]) if row else None

    def list_work_orders(self, task_id: str | None = None) -> list[WorkOrder]:
        query = "SELECT payload FROM work_orders"
        args: tuple[str, ...] = ()
        if task_id is not None:
            query += " WHERE task_id=?"
            args = (task_id,)
        query += " ORDER BY created_at ASC"
        with self._connect() as conn:
            rows = conn.execute(query, args).fetchall()
        return [WorkOrder.model_validate_json(row["payload"]) for row in rows]

    def append_attempt(self, attempt: ExperimentResult) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO attempts(
                    attempt_id, task_id, work_order_id, parent_attempt_id,
                    branch_name, status, metric_value, higher_is_better,
                    created_at, completed_at, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt.attempt_id,
                    attempt.task_id,
                    attempt.work_order_id,
                    attempt.parent_attempt_id,
                    attempt.branch_name,
                    attempt.status.value,
                    attempt.metric_value,
                    None if attempt.higher_is_better is None else int(attempt.higher_is_better),
                    attempt.created_at.isoformat(),
                    attempt.completed_at.isoformat() if attempt.completed_at else None,
                    attempt.to_json_text(),
                ),
            )

    def get_attempt(self, attempt_id: str) -> ExperimentResult | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM attempts WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
        return ExperimentResult.model_validate_json(row["payload"]) if row else None

    def list_attempts(
        self,
        task_id: str | None = None,
        statuses: Iterable[AttemptStatus | str] | None = None,
    ) -> list[ExperimentResult]:
        query = "SELECT payload FROM attempts"
        clauses: list[str] = []
        args: list[str] = []
        if task_id is not None:
            clauses.append("task_id=?")
            args.append(task_id)
        if statuses is not None:
            status_values = [s.value if isinstance(s, AttemptStatus) else s for s in statuses]
            if status_values:
                placeholders = ",".join("?" for _ in status_values)
                clauses.append(f"status IN ({placeholders})")
                args.extend(status_values)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at ASC"
        with self._connect() as conn:
            rows = conn.execute(query, tuple(args)).fetchall()
        return [ExperimentResult.model_validate_json(row["payload"]) for row in rows]

    def get_best_experiment(self, task_id: str) -> ExperimentResult | None:
        attempts = [
            attempt
            for attempt in self.list_attempts(task_id, statuses=[AttemptStatus.SUCCESS])
            if attempt.metric_value is not None and attempt.higher_is_better is not None
        ]
        if not attempts:
            return None
        direction = attempts[0].higher_is_better
        if any(attempt.higher_is_better != direction for attempt in attempts):
            return None
        return max(attempts, key=lambda a: a.metric_value) if direction else min(attempts, key=lambda a: a.metric_value)

    def append_search_node(self, node: SearchNode) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO search_nodes(
                    node_id, task_id, attempt_id, work_order_id, parent_node_id,
                    parent_attempt_id, depth, score, status, payload, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node.node_id,
                    node.task_id,
                    node.attempt_id,
                    node.work_order_id,
                    node.parent_node_id,
                    node.parent_attempt_id,
                    node.depth,
                    node.score,
                    node.status.value,
                    node.to_json_text(),
                    node.created_at.isoformat(),
                ),
            )

    def list_search_nodes(self, task_id: str | None = None) -> list[SearchNode]:
        query = "SELECT payload FROM search_nodes"
        args: tuple[str, ...] = ()
        if task_id is not None:
            query += " WHERE task_id=?"
            args = (task_id,)
        query += " ORDER BY created_at ASC"
        with self._connect() as conn:
            rows = conn.execute(query, args).fetchall()
        return [SearchNode.model_validate_json(row["payload"]) for row in rows]

    def append_planner_validation(self, task_id: str, report: PlannerValidationReport) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO planner_validations(
                    task_id, round_index, planner_name, valid, repaired, payload, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    report.round_index,
                    report.planner_name,
                    int(report.valid),
                    int(report.repaired),
                    report.to_json_text(),
                    report.created_at.isoformat(),
                ),
            )

    def list_planner_validations(self, task_id: str | None = None) -> list[PlannerValidationReport]:
        query = "SELECT payload FROM planner_validations"
        args: tuple[str, ...] = ()
        if task_id is not None:
            query += " WHERE task_id=?"
            args = (task_id,)
        query += " ORDER BY created_at ASC"
        with self._connect() as conn:
            rows = conn.execute(query, args).fetchall()
        return [PlannerValidationReport.model_validate_json(row["payload"]) for row in rows]

    def append_promotion(self, promotion: PromotionRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO promotions(
                    promotion_id, task_id, round_index, planner_name, payload, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    promotion.promotion_id,
                    promotion.task_id,
                    promotion.round_index,
                    promotion.planner_name,
                    promotion.to_json_text(),
                    promotion.created_at.isoformat(),
                ),
            )

    def list_promotions(self, task_id: str | None = None) -> list[PromotionRecord]:
        query = "SELECT payload FROM promotions"
        args: tuple[str, ...] = ()
        if task_id is not None:
            query += " WHERE task_id=?"
            args = (task_id,)
        query += " ORDER BY created_at ASC"
        with self._connect() as conn:
            rows = conn.execute(query, args).fetchall()
        return [PromotionRecord.model_validate_json(row["payload"]) for row in rows]

    def append_adoption(self, adoption: AdoptionRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO adoptions(
                    adoption_id, task_id, attempt_id, adopted_branch, payload, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    adoption.adoption_id,
                    adoption.task_id,
                    adoption.attempt_id,
                    adoption.adopted_branch,
                    adoption.to_json_text(),
                    adoption.created_at.isoformat(),
                ),
            )

    def list_adoptions(self, task_id: str | None = None) -> list[AdoptionRecord]:
        query = "SELECT payload FROM adoptions"
        args: tuple[str, ...] = ()
        if task_id is not None:
            query += " WHERE task_id=?"
            args = (task_id,)
        query += " ORDER BY created_at ASC"
        with self._connect() as conn:
            rows = conn.execute(query, args).fetchall()
        return [AdoptionRecord.model_validate_json(row["payload"]) for row in rows]

    def append_attempt_summary(self, summary: AttemptSummary) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO attempt_summaries(
                    attempt_id, task_id, payload, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    summary.attempt_id,
                    summary.task_id,
                    summary.to_json_text(),
                    summary.created_at.isoformat(),
                ),
            )

    def get_attempt_summary(self, attempt_id: str) -> AttemptSummary | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM attempt_summaries WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
        return AttemptSummary.model_validate_json(row["payload"]) if row else None

    def list_attempt_summaries(self, task_id: str | None = None) -> list[AttemptSummary]:
        query = "SELECT payload FROM attempt_summaries"
        args: tuple[str, ...] = ()
        if task_id is not None:
            query += " WHERE task_id=?"
            args = (task_id,)
        query += " ORDER BY created_at ASC"
        with self._connect() as conn:
            rows = conn.execute(query, args).fetchall()
        return [AttemptSummary.model_validate_json(row["payload"]) for row in rows]

    def list_resumable_attempts(self, task_id: str | None = None) -> list[ExperimentResult]:
        return self.list_attempts(
            task_id,
            statuses=[
                AttemptStatus.IMPLEMENTATION_COMPLETED,
                AttemptStatus.EVALUATION_RUNNING,
            ],
        )

    def mark_running_as_interrupted(self) -> int:
        now = utc_now()
        attempts = self.list_attempts(statuses=[AttemptStatus.RUNNING])
        for attempt in attempts:
            attempt.status = AttemptStatus.INTERRUPTED
            attempt.completed_at = now
            attempt.failure_class = "interrupted"
            attempt.failure_reason = "Process stopped while attempt was running"
            self.append_attempt(attempt)
            self.update_work_order_status(attempt.work_order_id, AttemptStatus.INTERRUPTED)
        return len(attempts)
