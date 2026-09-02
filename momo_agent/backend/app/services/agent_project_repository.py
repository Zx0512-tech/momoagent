from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class WorkspaceRevisionConflict(ValueError):
    """Raised when a caller tries to write a stale Engineering Workspace snapshot."""

    def __init__(self, *, expected: int, current: int) -> None:
        self.expected = expected
        self.current = current
        super().__init__(f'Workspace revision conflict: expected {expected}, current {current}')


class EngineeringProjectRepository:
    """Project/Workspace persistence boundary.

    Project payload remains backward-compatible JSON, while Project -> Session membership is also
    materialized into a normalized table. The database therefore owns the one-session-to-one-project
    invariant instead of relying on a full JSON scan in application code.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            self._initialize(connection)
            self._backfill_project_sessions(connection)

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
            if row is None:
                return None
            return self._with_session_ids(connection, json.loads(row[0]))

    def list_projects(self, owner: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            self._initialize(connection)
            rows = connection.execute(
                'SELECT payload_json FROM agent_projects WHERE owner = ? ORDER BY updated_at DESC',
                (owner,),
            ).fetchall()
            return [self._with_session_ids(connection, json.loads(row[0])) for row in rows]

    def update_workspace(
        self,
        project_id: str,
        patch: dict[str, Any],
        *,
        updated_at: str,
        expected_revision: int | None = None,
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
            current_revision = int(payload.get('workspaceRevision') or 0)
            if expected_revision is not None and current_revision != expected_revision:
                connection.rollback()
                raise WorkspaceRevisionConflict(expected=expected_revision, current=current_revision)

            workspace = dict(payload.get('workspace') or {})
            changed = {key: value for key, value in patch.items() if workspace.get(key) != value}
            if not changed:
                connection.commit()
                return self._with_session_ids(connection, payload)

            workspace.update(changed)
            payload['workspace'] = workspace
            payload['workspaceRevision'] = current_revision + 1
            payload['updatedAt'] = updated_at
            connection.execute(
                'UPDATE agent_projects SET updated_at = ?, payload_json = ? WHERE project_id = ?',
                (updated_at, json.dumps(payload, ensure_ascii=False), project_id),
            )
            connection.commit()
            return self._with_session_ids(connection, payload)

    def attach_session(
        self,
        project_id: str,
        session_id: str,
        *,
        updated_at: str,
    ) -> dict[str, Any] | None:
        """Atomically bind a session; SQLite enforces that one session belongs to one Project."""

        with self._connect() as connection:
            self._initialize(connection)
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute(
                'SELECT owner, payload_json FROM agent_projects WHERE project_id = ?',
                (project_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                return None

            owner, raw_payload = row
            target = json.loads(raw_payload)
            existing = connection.execute(
                'SELECT project_id FROM agent_project_sessions WHERE session_id = ?',
                (session_id,),
            ).fetchone()
            if existing is not None and str(existing[0]) != project_id:
                connection.rollback()
                raise ValueError(
                    f'会话 {session_id} 已绑定工程项目 {existing[0]}，不能重复加入其他项目。'
                )

            if existing is None:
                connection.execute(
                    'INSERT INTO agent_project_sessions(session_id, project_id, owner, created_at) '
                    'VALUES (?, ?, ?, ?)',
                    (session_id, project_id, owner, updated_at),
                )

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
            return self._with_session_ids(connection, target)

    def project_for_session(self, session_id: str, *, owner: str | None = None) -> dict[str, Any] | None:
        with self._connect() as connection:
            self._initialize(connection)
            if owner is None:
                row = connection.execute(
                    'SELECT p.payload_json FROM agent_project_sessions ps '
                    'JOIN agent_projects p ON p.project_id = ps.project_id '
                    'WHERE ps.session_id = ?',
                    (session_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    'SELECT p.payload_json FROM agent_project_sessions ps '
                    'JOIN agent_projects p ON p.project_id = ps.project_id '
                    'WHERE ps.session_id = ? AND ps.owner = ?',
                    (session_id, owner),
                ).fetchone()
            return self._with_session_ids(connection, json.loads(row[0])) if row else None

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        try:
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('PRAGMA synchronous=FULL')
            connection.execute('PRAGMA foreign_keys=ON')
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

            CREATE TABLE IF NOT EXISTS agent_project_sessions (
                session_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                owner TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES agent_projects(project_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_agent_project_sessions_project
                ON agent_project_sessions(project_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_agent_project_sessions_owner
                ON agent_project_sessions(owner, project_id);
            '''
        )

    @staticmethod
    def _with_session_ids(connection: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
        project_id = str(payload.get('projectId') or '')
        if not project_id:
            return payload
        rows = connection.execute(
            'SELECT session_id FROM agent_project_sessions WHERE project_id = ? '
            'ORDER BY created_at, session_id',
            (project_id,),
        ).fetchall()
        hydrated = dict(payload)
        hydrated['sessionIds'] = [str(row[0]) for row in rows]
        return hydrated

    @staticmethod
    def _backfill_project_sessions(connection: sqlite3.Connection) -> None:
        """Migrate PR4 JSON memberships into the normalized relation table once, fail closed on drift."""

        rows = connection.execute(
            'SELECT project_id, owner, updated_at, payload_json FROM agent_projects ORDER BY project_id'
        ).fetchall()
        seen: dict[str, str] = {}
        for project_id, owner, updated_at, raw_payload in rows:
            payload = json.loads(raw_payload)
            for raw_session_id in payload.get('sessionIds') or []:
                session_id = str(raw_session_id)
                previous_project = seen.get(session_id)
                if previous_project is not None and previous_project != str(project_id):
                    raise RuntimeError(
                        f'Project/Session invariant violation: {session_id} belongs to both '
                        f'{previous_project} and {project_id}'
                    )
                seen[session_id] = str(project_id)
                existing = connection.execute(
                    'SELECT project_id FROM agent_project_sessions WHERE session_id = ?',
                    (session_id,),
                ).fetchone()
                if existing is not None and str(existing[0]) != str(project_id):
                    raise RuntimeError(
                        f'Project/Session invariant violation: {session_id} relation points to '
                        f'{existing[0]} but payload points to {project_id}'
                    )
                connection.execute(
                    'INSERT OR IGNORE INTO agent_project_sessions(session_id, project_id, owner, created_at) '
                    'VALUES (?, ?, ?, ?)',
                    (session_id, project_id, owner, updated_at),
                )
        connection.commit()
