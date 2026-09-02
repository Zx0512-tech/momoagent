from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.services.agent_project_repository import (
    EngineeringProjectRepository,
    WorkspaceRevisionConflict,
)
from app.services.agent_repository import DEFAULT_OWNER
from app.services.agent_service import agent_service
from app.services.platform_store import gen_id, platform_store, utc_now


DEFAULT_ENGINEERING_WORKSPACE: dict[str, Any] = {
    'schemaVersion': 1,
    'modelArtifactId': None,
    'modelFileName': None,
    'modelSha256': None,
    'solver': None,
    'loadKind': None,
    'loadArtifactId': None,
    'loadSha256': None,
    'damperType': None,
    'selectedLayoutId': None,
    'responseIds': [],
    'optimizationProfile': 'STANDARD',
}


class EngineeringProjectService:
    """Engineering Project / Workspace aggregate service."""

    def repository(self) -> EngineeringProjectRepository:
        return EngineeringProjectRepository(platform_store.state_path)

    @staticmethod
    def _ensure_owner(payload: dict[str, Any], owner: str) -> None:
        if str(payload.get('ownerId') or DEFAULT_OWNER) != owner:
            raise HTTPException(
                status_code=404,
                detail={'code': 'PROJECT_NOT_FOUND', 'message': '工程项目不存在'},
            )

    def _required_project(self, project_id: str, owner: str) -> dict[str, Any]:
        project = self.repository().get_project(project_id)
        if project is None:
            raise HTTPException(
                status_code=404,
                detail={'code': 'PROJECT_NOT_FOUND', 'message': f'工程项目 {project_id} 不存在'},
            )
        self._ensure_owner(project, owner)
        return project

    def create_project(
        self,
        name: str,
        *,
        description: str = '',
        workspace: dict[str, Any] | None = None,
        owner: str = DEFAULT_OWNER,
    ) -> dict[str, Any]:
        now = utc_now()
        resolved_workspace = dict(DEFAULT_ENGINEERING_WORKSPACE)
        resolved_workspace.update(workspace or {})
        payload = {
            'projectId': gen_id('agp'),
            'ownerId': owner,
            'name': name,
            'description': description,
            'status': 'ACTIVE',
            'workspace': resolved_workspace,
            'workspaceRevision': 1,
            'sessionIds': [],
            'createdAt': now,
            'updatedAt': now,
        }
        self.repository().create_project(payload)
        return self._project_summary(payload)

    def list_projects(self, owner: str = DEFAULT_OWNER) -> list[dict[str, Any]]:
        return [self._project_summary(project) for project in self.repository().list_projects(owner)]

    def get_project(self, project_id: str, owner: str = DEFAULT_OWNER) -> dict[str, Any]:
        project = self._required_project(project_id, owner)
        agent_repository = agent_service.repository()
        sessions: list[dict[str, Any]] = []
        runs: list[dict[str, Any]] = []
        for session_id in project.get('sessionIds') or []:
            session = agent_repository.get_session(str(session_id))
            if session is None:
                continue
            if str(session.get('ownerId') or DEFAULT_OWNER) != owner:
                continue
            session_runs = agent_repository.list_runs(str(session_id))
            sessions.append({
                **session,
                'runCount': len(session_runs),
            })
            runs.extend(self._run_summary(run) for run in session_runs)
        return {
            **project,
            'sessionCount': len(sessions),
            'runCount': len(runs),
            'sessions': sessions,
            'runs': sorted(
                runs,
                key=lambda item: str(item.get('updatedAt') or item.get('createdAt') or ''),
                reverse=True,
            ),
        }

    def update_workspace(
        self,
        project_id: str,
        patch: dict[str, Any],
        owner: str = DEFAULT_OWNER,
        *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        self._required_project(project_id, owner)
        try:
            updated = self.repository().update_workspace(
                project_id,
                patch,
                updated_at=utc_now(),
                expected_revision=expected_revision,
            )
        except WorkspaceRevisionConflict as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    'code': 'WORKSPACE_REVISION_CONFLICT',
                    'message': (
                        f'Workspace 已被其他操作更新：请求基于 revision {exc.expected}，'
                        f'当前为 revision {exc.current}。请刷新后重新确认修改。'
                    ),
                    'expectedRevision': exc.expected,
                    'currentRevision': exc.current,
                },
            ) from exc
        if updated is None:
            raise HTTPException(
                status_code=404,
                detail={'code': 'PROJECT_NOT_FOUND', 'message': f'工程项目 {project_id} 不存在'},
            )
        return self._project_summary(updated)

    def create_session(
        self,
        project_id: str,
        title: str,
        owner: str = DEFAULT_OWNER,
    ) -> dict[str, Any]:
        self._required_project(project_id, owner)
        session = agent_service.create_session(title, owner=owner)
        self.attach_session(project_id, str(session['sessionId']), owner=owner)
        return {
            **session,
            'projectId': project_id,
        }

    def attach_session(
        self,
        project_id: str,
        session_id: str,
        owner: str = DEFAULT_OWNER,
    ) -> dict[str, Any]:
        self._required_project(project_id, owner)
        session = agent_service.repository().get_session(session_id)
        if session is None or str(session.get('ownerId') or DEFAULT_OWNER) != owner:
            raise HTTPException(
                status_code=404,
                detail={'code': 'SESSION_NOT_FOUND', 'message': f'会话 {session_id} 不存在'},
            )
        try:
            project = self.repository().attach_session(
                project_id,
                session_id,
                updated_at=utc_now(),
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail={'code': 'SESSION_ALREADY_BOUND', 'message': str(exc)},
            ) from exc
        if project is None:
            raise HTTPException(
                status_code=404,
                detail={'code': 'PROJECT_NOT_FOUND', 'message': f'工程项目 {project_id} 不存在'},
            )
        return {
            'projectId': project_id,
            'sessionId': session_id,
            'attached': True,
        }

    def project_for_session(
        self,
        session_id: str,
        owner: str = DEFAULT_OWNER,
    ) -> dict[str, Any] | None:
        return self.repository().project_for_session(session_id, owner=owner)

    def _project_summary(self, project: dict[str, Any]) -> dict[str, Any]:
        agent_repository = agent_service.repository()
        session_ids = [str(item) for item in project.get('sessionIds') or []]
        run_count = 0
        visible_sessions = 0
        owner = str(project.get('ownerId') or DEFAULT_OWNER)
        for session_id in session_ids:
            session = agent_repository.get_session(session_id)
            if session is None or str(session.get('ownerId') or DEFAULT_OWNER) != owner:
                continue
            visible_sessions += 1
            run_count += len(agent_repository.list_runs(session_id))
        return {
            **project,
            'sessionCount': visible_sessions,
            'runCount': run_count,
        }

    @staticmethod
    def _run_summary(run: dict[str, Any]) -> dict[str, Any]:
        return {
            key: run.get(key)
            for key in (
                'runId',
                'sessionId',
                'taskType',
                'status',
                'currentStage',
                'createdAt',
                'updatedAt',
                'resultSummary',
            )
            if key in run
        }


engineering_project_service = EngineeringProjectService()
