from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from app.core.json_safety import strict_json_dumps


class SQLitePlatformRepository:
    """持久化平台状态，制品字节保存在数据库旁的文件目录。"""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.artifact_root = database_path.parent / 'artifacts'

    def exists(self) -> bool:
        return self.database_path.exists()

    @staticmethod
    def _bump_revision(connection: sqlite3.Connection) -> int:
        """状态版本戳：每次持久化写入递增，供 refresh 判断是否需要全量重载。"""
        connection.execute(
            "INSERT INTO settings(key, value_json) VALUES ('state_revision', '1') "
            "ON CONFLICT(key) DO UPDATE SET value_json = CAST(CAST(value_json AS INTEGER) + 1 AS TEXT)",
        )
        row = connection.execute(
            "SELECT value_json FROM settings WHERE key = 'state_revision'"
        ).fetchone()
        return int(row[0])

    def state_revision(self) -> int | None:
        """返回当前持久化版本；旧库尚无版本戳时返回 None（调用方应全量重载）。"""
        if not self.exists():
            return None
        with self._connect() as connection:
            self._initialize(connection)
            row = connection.execute(
                "SELECT value_json FROM settings WHERE key = 'state_revision'"
            ).fetchone()
        return int(row[0]) if row else None

    def load(self) -> dict[str, Any] | None:
        if not self.exists():
            return None
        with self._connect() as connection:
            self._initialize(connection)
            jobs = [json.loads(row[0]) for row in connection.execute('SELECT payload_json FROM jobs ORDER BY position')]
            artifacts = []
            for artifact_json, preview_json, content_path in connection.execute(
                'SELECT artifact_json, preview_json, content_path FROM artifacts ORDER BY position'
            ):
                raw_path = self.database_path.parent / content_path
                artifacts.append({
                    'artifact': json.loads(artifact_json),
                    'preview': json.loads(preview_json),
                    'content': raw_path.read_bytes(),
                })
            row = connection.execute(
                "SELECT value_json FROM settings WHERE key = 'engineering_config'"
            ).fetchone()
            engineering_config = json.loads(row[0]) if row else None
        return {'jobs': jobs, 'artifacts': artifacts, 'engineeringConfig': engineering_config}

    def save(
        self,
        *,
        jobs: list[dict[str, Any]],
        artifacts: list[dict[str, Any]],
        engineering_config: dict[str, Any] | None,
        update_engineering_config: bool = True,
    ) -> int:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        stored_artifacts: list[tuple[int, str, str, str, str]] = []
        for position, record in enumerate(artifacts):
            artifact_id = str(record['artifact']['artifactId'])
            relative_path = Path('artifacts') / f'{artifact_id}.bin'
            content_path = self.database_path.parent / relative_path
            content = record['content']
            if not content_path.exists() or content_path.read_bytes() != content:
                temporary_path = content_path.with_suffix('.tmp')
                temporary_path.write_bytes(content)
                temporary_path.replace(content_path)
            stored_artifacts.append((
                position,
                artifact_id,
                strict_json_dumps(record['artifact']),
                strict_json_dumps(record['preview']),
                relative_path.as_posix(),
            ))

        with self._connect() as connection:
            self._initialize(connection)
            connection.execute('BEGIN IMMEDIATE')
            existing_jobs = {
                str(job_id): (int(position), json.loads(payload_json))
                for position, job_id, payload_json in connection.execute(
                    'SELECT position, job_id, payload_json FROM jobs'
                )
            }
            next_job_position = max((position for position, _ in existing_jobs.values()), default=-1) + 1
            terminal_statuses = {'SUCCEEDED', 'FAILED', 'CANCELLED'}
            stored_jobs = []
            for job in jobs:
                job_id = str(job['jobId'])
                existing = existing_jobs.get(job_id)
                stored_job = job
                if (
                    existing is not None
                    and existing[1].get('status') in terminal_statuses
                    and job.get('status') not in terminal_statuses
                ):
                    stored_job = existing[1]
                position = existing[0] if existing is not None else next_job_position
                if existing is None:
                    next_job_position += 1
                stored_jobs.append((position, job_id, strict_json_dumps(stored_job)))
            connection.executemany(
                'INSERT INTO jobs(position, job_id, payload_json) VALUES (?, ?, ?) '
                'ON CONFLICT(job_id) DO UPDATE SET position=excluded.position, payload_json=excluded.payload_json',
                stored_jobs,
            )
            connection.executemany(
                'INSERT INTO artifacts(position, artifact_id, artifact_json, preview_json, content_path) '
                'VALUES (?, ?, ?, ?, ?) ON CONFLICT(artifact_id) DO UPDATE SET '
                'artifact_json=excluded.artifact_json, preview_json=excluded.preview_json, '
                'content_path=excluded.content_path',
                stored_artifacts,
            )
            if update_engineering_config:
                connection.execute(
                    "INSERT INTO settings(key, value_json) VALUES ('engineering_config', ?) "
                    'ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json',
                    (strict_json_dumps(engineering_config),),
                )
            revision = self._bump_revision(connection)
            connection.commit()
        return revision

    def update_job(self, job: dict[str, Any], *, expected_status: str | None = None) -> bool:
        """整条写回任务记录，返回是否真的写入。

        ``expected_status`` 给出时改成条件更新：只有数据库里的状态仍等于该值
        才写。worker 心跳进程与执行子进程分别持有各自的 Job 副本，读到 RUNNING
        之后执行进程可能已经写入终态，这时盲写会把 SUCCEEDED 覆盖回 RUNNING，
        最终被误判成 EXECUTOR_INCOMPLETE。条件更新让落后的一方拿到 0 行并放弃。
        """

        with self._connect() as connection:
            self._initialize(connection)
            job_id = str(job['jobId'])
            payload = strict_json_dumps(job)
            if expected_status is None:
                cursor = connection.execute(
                    'UPDATE jobs SET payload_json = ? WHERE job_id = ?',
                    (payload, job_id),
                )
            else:
                cursor = connection.execute(
                    'UPDATE jobs SET payload_json = ? '
                    "WHERE job_id = ? AND json_extract(payload_json, '$.status') = ?",
                    (payload, job_id, expected_status),
                )
            written = cursor.rowcount == 1
            if not written:
                exists = connection.execute(
                    'SELECT 1 FROM jobs WHERE job_id = ?', (job_id,)
                ).fetchone()
                if not exists:
                    raise KeyError(f'任务 {job_id} 不存在')
            else:
                # update_job 绕过 save() 直接落库（worker 心跳/终态写入），
                # 同样要推进版本戳，否则 refresh 会漏掉这些变更。
                self._bump_revision(connection)
            connection.commit()
        return written

    def load_job(self, job_id: str) -> dict[str, Any] | None:
        if not self.exists():
            return None
        with self._connect() as connection:
            self._initialize(connection)
            row = connection.execute(
                'SELECT payload_json FROM jobs WHERE job_id = ?',
                (job_id,),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def check_read_write(self) -> None:
        """验证 SQLite 可读写，但不修改持久化业务数据。"""
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            result = connection.execute('PRAGMA quick_check').fetchone()
            if not result or result[0] != 'ok':
                raise sqlite3.DatabaseError(f'SQLite quick_check failed: {result}')
            connection.execute('BEGIN IMMEDIATE')
            try:
                connection.execute('CREATE TEMP TABLE readiness_probe(value INTEGER NOT NULL)')
                connection.execute('INSERT INTO readiness_probe(value) VALUES (1)')
                value = connection.execute('SELECT value FROM readiness_probe').fetchone()
                if value != (1,):
                    raise sqlite3.DatabaseError('SQLite readiness probe returned unexpected data')
            finally:
                connection.rollback()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        try:
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('PRAGMA synchronous=FULL')
            yield connection
        finally:
            connection.close()

    def _initialize(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            '''
            CREATE TABLE IF NOT EXISTS jobs (
                position INTEGER NOT NULL,
                job_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS artifacts (
                position INTEGER NOT NULL,
                artifact_id TEXT PRIMARY KEY,
                artifact_json TEXT NOT NULL,
                preview_json TEXT NOT NULL,
                content_path TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL
            );
            '''
        )
