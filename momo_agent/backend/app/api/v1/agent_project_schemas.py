from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class ProjectModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra='forbid',
        strict=True,
    )


class EngineeringWorkspacePatch(ProjectModel):
    model_artifact_id: str | None = None
    model_file_name: str | None = None
    model_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    solver: Literal['ANSYS', 'OPENSEESPY_INPROC'] | None = None
    load_kind: Literal['EARTHQUAKE', 'WIND', 'TRAFFIC', 'GENERIC_NODAL'] | None = None
    load_artifact_id: str | None = None
    load_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    damper_type: Literal['VISCOUS', 'FRICTION', 'EDDY_CURRENT'] | None = None
    selected_layout_id: Literal['ONE_PER_TOWER', 'TWO_PER_TOWER'] | None = None
    response_ids: list[str] | None = Field(default=None, max_length=32)
    optimization_profile: Literal['STANDARD', 'FULL', 'CUSTOM'] | None = None


class EngineeringWorkspaceUpdateRequest(ProjectModel):
    expected_revision: int = Field(ge=1)
    patch: EngineeringWorkspacePatch


class EngineeringProjectCreateRequest(ProjectModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default='', max_length=1000)
    workspace: EngineeringWorkspacePatch | None = None


class EngineeringProjectSessionCreateRequest(ProjectModel):
    title: str = Field(default='新建工程智能体会话', min_length=1, max_length=120)
