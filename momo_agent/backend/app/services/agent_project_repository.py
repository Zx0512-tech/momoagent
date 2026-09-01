from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
from typing import Any


class EngineeringProjectRepository:
    """Project/Workspace 持久化边界。

    先与现有 AgentRepository 共用同一个 SQLite 文件，但使用独立表，避免 PR4
    改写会话、运行和证据表。Project 通过 sessionIds 关联现有会话；后续跨 run
    context 只需从 Project 解析关联会话即可。
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            self._initialize(connection)

    def create_project(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as connection:
            self._initialize(connection)
            connection.execute(
                'INSERT INTO agent_projects(project_id, owner, updated_at, payload_json) VALUES (?, ?, ?, ?)',
                (
                    payload['projectId'],
                    payload['ownerId'],
                    payload['updatedAt'],
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            connection.commit()
        return payload

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            self._initialize(connection)
            row = connection.execute(
                'SELECT payload_json FROM agent_projects WHERE project_id = ?',
                (project_id,),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def list_projects(self, owner: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            self._initialize(connection)
            rows = connection.execute(
                'SELECT payload_json FROM agent_projects WHERE owner = ? ORDER BY updated_at DESC',
                (owner,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def update_workspace(
        self,
        project_id: str,
        patch: dict[str, Any],
        *,
        updated_at: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            self._initialize(connection)
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute(
                'SELECT payload_json FROM agent_projects WHERE project_id = ?',
                (project_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                return None
            payload = json.loads(row[0])
            workspace = dict(payload.get('workspace') or {})
            workspace.update(patch)
            payload['workspace'] = workspace
            payload['workspaceRevision'] = int(payload.get('workspaceRevision') or 0) + 1
            payload['updatedAt'] = updated_at
            connection.execute(
                'UPDATE agent_projects SET updated_at = ?, payload_json = ? WHERE project_id = ?',
                (updated_at, json.dumps(payload, ensure_ascii=False), project_id),
            )
            connection.commit()
        return payload

    def attach_session(
        self,
        project_id: str,
        session_id: str,
        *,
        updated_at: str,
    ) -> dict[str, Any] | None:
        """原子绑定会话，并保证一个 session 只属于一个 Project。"""
        with self._connect() as connection:
            self._initialize(connection)
            connection.execute('BEGIN IMMEDIATE')
            rows = connection.execute(
                'SELECT project_id, payload_json FROM agent_projects'
            ).fetchall()
            target: dict[str, Any] | None = None
            for candidate_id, raw_payload in rows:
                payload = json.loads(raw_payload)
                session_ids = [str(item) for item in payload.get('sessionIds') or []]
                if str(candidate_id) == project_id:
                    target = payload
                if session_id in session_ids and str(candidate_id) != project_id:
                    connection.rollback()
                    raise ValueError(
                        f'会话 {session_id} 已绑定工程项目 {candidate_id}，不能重复加入其他项目。'
                    )
            if target is None:
                connection.rollback()
                return None
            session_ids = [str(item) for item in target.get('sessionIds') or []]
            if session_id not in session_ids:
                session_ids.append(session_id)
                target['sessionIds'] = session_ids
                target['updatedAt'] = updated_at
                connection.execute(
                    'UPDATE agent_projects SET updated_at = ?, payload_json = ? WHERE project_id = ?',
                    (updated_at, json.dumps(target, ensure_ascii=False), project_id),
                )
            connection.commit()
        return target

    def project_for_session(self, session_id: str, *, owner: str | None = None) -> dict[str, Any] | None:
        with self._connect() as connection:
            self._initialize(connection)
            if owner is None:
                rows = connection.execute('SELECT payload_json FROM agent_projects').fetchall()
            else:
                rows = connection.execute(
                    'SELECT payload_json FROM agent_projects WHERE owner = ?',
                    (owner,),
                ).fetchall()
        for row in rows:
            payload = json.loads(row[0])
            if session_id in {str(item) for item in payload.get('sessionIds') or []}:
                return payload
        return None

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        try:
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('PRAGMA synchronous=FULL')
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _initialize(connection: sqlite3.Connection) -> None:
        connection.executescript(
            '''
            CREATE TABLE IF NOT EXISTS agent_projects (
                project_id TEXT PRIMARY KEY,
                owner TEXT NOT NULL DEFAULT 'local',
                updated_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_agent_projects_owner_updated
                ON agent_projects(owner, updated_at DESC);
            '''
        )
