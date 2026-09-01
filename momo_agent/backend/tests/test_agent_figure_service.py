from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from types import SimpleNamespace

import pytest

from app.agents.capabilities import ArtifactRef, FigureContract, FigurePanel, FigureRenderInput
from app.services.agent_llm import FigureRequest
from app.services.agent_figure_service import AgentFigureService


SOURCE_CONTENT = (
    'time,displacement,tower_base_shear\n'
    '0.0,0.0,0.0\n'
    '0.1,0.01,100.0\n'
    '0.2,-0.02,-150.0\n'
).encode('utf-8')
SOURCE_SHA256 = sha256(SOURCE_CONTENT).hexdigest()


@dataclass
class _Record:
    artifact: object
    content: bytes
    preview: object


class _ArtifactStore:
    def __init__(self) -> None:
        source = SimpleNamespace(
            artifact_id='art_timeseries_1',
            kind='CSV_TIMESERIES',
            sha256=SOURCE_SHA256,
            run_id='agr_1',
            path='output/results/timeseries.csv',
        )
        self.artifacts = [_Record(
            artifact=source,
            content=SOURCE_CONTENT,
            preview={},
        )]

    def get_artifact(self, artifact_id: str) -> _Record:
        return next(record for record in self.artifacts if record.artifact.artifact_id == artifact_id)

    def register_artifact(self, **kwargs):
        artifact = SimpleNamespace(
            artifact_id=f'art_figure_{len(self.artifacts)}',
            kind=kwargs['kind'],
            sha256=__import__('hashlib').sha256(kwargs['content']).hexdigest(),
            run_id=kwargs['preview']['runId'],
            path=kwargs['path'],
        )
        self.artifacts.insert(0, _Record(
            artifact=artifact,
            content=kwargs['content'],
            preview=kwargs['preview'],
        ))
        return artifact


def _request(source_sha256: str = SOURCE_SHA256) -> FigureRenderInput:
    return FigureRenderInput(
        run_id='agr_1',
        contract=FigureContract(
            claim='展示真实求解得到的梁端位移和塔底剪力时间历程。',
            source_artifacts=(ArtifactRef(
                artifact_id='art_timeseries_1',
                kind='CSV_TIMESERIES',
                sha256=source_sha256,
            ),),
            panels=(
                FigurePanel(
                    panel_id='a',
                    evidence_role='梁端位移响应',
                    x_field='time',
                    y_fields=('displacement',),
                    x_label='时间（s）',
                    y_label='梁端位移（m）',
                ),
                FigurePanel(
                    panel_id='b',
                    evidence_role='塔底剪力响应',
                    x_field='time',
                    y_fields=('tower_base_shear',),
                    x_label='时间（s）',
                    y_label='塔底剪力（N）',
                ),
            ),
            export_formats=('PNG', 'SVG', 'PDF', 'TIFF'),
        ),
    )


def test_agent_figure_service_renders_traceable_multiformat_bundle() -> None:
    store = _ArtifactStore()
    service = AgentFigureService(store)

    result = service.render(_request())

    assert {artifact.kind for artifact in result.figure_artifacts} == {'PLOT'}
    assert len(result.figure_artifacts) == 4
    assert result.source_data_artifact.artifact_id == 'art_timeseries_1'
    assert len(result.contract_sha256) == 64
    contents = {record.artifact.path.rsplit('.', 1)[-1]: record.content for record in store.artifacts}
    assert contents['png'].startswith(b'\x89PNG')
    assert b'<text' in contents['svg']
    assert contents['pdf'].startswith(b'%PDF')
    assert contents['tiff'][:2] in {b'II', b'MM'}
    assert all(record.preview['sourceArtifactIds'] == ['art_timeseries_1'] for record in store.artifacts[:4])


def test_agent_figure_service_is_idempotent_for_same_contract() -> None:
    store = _ArtifactStore()
    service = AgentFigureService(store)

    first = service.render(_request())
    artifact_count = len(store.artifacts)
    second = service.render(_request())

    assert len(store.artifacts) == artifact_count
    assert second.figure_artifacts == first.figure_artifacts


def test_agent_figure_service_rejects_source_hash_mismatch() -> None:
    store = _ArtifactStore()
    service = AgentFigureService(store)

    with pytest.raises(ValueError, match='SHA256'):
        service.render(_request('b' * 64))


def test_agent_figure_service_recomputes_registered_source_hash() -> None:
    store = _ArtifactStore()
    store.artifacts[0].content = b'time,displacement,tower_base_shear\n0.0,9.9,9.9\n'
    service = AgentFigureService(store)

    with pytest.raises(ValueError, match='内容 SHA256'):
        service.render(_request())


def test_build_render_input_resolves_metric_semantics_and_registered_sha256() -> None:
    store = _ArtifactStore()
    service = AgentFigureService(store)
    request = FigureRequest(
        metrics=['max_girder_end_displacement', 'max_tower_base_shear'],
        artifact='timeseries.csv',
        claim='展示位移与塔底剪力时程',
        exportFormats=['PNG', 'SVG'],
    )

    render_input = service.build_render_input(
        run_id='agr_1',
        figure_request=request,
        catalog={
            'artifacts': {
                'timeseries.csv': {
                    'artifactId': 'art_timeseries_1',
                    'columns': ['time', 'displacement', 'tower_base_shear'],
                },
            },
        },
    )

    panel = render_input.contract.panels[0]
    assert panel.y_fields == ('displacement', 'tower_base_shear')
    assert render_input.contract.source_artifacts[0].sha256 == SOURCE_SHA256
    assert render_input.contract.export_formats == ('PNG', 'SVG')


def test_build_render_input_rejects_metric_without_real_csv_column() -> None:
    service = AgentFigureService(_ArtifactStore())
    request = FigureRequest(
        metrics=['max_damper_force'],
        artifact='timeseries.csv',
        claim='画阻尼器力',
    )

    with pytest.raises(ValueError, match='没有可绘制列'):
        service.build_render_input(
            run_id='agr_1',
            figure_request=request,
            catalog={'artifacts': {
                'timeseries.csv': {
                    'artifactId': 'art_timeseries_1',
                    'columns': ['time', 'displacement'],
                },
            }},
        )
