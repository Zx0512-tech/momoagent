from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from app.agents.capabilities import (
    AnalysisCapabilityTools,
    ArtifactRef,
    CommandAssembleInput,
    FigureContract,
    FigurePanel,
    FigureRenderInput,
)
from app.agents.tools import ToolApprovalRequired, ToolRisk, TypedAgentTool, TypedToolRegistry


class _ArtifactInput(BaseModel):
    artifact_id: str


class _ArtifactOutput(BaseModel):
    accepted: bool


def _handlers() -> dict[str, object]:
    return {
        name: (lambda _payload: {})
        for name in AnalysisCapabilityTools.REQUIRED_HANDLER_NAMES
    }


def test_analysis_capability_catalog_exposes_engineering_boundaries() -> None:
    tools = AnalysisCapabilityTools(_handlers())

    descriptors = {item.name: item for item in tools.list_tools()}

    assert set(descriptors) == {
        'command_stream.assemble',
        'command_stream.validate',
        'evidence.verify',
        'figure.render',
        'load.inspect',
        'load.map_targets',
        'result.extract',
        'solver.capabilities',
        'solver.execute',
    }
    assert descriptors['load.inspect'].risk is ToolRisk.READ_ONLY
    assert descriptors['command_stream.assemble'].risk is ToolRisk.ARTIFACT_WRITE
    assert descriptors['solver.execute'].risk is ToolRisk.SOLVER_EXECUTION
    assert descriptors['solver.execute'].requires_approval is True
    assert descriptors['figure.render'].artifact_kinds == ('FIGURE_BUNDLE', 'SOURCE_DATA')
    assert all('当' in descriptor.description for descriptor in descriptors.values())


def test_artifact_write_requires_idempotency_without_human_approval() -> None:
    registry = TypedToolRegistry()
    registry.register(TypedAgentTool(
        name='artifact.write',
        description='写入受控派生制品',
        input_model=_ArtifactInput,
        output_model=_ArtifactOutput,
        risk=ToolRisk.ARTIFACT_WRITE,
        requires_approval=False,
        handler=lambda _payload: {'accepted': True},
    ))

    with pytest.raises(ValueError, match='idempotency_key'):
        registry.execute('artifact.write', {'artifact_id': 'art_1'})

    result = registry.execute(
        'artifact.write',
        {'artifact_id': 'art_1'},
        idempotency_key='run_1:artifact:hash',
    )

    assert result.accepted is True


def test_solver_execute_rejects_unregistered_frozen_action_fields() -> None:
    tools = AnalysisCapabilityTools(_handlers())
    payload = {
        'run_id': 'agr_1',
        'frozen_action': {
            'solver': 'OPENSEESPY_INPROC',
            'runMode': 'REAL_AGENT_ANALYSIS',
            'workflowConfigPath': (
                'docs/examples/templates/'
                'openseespy_inproc_run_earthquake_baseline_template.json'
            ),
            'shell': 'unsafe command',
        },
    }

    with pytest.raises(ToolApprovalRequired):
        tools.execute('solver.execute', payload)
    with pytest.raises(ValueError, match='未登记字段'):
        tools.execute(
            'solver.execute',
            payload,
            approved=True,
            idempotency_key='agr_1:solver:hash',
        )


def test_command_stream_contract_rejects_arbitrary_nested_mapping() -> None:
    with pytest.raises(ValidationError):
        CommandAssembleInput(
            solver='ANSYS',
            template_id='ANSYS_EARTHQUAKE_BASELINE',
            case_id='case_1',
            load_artifact=ArtifactRef(
                artifact_id='art_load_1',
                kind='STANDARD_LOAD',
                sha256='a' * 64,
            ),
            load_mapping={'shell': 'unsafe command'},
            response_ids=('max_tower_base_shear',),
        )


def test_figure_contract_requires_source_artifact_and_supported_exports() -> None:
    source = ArtifactRef(
        artifact_id='art_result_1',
        kind='RESULT_TIMESERIES',
        sha256='a' * 64,
    )
    panel = FigurePanel(
        panel_id='a',
        evidence_role='展示梁端位移时间历程',
        x_field='time',
        y_fields=('displacement',),
        x_label='时间（s）',
        y_label='梁端位移（m）',
    )

    request = FigureRenderInput(
        run_id='agr_1',
        contract=FigureContract(
            claim='展示真实求解得到的梁端位移时程。',
            source_artifacts=(source,),
            panels=(panel,),
            export_formats=('PNG', 'SVG', 'PDF', 'TIFF'),
        ),
    )

    assert request.contract.backend == 'PYTHON_MATPLOTLIB'
    assert request.contract.source_data_required is True
    with pytest.raises(ValidationError):
        FigureContract(
            claim='无来源图件。',
            source_artifacts=(),
            panels=(panel,),
            export_formats=('PNG',),
        )
    with pytest.raises(ValidationError):
        FigureRenderInput(run_id='../outside', contract=request.contract)
    with pytest.raises(ValidationError):
        FigureContract(
            claim='重复格式。',
            source_artifacts=(source,),
            panels=(panel,),
            export_formats=('SVG', 'SVG'),
        )
