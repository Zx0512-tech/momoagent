from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.api.v1.agent_project_schemas import (
    EngineeringProjectCreateRequest,
    EngineeringProjectSessionCreateRequest,
    EngineeringWorkspacePatch,
)
from app.services.agent_project_service import engineering_project_service


router = APIRouter()


@router.post('/agent/projects')
def create_engineering_project(payload: EngineeringProjectCreateRequest) -> dict[str, Any]:
    workspace = (
        payload.workspace.model_dump(by_alias=True, exclude_unset=True)
        if payload.workspace is not None
        else None
    )
    return engineering_project_service.create_project(
        payload.name,
        description=payload.description,
        workspace=workspace,
    )


@router.get('/agent/projects')
def list_engineering_projects() -> dict[str, Any]:
    return {'data': engineering_project_service.list_projects()}


@router.get('/agent/projects/{project_id}')
def get_engineering_project(project_id: str) -> dict[str, Any]:
    return engineering_project_service.get_project(project_id)


@router.put('/agent/projects/{project_id}/workspace')
def update_engineering_workspace(
    project_id: str,
    payload: EngineeringWorkspacePatch,
) -> dict[str, Any]:
    return engineering_project_service.update_workspace(
        project_id,
        payload.model_dump(by_alias=True, exclude_unset=True),
    )


@router.post('/agent/projects/{project_id}/sessions')
def create_project_session(
    project_id: str,
    payload: EngineeringProjectSessionCreateRequest,
) -> dict[str, Any]:
    return engineering_project_service.create_session(project_id, payload.title)


@router.put('/agent/projects/{project_id}/sessions/{session_id}')
def attach_project_session(project_id: str, session_id: str) -> dict[str, Any]:
    return engineering_project_service.attach_session(project_id, session_id)
