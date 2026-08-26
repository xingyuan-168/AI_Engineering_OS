"""Transactional task DAG, Handoff review, integration, and join persistence."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from codex_ai_os.domain.coordination import HandoffReviewInput, TaskBlueprint, TaskGroupView
from codex_ai_os.domain.ids import new_id
from codex_ai_os.infrastructure.database import Database


class CoordinationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class PlannedTask:
    id: str
    key: str
    agent: str
    skill: str
    prompt: str
    allowed_paths: tuple[str, ...]
    depends_on_task_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HandoffDecisionResult:
    handoff_id: str
    task_id: str
    task_group_id: str
    decision: str
    source_commit: str


class CoordinationStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_group(
        self,
        *,
        run_id: str,
        name: str,
        phase: str,
        base_commit: str,
        blueprints: tuple[TaskBlueprint, ...],
    ) -> tuple[TaskGroupView, tuple[PlannedTask, ...]]:
        if not blueprints or len(blueprints) > 4:
            raise CoordinationError("CONFIG_INVALID", "task group must contain 1..4 tasks")
        keys = [task.key for task in blueprints]
        if len(keys) != len(set(keys)):
            raise CoordinationError("CONFIG_INVALID", "task blueprint keys must be unique")
        dependencies = self._dependencies(blueprints)
        self._assert_acyclic(keys, dependencies)
        now = _utc_now()
        group_id = new_id("TASKGROUP")
        task_ids = {key: new_id("TASK") for key in keys}
        planned: list[PlannedTask] = []
        with self.database.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                run = connection.execute(
                    "SELECT project_id, max_parallel_agents FROM workflow_runs WHERE id = ?",
                    (run_id,),
                ).fetchone()
                if run is None:
                    raise CoordinationError("RECOVERY_UNAVAILABLE", f"run not found: {run_id}")
                if int(run["max_parallel_agents"]) > 4:
                    raise CoordinationError("CONFIG_INVALID", "max parallel agents exceeds 4")
                connection.execute(
                    """
                    INSERT INTO task_groups(
                        id, run_id, name, phase, status, join_policy,
                        base_commit, state_version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'running', 'all_accepted_merged', ?, 0, ?, ?)
                    """,
                    (group_id, run_id, name, phase, base_commit, now, now),
                )
                for blueprint in blueprints:
                    task_id = task_ids[blueprint.key]
                    input_hash = hashlib.sha256(
                        json.dumps(
                            {
                                "run_id": run_id,
                                "group": group_id,
                                "task": blueprint.model_dump(mode="json"),
                                "base_commit": base_commit,
                            },
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode()
                    ).hexdigest()
                    connection.execute(
                        """
                        INSERT INTO tasks(
                            id, run_id, agent, status, input_hash, branch, worktree,
                            output_ref, review_status, state_version, created_at, updated_at,
                            task_group_id, task_key, allowed_paths_json, producer
                            , skill, prompt
                        ) VALUES (
                            ?, ?, ?, 'pending', ?, NULL, NULL, NULL, NULL,
                            0, ?, ?, ?, ?, ?, ?, ?, ?
                        )
                        """,
                        (
                            task_id,
                            run_id,
                            blueprint.agent,
                            input_hash,
                            now,
                            now,
                            group_id,
                            blueprint.key,
                            _json(blueprint.allowed_paths),
                            blueprint.agent,
                            blueprint.skill,
                            blueprint.prompt,
                        ),
                    )
                    for dependency_key in sorted(dependencies[blueprint.key]):
                        connection.execute(
                            """
                            INSERT INTO task_dependencies(
                                task_id, depends_on_task_id, dependency_type, created_at
                            ) VALUES (?, ?, ?, ?)
                            """,
                            (
                                task_id,
                                task_ids[dependency_key],
                                (
                                    "ordering"
                                    if dependency_key in blueprint.depends_on
                                    else "path_overlap"
                                ),
                                now,
                            ),
                        )
                    planned.append(
                        PlannedTask(
                            id=task_id,
                            key=blueprint.key,
                            agent=blueprint.agent,
                            skill=blueprint.skill,
                            prompt=blueprint.prompt,
                            allowed_paths=blueprint.allowed_paths,
                            depends_on_task_ids=tuple(
                                task_ids[key] for key in sorted(dependencies[blueprint.key])
                            ),
                        )
                    )
                connection.execute(
                    """
                    INSERT INTO events(
                        event_id, project_id, run_id, task_id, event_type,
                        idempotency_key, payload_json, approval_required, created_at
                    ) VALUES (?, ?, ?, NULL, 'task_group.created', ?, ?, 0, ?)
                    """,
                    (
                        new_id("EVENT"),
                        str(run["project_id"]),
                        run_id,
                        f"{run_id}:task_group.created:{group_id}",
                        _json({"task_group_id": group_id, "task_ids": list(task_ids.values())}),
                        now,
                    ),
                )
                connection.commit()
            except (sqlite3.Error, CoordinationError):
                connection.rollback()
                raise
        return self.group_view(group_id), tuple(planned)

    def group_view(self, group_id: str) -> TaskGroupView:
        with self.database.connection() as connection:
            group = connection.execute(
                "SELECT * FROM task_groups WHERE id = ?", (group_id,)
            ).fetchone()
            if group is None:
                raise CoordinationError("RECOVERY_UNAVAILABLE", f"task group not found: {group_id}")
            ready = self._ready_rows(connection, group_id)
            blocked = connection.execute(
                "SELECT id FROM tasks WHERE task_group_id = ? AND status = 'blocked' ORDER BY id",
                (group_id,),
            ).fetchall()
        return TaskGroupView(
            id=str(group["id"]),
            run_id=str(group["run_id"]),
            name=str(group["name"]),
            phase=str(group["phase"]),
            status=str(group["status"]),
            base_commit=str(group["base_commit"]),
            state_version=int(group["state_version"]),
            ready_task_ids=tuple(str(row["id"]) for row in ready),
            blocked_task_ids=tuple(str(row["id"]) for row in blocked),
        )

    def ready_tasks(self, group_id: str, *, limit: int = 4) -> tuple[PlannedTask, ...]:
        if limit < 1 or limit > 4:
            raise CoordinationError("CONFIG_INVALID", "ready task limit must be 1..4")
        with self.database.connection() as connection:
            rows = self._ready_rows(connection, group_id)[:limit]
            planned: list[PlannedTask] = []
            for row in rows:
                deps = connection.execute(
                    "SELECT depends_on_task_id FROM task_dependencies "
                    "WHERE task_id = ? ORDER BY depends_on_task_id",
                    (str(row["id"]),),
                ).fetchall()
                planned.append(
                    PlannedTask(
                        id=str(row["id"]),
                        key=str(row["task_key"]),
                        agent=str(row["agent"]),
                        skill=str(row["skill"]),
                        prompt=str(row["prompt"]),
                        allowed_paths=_string_tuple(str(row["allowed_paths_json"])),
                        depends_on_task_ids=tuple(str(dep[0]) for dep in deps),
                    )
                )
        return tuple(planned)

    def submit_handoff(
        self,
        *,
        task_id: str,
        source_commit: str,
        artifact_refs: dict[str, str],
        checks: tuple[str, ...],
        risks: tuple[str, ...] = (),
        open_questions: tuple[str, ...] = (),
    ) -> str:
        now = _utc_now()
        with self.database.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                task = connection.execute(
                    "SELECT * FROM tasks WHERE id = ?", (task_id,)
                ).fetchone()
                if task is None or task["task_group_id"] is None:
                    raise CoordinationError("RECOVERY_UNAVAILABLE", "coordinated task not found")
                if str(task["status"]) not in {"pending", "running", "completed"}:
                    raise CoordinationError("STATE_VERSION_CONFLICT", "task cannot submit Handoff")
                connection.execute(
                    """
                    UPDATE tasks SET status = 'completed', head_commit = ?,
                        review_status = 'pending', state_version = state_version + 1,
                        updated_at = ? WHERE id = ?
                    """,
                    (source_commit.casefold(), now, task_id),
                )
                handoff = connection.execute(
                    "SELECT * FROM handoffs WHERE task_id = ? ORDER BY created_at DESC LIMIT 1",
                    (task_id,),
                ).fetchone()
                if handoff is None:
                    handoff_id = new_id("HANDOFF")
                    connection.execute(
                        """
                        INSERT INTO handoffs(
                            id, run_id, task_id, producer, consumer, status,
                            artifact_refs_json, commit_refs_json, tests_json, risks_json,
                            open_questions_json, created_at, accepted_at, reviewed_commit,
                            reviewer, decision_reason, rejection_reason, blocked_reason,
                            updated_at, state_version
                        ) VALUES (?, ?, ?, ?, 'integration', 'ready', ?, ?, ?, ?, ?, ?,
                                  NULL, NULL, NULL, NULL, NULL, NULL, ?, 0)
                        """,
                        (
                            handoff_id,
                            str(task["run_id"]),
                            task_id,
                            str(task["agent"]),
                            _json(artifact_refs),
                            _json({"source_commit": source_commit.casefold()}),
                            _json(checks),
                            _json(risks),
                            _json(open_questions),
                            now,
                            now,
                        ),
                    )
                else:
                    handoff_id = str(handoff["id"])
                    if str(handoff["status"]) not in {"rejected", "blocked", "ready"}:
                        raise CoordinationError(
                            "HANDOFF_NOT_ACCEPTED", "accepted Handoff cannot be overwritten"
                        )
                    connection.execute(
                        """
                        UPDATE handoffs SET status = 'ready', artifact_refs_json = ?,
                            commit_refs_json = ?, tests_json = ?, risks_json = ?,
                            open_questions_json = ?, reviewed_commit = NULL, reviewer = NULL,
                            decision_reason = NULL, rejection_reason = NULL,
                            blocked_reason = NULL, accepted_at = NULL, updated_at = ?,
                            state_version = state_version + 1 WHERE id = ?
                        """,
                        (
                            _json(artifact_refs),
                            _json({"source_commit": source_commit.casefold()}),
                            _json(checks),
                            _json(risks),
                            _json(open_questions),
                            now,
                            handoff_id,
                        ),
                    )
                connection.commit()
            except (sqlite3.Error, CoordinationError):
                connection.rollback()
                raise
        return handoff_id

    def review_handoff(self, review: HandoffReviewInput) -> HandoffDecisionResult:
        now = _utc_now()
        with self.database.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT h.*, t.task_group_id, t.head_commit, t.agent
                    FROM handoffs h JOIN tasks t ON t.id = h.task_id
                    WHERE h.id = ?
                    """,
                    (review.handoff_id,),
                ).fetchone()
                if row is None or row["task_group_id"] is None:
                    raise CoordinationError("RECOVERY_UNAVAILABLE", "Handoff not found")
                if str(row["status"]) != "ready":
                    raise CoordinationError("STATE_VERSION_CONFLICT", "Handoff is not ready")
                if review.reviewer == str(row["producer"]):
                    raise CoordinationError("REVIEW_STALE", "producer cannot review own Handoff")
                if review.reviewed_commit.casefold() != str(row["head_commit"]).casefold():
                    raise CoordinationError("REVIEW_STALE", "reviewed Commit is not task HEAD")
                connection.execute(
                    """
                    INSERT INTO handoff_reviews(
                        id, handoff_id, reviewer, reviewed_commit, decision, reason,
                        findings_json, risks_json, report_ref, report_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id("HANDOFFREVIEW"),
                        review.handoff_id,
                        review.reviewer,
                        review.reviewed_commit.casefold(),
                        review.decision.value,
                        review.reason,
                        _json(
                            [
                                finding.model_dump(mode="json")
                                for finding in review.findings
                            ]
                        ),
                        _json(review.risks),
                        review.report_ref,
                        review.report_hash.casefold(),
                        now,
                    ),
                )
                connection.execute(
                    """
                    UPDATE handoffs SET status = ?, reviewer = ?, reviewed_commit = ?,
                        decision_reason = ?, rejection_reason = ?, blocked_reason = ?,
                        accepted_at = ?, updated_at = ?, state_version = state_version + 1
                    WHERE id = ?
                    """,
                    (
                        review.decision.value,
                        review.reviewer,
                        review.reviewed_commit.casefold(),
                        review.reason,
                        review.reason if review.decision.value == "rejected" else None,
                        review.reason if review.decision.value == "blocked" else None,
                        now if review.decision.value == "accepted" else None,
                        now,
                        review.handoff_id,
                    ),
                )
                if review.decision.value == "rejected":
                    connection.execute(
                        "UPDATE tasks SET status = 'running', review_status = 'rejected', "
                        "state_version = state_version + 1, updated_at = ? WHERE id = ?",
                        (now, str(row["task_id"])),
                    )
                elif review.decision.value == "blocked":
                    connection.execute(
                        "UPDATE tasks SET status = 'blocked', review_status = 'blocked', "
                        "state_version = state_version + 1, updated_at = ? WHERE id = ?",
                        (now, str(row["task_id"])),
                    )
                    connection.execute(
                        "UPDATE task_groups SET status = 'blocked', "
                        "state_version = state_version + 1, updated_at = ? WHERE id = ?",
                        (now, str(row["task_group_id"])),
                    )
                else:
                    connection.execute(
                        "UPDATE tasks SET review_status = 'accepted', "
                        "state_version = state_version + 1, updated_at = ? WHERE id = ?",
                        (now, str(row["task_id"])),
                    )
                connection.commit()
            except (sqlite3.Error, CoordinationError):
                connection.rollback()
                raise
        return HandoffDecisionResult(
            handoff_id=review.handoff_id,
            task_id=str(row["task_id"]),
            task_group_id=str(row["task_group_id"]),
            decision=review.decision.value,
            source_commit=review.reviewed_commit.casefold(),
        )

    def record_merge(
        self,
        *,
        decision: HandoffDecisionResult,
        source_branch: str,
        integration_branch: str,
        integration_head_before: str,
        merge_commit: str | None,
        parent_commits: tuple[str, ...],
        status: str,
        conflict_paths: tuple[str, ...] = (),
        error_code: str | None = None,
    ) -> TaskGroupView:
        now = _utc_now()
        with self.database.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT OR REPLACE INTO integration_merges(
                        id, run_id, task_group_id, task_id, handoff_id,
                        source_branch, source_commit, integration_branch,
                        integration_head_before, merge_commit, parent_commits_json,
                        status, conflict_paths_json, error_code, created_at, updated_at
                    )
                    SELECT ?, h.run_id, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    FROM handoffs h WHERE h.id = ?
                    """,
                    (
                        new_id("MERGE"),
                        decision.task_group_id,
                        decision.task_id,
                        decision.handoff_id,
                        source_branch,
                        decision.source_commit,
                        integration_branch,
                        integration_head_before,
                        merge_commit,
                        _json(parent_commits),
                        status,
                        _json(conflict_paths),
                        error_code,
                        now,
                        now,
                        decision.handoff_id,
                    ),
                )
                group = connection.execute(
                    "SELECT run_id, state_version FROM task_groups WHERE id = ?",
                    (decision.task_group_id,),
                ).fetchone()
                if group is None:
                    raise CoordinationError("RECOVERY_UNAVAILABLE", "task group disappeared")
                if status == "merged":
                    all_joined = connection.execute(
                        """
                        SELECT NOT EXISTS (
                            SELECT 1 FROM tasks t
                            LEFT JOIN handoffs h ON h.task_id = t.id AND h.status = 'accepted'
                            LEFT JOIN integration_merges m
                              ON m.task_id = t.id AND m.status = 'merged'
                            WHERE t.task_group_id = ?
                              AND (t.status <> 'completed' OR h.id IS NULL OR m.id IS NULL)
                        )
                        """,
                        (decision.task_group_id,),
                    ).fetchone()[0]
                    target_status = "completed" if bool(all_joined) else "running"
                else:
                    target_status = "blocked"
                    connection.execute(
                        """
                        UPDATE handoffs SET status = 'rejected', rejection_reason = ?,
                            updated_at = ?, state_version = state_version + 1
                        WHERE id = ? AND status = 'accepted'
                        """,
                        (error_code or status, now, decision.handoff_id),
                    )
                    connection.execute(
                        """
                        UPDATE tasks SET status = 'running', review_status = 'rejected',
                            state_version = state_version + 1, updated_at = ?
                        WHERE id = ?
                        """,
                        (now, decision.task_id),
                    )
                connection.execute(
                    "UPDATE task_groups SET status = ?, state_version = state_version + 1, "
                    "updated_at = ? WHERE id = ?",
                    (target_status, now, decision.task_group_id),
                )
                if target_status == "completed":
                    connection.execute(
                        """
                        UPDATE workflow_runs SET integration_head = ?,
                            state_version = state_version + 1, updated_at = ?
                        WHERE id = ?
                        """,
                        (merge_commit, now, str(group["run_id"])),
                    )
                connection.commit()
            except (sqlite3.Error, CoordinationError):
                connection.rollback()
                raise
        return self.group_view(decision.task_group_id)

    @staticmethod
    def _dependencies(
        blueprints: tuple[TaskBlueprint, ...],
    ) -> dict[str, set[str]]:
        keys = {task.key for task in blueprints}
        result = {task.key: set(task.depends_on) for task in blueprints}
        for key, dependencies in result.items():
            unknown = dependencies - keys
            if unknown:
                raise CoordinationError(
                    "TASK_DEPENDENCY_BLOCKED", f"{key} has unknown dependencies: {sorted(unknown)}"
                )
        for index, task in enumerate(blueprints):
            for previous in blueprints[:index]:
                if _paths_overlap(task.allowed_paths, previous.allowed_paths):
                    result[task.key].add(previous.key)
        return result

    @staticmethod
    def _assert_acyclic(keys: list[str], dependencies: dict[str, set[str]]) -> None:
        remaining = {key: set(dependencies[key]) for key in keys}
        while remaining:
            ready = {key for key, deps in remaining.items() if not deps}
            if not ready:
                raise CoordinationError("TASK_DEPENDENCY_BLOCKED", "task dependency cycle")
            for key in ready:
                remaining.pop(key)
            for deps in remaining.values():
                deps.difference_update(ready)

    @staticmethod
    def _ready_rows(connection: sqlite3.Connection, group_id: str) -> list[sqlite3.Row]:
        return connection.execute(
            """
            SELECT t.* FROM tasks t
            WHERE t.task_group_id = ? AND t.status = 'pending'
              AND NOT EXISTS (
                SELECT 1 FROM task_dependencies d
                WHERE d.task_id = t.id AND NOT EXISTS (
                    SELECT 1 FROM handoffs h
                    JOIN integration_merges m ON m.handoff_id = h.id
                    WHERE h.task_id = d.depends_on_task_id
                      AND h.status = 'accepted' AND m.status = 'merged'
                )
              )
            ORDER BY t.created_at, t.id
            """,
            (group_id,),
        ).fetchall()


def _paths_overlap(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    for left_path in left:
        normalized_left = left_path.replace("\\", "/").rstrip("/")
        for right_path in right:
            normalized_right = right_path.replace("\\", "/").rstrip("/")
            if (
                normalized_left == normalized_right
                or normalized_left.startswith(normalized_right + "/")
                or normalized_right.startswith(normalized_left + "/")
            ):
                return True
    return False


def _string_tuple(raw: str) -> tuple[str, ...]:
    value: object = json.loads(raw)
    if not isinstance(value, list):
        raise CoordinationError("RECOVERY_UNAVAILABLE", "stored paths are invalid")
    object_values = cast(list[object], value)
    if not all(isinstance(item, str) for item in object_values):
        raise CoordinationError("RECOVERY_UNAVAILABLE", "stored paths are invalid")
    return tuple(cast(list[str], object_values))


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
