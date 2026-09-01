from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
from threading import Lock, RLock
from typing import Any


class RunRevisionConflict(Exception):
    """run 的读-改-写与其他请求交叉，拒绝静默覆盖。"""

    def __init__(self, run_id: str, *, expected: int, actual: int) -> None:
        self.run_id = run_id
        self.expected = expected
        self.actual = actual
        super().__init__(
            f'运行 {run_id} 已被其他请求更新（expected revision {expected}, actual {actual}），请重新读取后重试。'
        )


# 未接入认证时的单用户缺省身份；旧库迁移出的历史数据同样归属它，
# 保证单人本地部署行为不变。
DEFAULT_OWNER = 'local'


# AgentRepository 每次请求都会新建实例，互斥必须是进程级的。
# 键为 run_id；平台整体是单进程部署假设（platform_store 同样依赖进程内 RLock）。
_RUN_LOCKS: dict[str, RLock] = {}
_RUN_LOCKS_GUARD = Lock()


@contextmanager
def run_state_lock(run_id: str) -> Iterator[None]:
    """串行化同一 run 的读-改-写序列；可重入以兼容嵌套调用。"""
    with _RUN_LOCKS_GUARD:
        lock = _RUN_LOCKS.setdefault(str(run_id), RLock())
    with lock:
        yield


class AgentRepository:
    """在平台 SQLite 中独立保存智能体状态，不改动现有平台表。"""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            self._initialize(connection)

    def save_session(self, payload: dict[str, Any]) -> None:
        owner = str(payload.get('ownerId') or DEFAULT_OWNER)
        payload['ownerId'] = owner
        with self._connect() as connection:
            self._initialize(connection)
            connection.execute(
                'INSERT INTO agent_sessions(session_id, owner, updated_at, payload_json) VALUES (?, ?, ?, ?) '
                'ON CONFLICT(session_id) DO UPDATE SET owner=excluded.owner, '
                'updated_at=excluded.updated_at, payload_json=excluded.payload_json',
                (
                    payload['sessionId'],
                    owner,
                    str(payload.get('updatedAt') or payload.get('createdAt') or ''),
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            connection.commit()

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        return self._get('agent_sessions', 'session_id', session_id)

    def update_session_title(self, session_id: str, title: str) -> bool:
        """在事务内只更新会话标题，不改变活动时间或覆盖同期写入。"""
        with self._connect() as connection:
            self._initialize(connection)
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute(
                'SELECT payload_json FROM agent_sessions WHERE session_id = ?',
                (session_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                return False
            payload = json.loads(row[0])
            payload['title'] = title
            connection.execute(
                'UPDATE agent_sessions SET payload_json = ? WHERE session_id = ?',
                (json.dumps(payload, ensure_ascii=False), session_id),
            )
            connection.commit()
        return True

    def list_sessions(self, owner: str | None = None) -> list[dict[str, Any]]:
        if owner is None:
            return self._list('agent_sessions', 'updated_at DESC')
        with self._connect() as connection:
            self._initialize(connection)
            rows = connection.execute(
                'SELECT payload_json FROM agent_sessions WHERE owner = ? ORDER BY updated_at DESC',
                (owner,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def delete_session_history(self, session_id: str) -> bool:
        """删除会话及可见消息，保留运行和工具证据供审计。"""
        with self._connect() as connection:
            self._initialize(connection)
            connection.execute('BEGIN IMMEDIATE')
            existing = connection.execute(
                'SELECT 1 FROM agent_sessions WHERE session_id = ?',
                (session_id,),
            ).fetchone()
            if existing is None:
                connection.rollback()
                return False
            connection.execute(
                'DELETE FROM agent_messages WHERE session_id = ?',
                (session_id,),
            )
            connection.execute(
                'DELETE FROM agent_sessions WHERE session_id = ?',
                (session_id,),
            )
            connection.commit()
        return True

    def add_message(self, payload: dict[str, Any]) -> None:
        with self._connect() as connection:
            self._initialize(connection)
            connection.execute(
                'INSERT INTO agent_messages(message_id, session_id, created_at, payload_json) VALUES (?, ?, ?, ?)',
                (
                    payload['messageId'],
                    payload['sessionId'],
                    payload['createdAt'],
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            connection.commit()

    def has_messages(self, session_id: str) -> bool:
        """检查会话是否已有消息，避免读取并解析完整历史。"""
        with self._connect() as connection:
            self._initialize(connection)
            row = connection.execute(
                'SELECT 1 FROM agent_messages WHERE session_id = ? LIMIT 1',
                (session_id,),
            ).fetchone()
        return row is not None

    def list_messages(self, session_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            self._initialize(connection)
            rows = connection.execute(
                'SELECT payload_json FROM agent_messages WHERE session_id = ? ORDER BY position',
                (session_id,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def save_run(self, payload: dict[str, Any]) -> None:
        """CAS 落库：payload 携带的 revision 必须等于库内当前值。

        run 是整体 JSON 覆盖写；没有版本校验时并发读-改-写会静默丢更新。
        同一 dict 对象在一次流程内多次保存会随保存原地递增 revision，
        跨请求的陈旧副本则会触发 RunRevisionConflict。
        """
        with self._connect() as connection:
            self._initialize(connection)
            connection.execute('BEGIN IMMEDIATE')
            try:
                row = connection.execute(
                    "SELECT json_extract(payload_json, '$.revision') FROM agent_runs WHERE run_id = ?",
                    (payload['runId'],),
                ).fetchone()
                expected = int(payload.get('revision') or 0)
                if row is not None:
                    actual = int(row[0] or 0)
                    if expected != actual:
                        raise RunRevisionConflict(
                            str(payload['runId']),
                            expected=expected,
                            actual=actual,
                        )
                payload['revision'] = expected + 1
                owner = payload.get('ownerId')
                if not owner:
                    # 运行归属继承自会话；创建路径众多，统一在落库时解析。
                    session_row = connection.execute(
                        'SELECT owner FROM agent_sessions WHERE session_id = ?',
                        (payload.get('sessionId'),),
                    ).fetchone()
                    owner = str((session_row or [None])[0] or DEFAULT_OWNER)
                    payload['ownerId'] = owner
                connection.execute(
                    'INSERT INTO agent_runs(run_id, session_id, owner, updated_at, payload_json) VALUES (?, ?, ?, ?, ?) '
                    'ON CONFLICT(run_id) DO UPDATE SET session_id=excluded.session_id, owner=excluded.owner, '
                    'updated_at=excluded.updated_at, payload_json=excluded.payload_json',
                    (
                        payload['runId'],
                        payload['sessionId'],
                        str(owner),
                        str(payload.get('updatedAt') or payload.get('createdAt') or ''),
                        json.dumps(payload, ensure_ascii=False),
                    ),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self._get('agent_runs', 'run_id', run_id)

    def list_runs(self, session_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            self._initialize(connection)
            rows = connection.execute(
                'SELECT payload_json FROM agent_runs WHERE session_id = ? ORDER BY updated_at DESC',
                (session_id,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def list_all_runs(self, owner: str | None = None) -> list[dict[str, Any]]:
        """按更新时间倒序返回所有会话的运行，供全局结果目录使用。"""
        if owner is None:
            return self._list('agent_runs', 'updated_at DESC')
        with self._connect() as connection:
            self._initialize(connection)
            rows = connection.execute(
                'SELECT payload_json FROM agent_runs WHERE owner = ? ORDER BY updated_at DESC',
                (owner,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def list_foreign_run_ids(self, owner: str) -> set[str]:
        """返回不属于该 owner 的 run_id 集合，供制品可见性过滤。"""
        with self._connect() as connection:
            self._initialize(connection)
            rows = connection.execute(
                'SELECT run_id FROM agent_runs WHERE owner != ?',
                (owner,),
            ).fetchall()
        return {str(row[0]) for row in rows}

    def find_pending_clarification_run(self, session_id: str) -> dict[str, Any] | None:
        """返回该会话最近一条仍等待用户补充信息的运行记录。"""
        pending = [
            run
            for run in self.list_runs(session_id)
            if run.get('status') == 'NEEDS_CLARIFICATION'
        ]
        if not pending:
            return None
        return max(
            pending,
            key=lambda run: str(run.get('updatedAt') or run.get('createdAt') or ''),
        )

    def find_pending_approval_run(self, session_id: str) -> dict[str, Any] | None:
        """返回该会话最近一条等待人工审批的工程运行。"""
        for run in self.list_runs(session_id):
            if run.get('status') == 'WAITING_APPROVAL' and run.get('pendingApprovalId'):
                return run
        return None

    def save_step(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as connection:
            self._initialize(connection)
            existing = connection.execute(
                'SELECT payload_json FROM agent_steps WHERE idempotency_key = ?',
                (payload['idempotencyKey'],),
            ).fetchone()
            if existing:
                return json.loads(existing[0])
            connection.execute(
                'INSERT INTO agent_steps(step_id, run_id, idempotency_key, created_at, payload_json) '
                'VALUES (?, ?, ?, ?, ?)',
                (
                    payload['stepId'],
                    payload['runId'],
                    payload['idempotencyKey'],
                    payload['createdAt'],
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            connection.commit()
        return payload

    def list_steps(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            self._initialize(connection)
            rows = connection.execute(
                'SELECT payload_json FROM agent_steps WHERE run_id = ? ORDER BY position',
                (run_id,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def save_approval(self, payload: dict[str, Any]) -> None:
        self._upsert('agent_approvals', 'approval_id', payload['approvalId'], payload)

    def get_approval(self, approval_id: str) -> dict[str, Any] | None:
        return self._get('agent_approvals', 'approval_id', approval_id)

    def list_approvals(self, run_ids: set[str] | None = None) -> list[dict[str, Any]]:
        approvals = self._list('agent_approvals', 'updated_at ASC')
        if run_ids is None:
            return approvals
        return [approval for approval in approvals if approval.get('runId') in run_ids]

    def save_tool_call(self, payload: dict[str, Any]) -> None:
        """保存可恢复的工具调用状态；同一 toolCallId 只更新一条记录。"""
        with self._connect() as connection:
            self._initialize(connection)
            connection.execute(
                'INSERT INTO agent_tool_calls(tool_call_id, run_id, updated_at, payload_json) '
                'VALUES (?, ?, ?, ?) '
                'ON CONFLICT(tool_call_id) DO UPDATE SET run_id=excluded.run_id, '
                'updated_at=excluded.updated_at, payload_json=excluded.payload_json',
                (
                    payload['toolCallId'],
                    payload['runId'],
                    str(payload.get('updatedAt') or payload.get('createdAt') or ''),
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            connection.commit()

    def save_message(self, payload: dict[str, Any]) -> None:
        """更新已落库消息的 Harness 投影字段，保持用户可见内容不变。"""
        with self._connect() as connection:
            self._initialize(connection)
            connection.execute(
                'UPDATE agent_messages SET payload_json = ? WHERE message_id = ?',
                (json.dumps(payload, ensure_ascii=False), payload['messageId']),
            )
            connection.commit()

    def get_tool_call(self, tool_call_id: str) -> dict[str, Any] | None:
        return self._get('agent_tool_calls', 'tool_call_id', tool_call_id)

    def list_tool_calls(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            self._initialize(connection)
            rows = connection.execute(
                'SELECT payload_json FROM agent_tool_calls WHERE run_id = ? ORDER BY position',
                (run_id,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def save_import(self, payload: dict[str, Any]) -> None:
        self._upsert('load_imports', 'import_id', payload['importId'], payload)

    def get_import(self, import_id: str) -> dict[str, Any] | None:
        return self._get('load_imports', 'import_id', import_id)

    def find_import_by_file(self, file_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            self._initialize(connection)
            rows = connection.execute('SELECT payload_json FROM load_imports').fetchall()
        for row in rows:
            payload = json.loads(row[0])
            if payload.get('fileId') == file_id:
                return payload
        return None

    def _upsert(self, table: str, id_column: str, identifier: str, payload: dict[str, Any]) -> None:
        updated_at = str(payload.get('updatedAt') or payload.get('createdAt') or '')
        with self._connect() as connection:
            self._initialize(connection)
            connection.execute(
                f'INSERT INTO {table}({id_column}, updated_at, payload_json) VALUES (?, ?, ?) '
                f'ON CONFLICT({id_column}) DO UPDATE SET updated_at=excluded.updated_at, payload_json=excluded.payload_json',
                (identifier, updated_at, json.dumps(payload, ensure_ascii=False)),
            )
            connection.commit()

    def _get(self, table: str, id_column: str, identifier: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            self._initialize(connection)
            row = connection.execute(
                f'SELECT payload_json FROM {table} WHERE {id_column} = ?',
                (identifier,),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def _list(self, table: str, order_by: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            self._initialize(connection)
            rows = connection.execute(f'SELECT payload_json FROM {table} ORDER BY {order_by}').fetchall()
        return [json.loads(row[0]) for row in rows]

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
            CREATE TABLE IF NOT EXISTS agent_sessions (
                session_id TEXT PRIMARY KEY,
                owner TEXT NOT NULL DEFAULT 'local',
                updated_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_messages (
                position INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT UNIQUE NOT NULL,
                session_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_runs (
                run_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL DEFAULT '',
                owner TEXT NOT NULL DEFAULT 'local',
                updated_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_steps (
                position INTEGER PRIMARY KEY AUTOINCREMENT,
                step_id TEXT UNIQUE NOT NULL,
                run_id TEXT NOT NULL,
                idempotency_key TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_approvals (
                approval_id TEXT PRIMARY KEY, updated_at TEXT NOT NULL, payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_tool_calls (
                position INTEGER PRIMARY KEY AUTOINCREMENT,
                tool_call_id TEXT UNIQUE NOT NULL,
                run_id TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_agent_tool_calls_run_id
                ON agent_tool_calls(run_id, position);
            CREATE TABLE IF NOT EXISTS load_imports (
                import_id TEXT PRIMARY KEY, updated_at TEXT NOT NULL, payload_json TEXT NOT NULL
            );
            '''
        )
        # 兼容首次创建后补充 session_id / owner 的旧本地开发表；
        # 旧数据统一归属 DEFAULT_OWNER，单用户部署行为不变。
        run_columns = {row[1] for row in connection.execute('PRAGMA table_info(agent_runs)')}
        if 'session_id' not in run_columns:
            connection.execute("ALTER TABLE agent_runs ADD COLUMN session_id TEXT NOT NULL DEFAULT ''")
        if 'owner' not in run_columns:
            connection.execute("ALTER TABLE agent_runs ADD COLUMN owner TEXT NOT NULL DEFAULT 'local'")
        session_columns = {row[1] for row in connection.execute('PRAGMA table_info(agent_sessions)')}
        if 'owner' not in session_columns:
            connection.execute("ALTER TABLE agent_sessions ADD COLUMN owner TEXT NOT NULL DEFAULT 'local'")
