from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictToolModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ArtifactRef(StrictToolModel):
    artifact_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,128}$')
    kind: str = Field(pattern=r'^[A-Z][A-Z0-9_]{0,63}$')
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class FigurePanel(StrictToolModel):
    panel_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,16}$')
    evidence_role: str = Field(min_length=1, max_length=200)
    x_field: str = Field(pattern=r'^[A-Za-z_][A-Za-z0-9_]{0,127}$')
    y_fields: tuple[str, ...] = Field(min_length=1, max_length=6)
    x_label: str = Field(min_length=1, max_length=100)
    y_label: str = Field(min_length=1, max_length=100)

    @field_validator('y_fields')
    @classmethod
    def validate_y_fields(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        field_pattern = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,127}$')
        if len(set(value)) != len(value) or any(not field_pattern.fullmatch(item) for item in value):
            raise ValueError('y_fields 必须是互不重复的登记字段名')
        return value


class FigureContract(StrictToolModel):
    claim: str = Field(min_length=1, max_length=500)
    archetype: Literal['QUANTITATIVE_GRID'] = 'QUANTITATIVE_GRID'
    backend: Literal['PYTHON_MATPLOTLIB'] = 'PYTHON_MATPLOTLIB'
    source_artifacts: tuple[ArtifactRef, ...] = Field(min_length=1, max_length=1)
    panels: tuple[FigurePanel, ...] = Field(min_length=1, max_length=6)
    export_formats: tuple[Literal['PNG', 'SVG', 'PDF', 'TIFF'], ...] = Field(
        min_length=1,
        max_length=4,
    )
    source_data_required: Literal[True] = True

    @field_validator('export_formats')
    @classmethod
    def validate_export_formats(
        cls,
        value: tuple[Literal['PNG', 'SVG', 'PDF', 'TIFF'], ...],
    ) -> tuple[Literal['PNG', 'SVG', 'PDF', 'TIFF'], ...]:
        if len(set(value)) != len(value):
            raise ValueError('export_formats 不能重复')
        return value


class FigureRenderInput(StrictToolModel):
    run_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,128}$')
    contract: FigureContract


class FigureRenderOutput(StrictToolModel):
    figure_artifacts: tuple[ArtifactRef, ...] = Field(min_length=1)
    source_data_artifact: ArtifactRef
    contract_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
