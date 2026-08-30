from __future__ import annotations

import csv
import io
import json
from hashlib import sha256
from types import SimpleNamespace

import pytest

from app.core.exceptions import LLMUnavailableError
from app.agents.inquiry import InquiryTools
from app.services.result_inquiry import ResultInquiryError, ResultInquiryService
from app.services.agent_llm import (
    FigureRequest,
    InquiryPlan,
    InquiryQuery,
    OpenAICompatiblePlanner,
    NarrativeResult,
)
from app.services.agent_service import AgentService


class _FakeStore:
    def __init__(self, content: bytes, *, kind: str = 'CSV_TIMESERIES') -> None:
        self.artifacts = []
        artifact = SimpleNamespace(
            artifact_id='art_csv_1',
            kind=kind,
            name='timeseries.csv',
            path='output/test/timeseries.csv',
            sha256=sha256(content).hexdigest(),
        )
        self.record = SimpleNamespace(artifact=artifact, content=content)
        self.artifacts.append(self.record)

    def get_artifact(self, artifact_id: str):
        for record in self.artifacts:
            if artifact_id == record.artifact.artifact_id:
                return record
        raise KeyError(artifact_id)

    def register_artifact(self, **kwargs):
        artifact_id = f'art_plot_{len(self.artifacts)}'
        content = bytes(kwargs['content'])
        artifact = SimpleNamespace(
            artifact_id=artifact_id,
            kind=kwargs['kind'],
            name=kwargs['name'],
            path=kwargs['path'],
            sha256=sha256(content).hexdigest(),
            mime_type=kwargs.get('mime_type'),
        )
        record = SimpleNamespace(artifact=artifact, content=content)
        self.artifacts.append(record)
        return artifact


def _csv_bytes(headers: list[str], rows: list[list[object]]) -> bytes:
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(rows)
    return output.getvalue().encode('utf-8')


def test_load_caches_parsed_csv_by_artifact_and_sha(monkeypatch) -> None:
    """同一制品重复查询命中解析缓存：不再重读内容、重算 SHA、重新解析。"""
    content = _csv_bytes(['time', 'displacement'], [[0.0, 1.0], [0.5, -4.0], [1.0, 2.0]])
    store = _FakeStore(content)
    store.record.artifact.artifact_id = 'art_cache_probe'
    service = ResultInquiryService(store)

    first = service.peak('art_cache_probe', column='displacement')
    # 破坏底层内容但保持登记 sha 不变：命中缓存时不会重新校验/解析，
    # 若未命中则 SHA 校验必然失败。
    store.record.content = b'corrupted'
    second = service.peak('art_cache_probe', column='displacement')

    assert first == second


def _sweep_summary_store(case_results: list[dict], *, kind: str = 'JSON_SUMMARY') -> _FakeStore:
    payload = {
        'mode': 'real_damper_parameter_sweep',
        'caseResults': case_results,
    }
    store = _FakeStore(json.dumps(payload, ensure_ascii=False).encode('utf-8'), kind=kind)
    store.record.artifact.artifact_id = 'art_sweep_summary'
    store.record.artifact.name = 'real_damper_parameter_sweep_summary.json'
    return store


def test_sweep_cases_ranks_by_metric_and_reports_sensitivity() -> None:
    """跨算例聚合：按指标排序并给出同组参数与指标的 Pearson 敏感性。"""
    cases = [
        {
            'caseId': f'case_{index}',
            'damperType': 'VISCOUS',
            'parameters': {'c': float(c), 'alpha': 0.5},
            'objectives': {'max_displacement': 0.10 - 0.01 * index, 'max_damper_force': 1000.0 * (index + 1)},
            'isVerifiedSolverOutput': True,
        }
        for index, c in enumerate([1000.0, 2000.0, 3000.0, 4000.0])
    ]
    service = ResultInquiryService(_sweep_summary_store(cases))

    result = service.sweep_cases('art_sweep_summary', metric='max_displacement', order='asc', limit=3)

    assert result['metric'] == 'max_displacement'
    assert [row['caseId'] for row in result['rows']] == ['case_3', 'case_2', 'case_1']
    assert result['rows'][0]['rank'] == 1
    assert result['caseCount'] == 4
    assert 'max_damper_force' in result['availableMetrics']
    # c 越大位移越小 → 完全负相关；alpha 为常量 → 不产生敏感性行。
    sensitivity = {item['parameter']: item for item in result['sensitivity']}
    assert sensitivity['c']['pearson'] == -1.0
    assert sensitivity['c']['damperType'] == 'VISCOUS'
    assert 'alpha' not in sensitivity


def test_sweep_cases_defaults_metric_and_validates_inputs() -> None:
    cases = [
        {'caseId': 'a', 'damperType': 'FRICTION', 'parameters': {'f': 1.0}, 'objectives': {'max_acceleration': 2.0}},
        {'caseId': 'b', 'damperType': 'FRICTION', 'parameters': {'f': 2.0}, 'objectives': {'max_acceleration': 1.0}},
    ]
    service = ResultInquiryService(_sweep_summary_store(cases))

    result = service.sweep_cases('art_sweep_summary')
    assert result['metric'] == 'max_acceleration'
    assert [row['caseId'] for row in result['rows']] == ['b', 'a']
    # 两个样本不足以计算敏感性。
    assert result['sensitivity'] == []

    with pytest.raises(ResultInquiryError):
        service.sweep_cases('art_sweep_summary', metric='not_a_metric')
    with pytest.raises(ResultInquiryError):
        service.sweep_cases('art_sweep_summary', order='sideways')
    with pytest.raises(ResultInquiryError):
        service.sweep_cases('art_sweep_summary', limit=65)


def test_sweep_cases_rejects_non_summary_artifacts() -> None:
    content = _csv_bytes(['time', 'displacement'], [[0.0, 1.0]])
    store = _FakeStore(content)
    service = ResultInquiryService(store)

    with pytest.raises(ResultInquiryError):
        service.sweep_cases('art_csv_1')


def test_sweep_cases_tool_is_registered_with_strict_input() -> None:
    cases = [
        {'caseId': 'a', 'damperType': 'VISCOUS', 'parameters': {'c': 1.0}, 'objectives': {'max_displacement': 1.0}},
    ]
    service = ResultInquiryService(_sweep_summary_store(cases))
    tools = InquiryTools(service=service)

    output = tools.call('result.sweep_cases', {'artifact_id': 'art_sweep_summary'})
    payload = output.model_dump(by_alias=True, mode='json')
    assert payload['metric'] == 'max_displacement'
    assert payload['caseCount'] == 1


def test_load_cache_miss_on_different_sha_still_verifies_integrity() -> None:
    content = _csv_bytes(['time', 'displacement'], [[0.0, 1.0], [0.5, -4.0]])
    store = _FakeStore(content)
    store.record.artifact.artifact_id = 'art_cache_probe_2'
    service = ResultInquiryService(store)
    service.columns('art_cache_probe_2')

    # 内容与登记 sha 同时变化 → 新缓存键未命中 → 完整性校验必须继续生效。
    store.record.content = b'time,displacement\n0.0,1.0\n'
    store.record.artifact.sha256 = 'not-a-real-sha'
    with pytest.raises(ResultInquiryError, match='SHA256'):
        service.columns('art_cache_probe_2')


def test_peak_returns_signed_value_and_time_for_absolute_peak() -> None:
    content = _csv_bytes(['time', 'displacement'], [[0.0, 1.0], [0.5, -4.0], [1.0, 2.0]])
    service = ResultInquiryService(_FakeStore(content))

    result = service.peak('art_csv_1', column='displacement')

    assert result == {
        'column': 'displacement',
        'peakAbsolute': 4.0,
        'peakSigned': -4.0,
        'peakTime': 0.5,
        'sampleCount': 3,
    }


def test_topsis_reads_top_candidates_from_optimization_summary() -> None:
    summary = {
        'optimization': {
            'objective_names': ['earthquake:displacement'],
            'decision_weights': [1.0],
            'pareto_solutions': [
                {'design_parameters': {'c': 7600.0}, 'objective_values': {'earthquake:displacement': 0.12}},
                {'design_parameters': {'c': 8200.0}, 'objective_values': {'earthquake:displacement': 0.14}},
            ],
            'topsis': {'ranking': [1, 0], 'closeness': [0.9, 0.4], 'weights': [1.0]},
        },
    }
    content = json.dumps(summary, ensure_ascii=False).encode('utf-8')
    service = ResultInquiryService(_FakeStore(content, kind='OPTIMIZATION_REPORT'))
    service.store.record.artifact.name = 'real_optimization_summary.json'

    result = service.topsis('art_csv_1', limit=2)

    assert result['availableCount'] == 2
    assert result['rows'][0]['rank'] == 1
    assert result['rows'][0]['parameters'] == {'c': 8200.0}
    assert result['rows'][0]['score'] == 0.4


def test_at_time_matches_nearest_sample() -> None:
    content = _csv_bytes(['time', 'displacement'], [[0.0, 1.0], [0.5, 3.0], [1.0, 2.0]])
    service = ResultInquiryService(_FakeStore(content))

    result = service.at_time('art_csv_1', columns=['displacement'], target_time=0.62)

    assert result['matchedTime'] == 0.5
    assert result['values'] == {'displacement': 3.0}


def test_downsample_uses_ceil_buckets_and_preserves_extreme_peak() -> None:
    rows = [[index * 0.01, 0.0, 0.0] for index in range(4001)]
    rows[3210][1] = -99.0
    rows[1777][2] = 77.0
    service = ResultInquiryService(_FakeStore(_csv_bytes(['time', 'displacement', 'shear'], rows)))

    result = service.downsample(
        'art_csv_1',
        columns=['time', 'displacement', 'shear'],
        max_points=1000,
    )

    assert len(result['time']) <= 1000
    assert -99.0 in result['displacement']
    assert 77.0 in result['shear']


def test_correlate_returns_linear_relationship_and_rejects_constant_series() -> None:
    content = _csv_bytes(
        ['time', 'a', 'b', 'constant'],
        [[0.0, 1.0, 2.0, 1.0], [0.5, 2.0, 4.0, 1.0], [1.0, 3.0, 6.0, 1.0]],
    )
    service = ResultInquiryService(_FakeStore(content))

    assert service.correlate('art_csv_1', column_a='a', column_b='b')['pearson'] == 1.0
    with pytest.raises(ResultInquiryError, match='常量'):
        service.correlate('art_csv_1', column_a='a', column_b='constant')


def test_query_rejects_non_csv_and_sha_mismatch_and_missing_column() -> None:
    content = _csv_bytes(['time', 'value'], [[0.0, 1.0], [1.0, 2.0]])
    non_csv = ResultInquiryService(_FakeStore(content, kind='JSON_SUMMARY'))
    with pytest.raises(ResultInquiryError, match='CSV'):
        non_csv.columns('art_csv_1')

    store = _FakeStore(content)
    store.record.content = b'tampered'
    with pytest.raises(ResultInquiryError, match='SHA256'):
        ResultInquiryService(store).columns('art_csv_1')

    with pytest.raises(ResultInquiryError, match='missing'):
        ResultInquiryService(_FakeStore(content)).peak('art_csv_1', column='missing')


def test_inquiry_artifacts_consumes_verified_result_catalog(monkeypatch) -> None:
    content = _csv_bytes(
        ['time', 'displacement'],
        [[0.0, 1.0], [0.5, -4.0]],
    )
    store = _FakeStore(content)
    catalog = {
        'schemaVersion': '1.0',
        'runId': 'real_run_1',
        'entryCount': 1,
        'entries': [{
            'artifactPath': 'output/test/timeseries.csv',
            'columns': ['time', 'displacement'],
            'units': {'time': 's', 'displacement': 'm'},
            'sha256': sha256(content).hexdigest(),
            'verified': True,
        }],
        'verified': True,
    }
    catalog_content = json.dumps(catalog, ensure_ascii=False).encode('utf-8')
    catalog_record = SimpleNamespace(
        artifact=SimpleNamespace(
            artifact_id='art_catalog_1',
            kind='JSON_SUMMARY',
            name='result_catalog.json',
            path='output/test/result_catalog.json',
            sha256=sha256(catalog_content).hexdigest(),
        ),
        content=catalog_content,
        preview=catalog,
    )
    store.artifacts.append(catalog_record)
    monkeypatch.setattr('app.services.agent_conversation.platform_store', store)

    artifacts = AgentService()._inquiry_artifacts({
        'runId': 'agr_source',
        'artifactIds': ['art_csv_1', 'art_catalog_1'],
    })

    assert artifacts == {'timeseries.csv': 'art_csv_1'}
    inquiry_catalog = AgentService()._build_inquiry_catalog(
        {'runId': 'agr_source', 'taskType': 'ANALYSIS', 'artifactIds': ['art_csv_1', 'art_catalog_1']},
        ResultInquiryService(store),
        artifacts,
    )
    assert inquiry_catalog['resultCatalog'] == {
        'artifactId': 'art_catalog_1',
        'schemaVersion': '1.0',
        'entryCount': 1,
        'verified': True,
    }
    assert inquiry_catalog['artifacts']['timeseries.csv']['units'] == {
        'time': 's',
        'displacement': 'm',
    }


def test_inquiry_artifacts_rejects_catalog_missing_registered_source(monkeypatch) -> None:
    content = _csv_bytes(['time', 'displacement'], [[0.0, 1.0], [0.5, -4.0]])
    store = _FakeStore(content)
    catalog = {
        'schemaVersion': '1.0',
        'runId': 'real_run_1',
        'entryCount': 1,
        'entries': [{
            'artifactPath': 'output/test/not_registered.csv',
            'columns': ['time', 'displacement'],
            'units': {'time': 's', 'displacement': 'm'},
            'sha256': sha256(content).hexdigest(),
            'verified': True,
        }],
        'verified': True,
    }
    catalog_content = json.dumps(catalog).encode('utf-8')
    store.artifacts.append(SimpleNamespace(
        artifact=SimpleNamespace(
            artifact_id='art_catalog_1',
            kind='JSON_SUMMARY',
            name='result_catalog.json',
            path='output/test/result_catalog.json',
            sha256=sha256(catalog_content).hexdigest(),
        ),
        content=catalog_content,
        preview=catalog,
    ))
    monkeypatch.setattr('app.services.agent_conversation.platform_store', store)

    with pytest.raises(ResultInquiryError, match='目录来源制品不存在'):
        AgentService()._inquiry_artifacts({
            'runId': 'agr_source',
            'artifactIds': ['art_csv_1', 'art_catalog_1'],
        })


def test_inquiry_artifacts_ignores_legacy_solver_index_entries(monkeypatch) -> None:
    content = _csv_bytes(
        ['time', 'displacement'],
        [[0.0, 1.0], [0.5, -4.0]],
    )
    store = _FakeStore(content)
    catalog = {
        'schemaVersion': '1.0',
        'runId': 'real_run_legacy',
        'entryCount': 2,
        'entries': [
            {
                'artifactPath': 'output/test/index.csv',
                'columns': ['case_id', 'status'],
                'units': {'case_id': '1', 'status': '1'},
                'sha256': '0' * 64,
                'verified': True,
            },
            {
                'artifactPath': 'output/test/timeseries.csv',
                'columns': ['time', 'displacement'],
                'units': {'time': 's', 'displacement': 'm'},
                'sha256': sha256(content).hexdigest(),
                'verified': True,
            },
        ],
        'verified': True,
    }
    catalog_content = json.dumps(catalog, ensure_ascii=False).encode('utf-8')
    store.artifacts.append(SimpleNamespace(
        artifact=SimpleNamespace(
            artifact_id='art_catalog_legacy',
            kind='JSON_SUMMARY',
            name='result_catalog.json',
            path='output/test/result_catalog.json',
            sha256=sha256(catalog_content).hexdigest(),
        ),
        content=catalog_content,
        preview=catalog,
    ))
    monkeypatch.setattr('app.services.agent_conversation.platform_store', store)

    artifacts = AgentService()._inquiry_artifacts({
        'runId': 'agr_source',
        'artifactIds': ['art_csv_1', 'art_catalog_legacy'],
    })

    assert artifacts == {'timeseries.csv': 'art_csv_1'}


def test_inquiry_tools_are_read_only_and_validate_camel_case_outputs() -> None:
    content = _csv_bytes(['time', 'displacement'], [[0.0, 1.0], [0.5, -4.0], [1.0, 2.0]])
    tools = InquiryTools(service=ResultInquiryService(_FakeStore(content)))

    peak = tools.call('result.peak', {'artifact_id': 'art_csv_1', 'column': 'displacement'})

    assert peak.peak_absolute == 4.0
    assert tools.registry.describe('result.peak').risk.value == 'READ_ONLY'
    assert tools.registry.describe('result.peak').requires_approval is False


def test_inquiry_tools_expose_one_general_compare_operation() -> None:
    content = _csv_bytes(
        ['time', 'baseline', 'controlled'],
        [[0.0, 1.0, 2.0], [0.5, -4.0, -6.0], [1.0, 2.0, 3.0]],
    )
    tools = InquiryTools(service=ResultInquiryService(_FakeStore(content)))

    compare = tools.call('result.compare', {
        'artifact_id': 'art_csv_1',
        'columns': ['baseline', 'controlled'],
    })

    assert {item.name for item in tools.registry.list_tools()} == {
        'result.at_time',
        'result.columns',
        'result.compare',
        'result.correlate',
        'result.peak',
        'result.sweep_cases',
        'result.topsis',
    }
    assert all('当' in item.description for item in tools.registry.list_tools())
    assert compare.difference == 2.0
    assert compare.relative_change_percent == 50.0
    assert compare.ratio == 1.5
    assert compare.relative_change == 0.5
    assert compare.interpretation_limit


@pytest.mark.parametrize(
    ('text', 'evidence_mode', 'reason'),
    [
        ('峰值为 999。', 'REAL_FEM', 'LLM_NUMBER_HALLUCINATION'),
        ('峰值为 4。', 'DIAGNOSTIC_ONLY', 'LLM_MISSING_DIAGNOSTIC_CAVEAT'),
        ('峰值为 4，建议采用新的布置。', 'REAL_FEM', 'LLM_OVERREACHING_CLAIM'),
    ],
)
def test_explain_inquiry_rejects_hallucination_diagnostic_or_design_claim(
    monkeypatch,
    text: str,
    evidence_mode: str,
    reason: str,
) -> None:
    planner = OpenAICompatiblePlanner(
        base_url='http://127.0.0.1:11434/v1',
        model='momo-planner',
    )
    monkeypatch.setattr(
        planner,
        '_request',
        lambda _payload, *, stage=None: {'choices': [{'message': {'content': f'{{"text":"{text}"}}'}}]},
    )

    result = planner.explain_inquiry(
        question='为什么改善有限？',
        question_type='WHY_LIMITED_IMPROVEMENT',
        evidence_mode=evidence_mode,
        facts={'targetPeak': {'peakAbsolute': 4.0}},
    )

    assert result.narrative_mode == 'TEMPLATE_FALLBACK'
    assert result.fallback_reason == reason


def test_explain_inquiry_accepts_grounded_real_fem_text_and_prompt_constraints(monkeypatch) -> None:
    planner = OpenAICompatiblePlanner(
        base_url='http://127.0.0.1:11434/v1',
        model='momo-planner',
    )
    monkeypatch.setattr(
        planner,
        '_request',
        lambda _payload, *, stage=None: {'choices': [{'message': {'content': '{"text":"峰值为 4。"}'}}]},
    )

    result = planner.explain_inquiry(
        question='什么时候最有效？',
        question_type='WHEN_MOST_EFFECTIVE',
        evidence_mode='REAL_FEM',
        facts={'targetPeak': {'peakAbsolute': 4.0}},
    )
    payload = planner._explain_inquiry_payload(
        question='为什么？',
        question_type='WHY_LIMITED_IMPROVEMENT',
        evidence_mode='REAL_FEM',
        facts={'targetPeak': {'peakAbsolute': 4.0}},
    )

    assert result.narrative_mode == 'LLM'
    system_prompt = payload['messages'][0]['content']
    assert '不得给出任何设计建议' in system_prompt
    assert '相关性只说明同步变化' in system_prompt


def test_plan_inquiry_requires_configuration() -> None:
    with pytest.raises(LLMUnavailableError) as error:
        OpenAICompatiblePlanner(base_url='', model='').plan_inquiry(
            question='峰值什么时候最大？',
            catalog={'artifacts': {}},
        )

    assert error.value.stage == 'INQUIRY_PLANNING'
    assert error.value.reason == 'LLM_NOT_CONFIGURED'


def test_plan_inquiry_request_failure_returns_safe_followup_plan(monkeypatch) -> None:
    planner = OpenAICompatiblePlanner(
        base_url='http://127.0.0.1:11434/v1',
        model='momo-planner',
    )
    monkeypatch.setattr(
        planner,
        '_request',
        lambda _payload, *, stage=None: (_ for _ in ()).throw(
            LLMUnavailableError('INQUIRY_PLANNING', 'LLM_SERVER_ERROR')
        ),
    )

    result = planner.plan_inquiry(
        question='位移峰值是多少？',
        catalog={'artifacts': {}},
    )

    assert result.queries == []
    assert result.needs_followup is True
    assert '规划不可用' in result.reason


def test_build_inquiry_catalog_uses_actual_csv_header() -> None:
    content = _csv_bytes(['time', 'only_response', 'another_response'], [[0.0, 1.0, 2.0]])
    service = AgentService()

    catalog = service._build_inquiry_catalog(
        {'taskType': 'ANALYSIS', 'resultSummary': {'objectives': {'max_response': 1.0}}},
        ResultInquiryService(_FakeStore(content)),
        {'timeseries.csv': 'art_csv_1'},
    )

    assert catalog['artifacts']['timeseries.csv']['columns'] == [
        'time', 'only_response', 'another_response',
    ]
    assert catalog['objectives'] == {'max_response': 1.0}


def test_build_inquiry_catalog_exposes_metric_semantics_and_objective_changes() -> None:
    content = _csv_bytes(
        ['time', 'displacement', 'tower_base_shear'],
        [[0.0, 0.1, 100.0], [1.0, 0.08, 90.0]],
    )
    service = AgentService()

    catalog = service._build_inquiry_catalog(
        {
            'taskType': 'FULL_OPTIMIZATION',
            'resultSummary': {
                'objectives': {'max_girder_end_displacement': 0.08},
                'baselineObjectives': {'max_girder_end_displacement': 0.1},
                'recommendedObjectives': {'max_girder_end_displacement': 0.08},
                'responseComparison': {'metrics': {'max_girder_end_displacement': {'relativeToFirst': -0.2}}},
                'sampleResponses': [{'sampleType': 'CONTROLLED_DOE', 'design': {'c': 7600, 'alpha': 0.8}}],
            },
        },
        ResultInquiryService(_FakeStore(content)),
        {'timeseries.csv': 'art_csv_1'},
    )

    metric = catalog['metrics']['max_girder_end_displacement']
    assert metric['label'] == '梁端位移'
    assert metric['unit'] == 'm'
    assert metric['artifact'] == 'timeseries.csv'
    assert metric['column'] == 'displacement'
    assert catalog['objectiveChanges']['max_girder_end_displacement']['relativeChangePercent'] == -20.0
    assert catalog['responseComparison']['metrics']['max_girder_end_displacement']['relativeToFirst'] == -0.2
    assert catalog['responseComparisonPercent']['metrics']['max_girder_end_displacement']['relativeChangePercent'] == -20.0
    assert catalog['sampleResponses'][0]['design']['c'] == 7600


def test_inquiry_catalog_lists_multiple_terminal_runs_for_cross_run_questions(monkeypatch) -> None:
    content = _csv_bytes(['time', 'displacement'], [[0.0, 1.0], [1.0, 2.0]])
    store = _FakeStore(content)
    repo = _InquiryRepository([
        {**_source_run(), 'runId': 'agr_latest'},
        {
            **_source_run(),
            'runId': 'agr_previous',
            'resultSummary': {
                'evidenceMode': 'REAL_FEM',
                'objectives': {'max_girder_end_displacement': 0.2},
            },
        },
    ])
    service = AgentService()
    service.repository = lambda: repo
    monkeypatch.setattr('app.services.agent_service.platform_store.get_artifact', store.get_artifact)

    catalog = service._build_inquiry_catalog(
        repo.runs[0],
        ResultInquiryService(store),
        {'timeseries.csv': 'art_csv_1'},
    )
    runs = service._find_inquirable_runs(repo, 'ags_inquiry')

    assert [run['runId'] for run in runs] == ['agr_latest', 'agr_previous']
    assert catalog['metrics']['max_girder_end_displacement']['column'] == 'displacement'


def test_plan_inquiry_parses_read_only_queries_and_constrains_prompt(monkeypatch) -> None:
    planner = OpenAICompatiblePlanner(
        base_url='http://127.0.0.1:11434/v1',
        model='momo-planner',
    )
    monkeypatch.setattr(
        planner,
        '_request',
        lambda _payload, *, stage=None: {'choices': [{
            'message': {'content': '{"queries":[{"op":"peak","artifact":"timeseries.csv",'
            '"columns":["displacement"],"targetTime":null}],"needsFollowup":false,"reason":"峰值"}'},
        }]},
    )

    result = planner.plan_inquiry(
        question='什么时候最有效？',
        catalog={'artifacts': {'timeseries.csv': {'columns': ['time', 'displacement']}}},
    )
    payload = planner._inquiry_plan_payload(
        question='为什么改善有限？',
        catalog={'artifacts': {'timeseries.csv': {'columns': ['time', 'displacement']}}},
    )

    assert isinstance(result, InquiryPlan)
    assert result.queries[0].op == 'peak'
    assert result.queries[0].artifact == 'timeseries.csv'
    assert '不要臆造 catalog 中没有的文件名或列名' in payload['messages'][0]['content']


def test_plan_inquiry_parses_optional_figure_request(monkeypatch) -> None:
    planner = OpenAICompatiblePlanner(
        base_url='http://127.0.0.1:11434/v1',
        model='momo-planner',
    )
    monkeypatch.setattr(
        planner,
        '_request',
        lambda _payload, *, stage=None: {'choices': [{
            'message': {'content': '{"queries":[],"figure":{"metrics":["max_girder_end_displacement"],'
            '"artifact":"timeseries.csv","xField":"time","claim":"位移时程","exportFormats":["PNG"]},'
            '"needsFollowup":false,"reason":"用户要求绘图"}'},
        }]},
    )

    result = planner.plan_inquiry(
        question='画一下位移时程',
        catalog={'artifacts': {'timeseries.csv': {'columns': ['time', 'displacement']}}},
    )

    assert isinstance(result.figure, FigureRequest)
    assert result.figure.metrics == ['max_girder_end_displacement']


def test_execute_inquiry_plan_keeps_other_queries_when_one_query_fails() -> None:
    content = _csv_bytes(['time', 'displacement'], [[0.0, 1.0], [0.5, -4.0]])
    service = AgentService()
    plan = InquiryPlan(
        queries=[
            InquiryQuery(op='peak', artifact='missing.csv', columns=['displacement']),
            InquiryQuery(op='peak', artifact='timeseries.csv', columns=['displacement']),
        ],
        reason='测试',
    )

    results = service._execute_inquiry_plan(
        plan,
        service=ResultInquiryService(_FakeStore(content)),
        artifacts={'timeseries.csv': 'art_csv_1'},
    )

    assert results[0]['error'] == 'ARTIFACT_NOT_FOUND'
    assert results[1]['data']['peakSigned'] == -4.0


def test_execute_inquiry_plan_caps_queries_at_eight() -> None:
    content = _csv_bytes(['time', 'displacement'], [[0.0, 1.0], [0.5, -4.0]])
    plan = InquiryPlan(
        queries=[InquiryQuery(op='peak', artifact='timeseries.csv', columns=['displacement']) for _ in range(10)],
        reason='测试',
    )

    results = AgentService()._execute_inquiry_plan(
        plan,
        service=ResultInquiryService(_FakeStore(content)),
        artifacts={'timeseries.csv': 'art_csv_1'},
    )

    assert len(results) == 8


def test_execute_inquiry_plan_records_invalid_correlate_without_aborting() -> None:
    content = _csv_bytes(['time', 'displacement'], [[0.0, 1.0], [0.5, -4.0]])
    plan = InquiryPlan(
        queries=[InquiryQuery(op='correlate', artifact='timeseries.csv', columns=['displacement'])],
        reason='测试',
    )

    results = AgentService()._execute_inquiry_plan(
        plan,
        service=ResultInquiryService(_FakeStore(content)),
        artifacts={'timeseries.csv': 'art_csv_1'},
    )

    assert '恰好两个列名' in results[0]['error']


def test_execute_inquiry_plan_supports_derived_comparison_operator() -> None:
    content = _csv_bytes(
        ['time', 'baseline', 'controlled'],
        [[0.0, 1.0, 2.0], [0.5, -4.0, -6.0], [1.0, 2.0, 3.0]],
    )
    plan = InquiryPlan(
        queries=[InquiryQuery(op='compare', artifact='timeseries.csv', columns=['baseline', 'controlled'])],
        reason='比较两列峰值',
    )

    results = AgentService()._execute_inquiry_plan(
        plan,
        service=ResultInquiryService(_FakeStore(content)),
        artifacts={'timeseries.csv': 'art_csv_1'},
    )

    assert results[0]['data']['relativeChangePercent'] == 50.0


class _InquiryRepository:
    def __init__(self, runs: list[dict], pending: dict | None = None, pending_approval: dict | None = None) -> None:
        self.session = {
            'sessionId': 'ags_inquiry',
            'title': '追问测试',
            'status': 'ACTIVE',
            'createdAt': '2026-01-01T00:00:00Z',
            'updatedAt': '2026-01-01T00:00:00Z',
        }
        self.runs = runs
        self.pending = pending
        self.pending_approval = pending_approval
        self.messages: list[dict] = []
        self.steps: list[dict] = []

    def get_session(self, session_id: str):
        return self.session if session_id == self.session['sessionId'] else None

    def find_import_by_file(self, _file_id: str):
        return None

    def find_pending_clarification_run(self, _session_id: str):
        return self.pending

    def find_pending_approval_run(self, _session_id: str):
        return self.pending_approval and self.pending_approval.get('run')

    def list_runs(self, _session_id: str):
        return list(self.runs)

    def add_message(self, message: dict) -> None:
        self.messages.append(message)

    def save_run(self, run: dict) -> None:
        self.runs.append(run)

    def save_step(self, step: dict) -> None:
        self.steps.append(step)

    def save_session(self, session: dict) -> None:
        self.session = session

    def list_steps(self, _run_id: str):
        return list(self.steps)

    def get_approval(self, _approval_id: str):
        return self.pending_approval and self.pending_approval.get('approval')


def _source_run() -> dict:
    return {
        'runId': 'agr_source',
        'sessionId': 'ags_inquiry',
        'taskType': 'ANALYSIS',
        'status': 'SUCCEEDED',
        'reportArtifactId': 'report_1',
        'artifactIds': ['art_csv_1'],
        'resultSummary': {'evidenceMode': 'REAL_FEM'},
    }


def test_refresh_backfills_job_artifacts_for_an_existing_report() -> None:
    class _Job:
        status = 'SUCCEEDED'

        def model_dump(self, **_kwargs):
            return {'artifacts': [{'artifactId': 'csv_timeseries'}]}

    class _PlatformStore:
        def refresh(self):
            return None

        def get_job(self, _job_id: str):
            return _Job()

    class _Repository:
        def __init__(self):
            self.saved = []

        def save_run(self, run: dict) -> None:
            self.saved.append(dict(run))

    run = {
        'runId': 'agr_existing',
        'jobId': 'job_existing',
        'taskType': 'DAMPER_OPTIMIZATION',
        'status': 'SUCCEEDED',
        'reportArtifactId': 'report_existing',
        'artifactIds': ['report_existing'],
    }
    repository = _Repository()
    service = AgentService()

    # 已有报告的旧 run 也要补回 Job 里登记的 CSV，保证后续追问可读。
    import importlib

    agent_service_module = importlib.import_module('app.services.agent_service')
    original_store = agent_service_module.platform_store
    agent_service_module.platform_store = _PlatformStore()
    try:
        refreshed = service._refresh_agent_run(repository, run)
    finally:
        agent_service_module.platform_store = original_store

    assert refreshed['artifactIds'] == ['report_existing', 'csv_timeseries']
    assert refreshed['status'] == 'SUCCEEDED'
    assert repository.saved[-1]['artifactIds'] == refreshed['artifactIds']


def test_inquiry_artifacts_falls_back_to_job_artifacts_for_legacy_runs() -> None:
    content = _csv_bytes(['time', 'displacement'], [[0.0, 1.0], [0.5, -4.0]])
    csv_store = _FakeStore(content)

    class _Job:
        def model_dump(self, **_kwargs):
            return {'artifacts': [{'artifactId': 'art_csv_1'}]}

    class _PlatformStore:
        def refresh(self):
            return None

        def get_job(self, _job_id: str):
            return _Job()

        def get_artifact(self, artifact_id: str):
            return csv_store.get_artifact(artifact_id)

    import importlib

    conversation_module = importlib.import_module('app.services.agent_conversation')
    original_store = conversation_module.platform_store
    conversation_module.platform_store = _PlatformStore()
    try:
        artifacts = AgentService()._inquiry_artifacts({'jobId': 'job_legacy', 'artifactIds': []})
    finally:
        conversation_module.platform_store = original_store

    assert artifacts == {'timeseries.csv': 'art_csv_1'}


def test_inquiry_artifacts_scopes_duplicate_csv_names_by_case_directory() -> None:
    content = _csv_bytes(['time', 'displacement'], [[0.0, 1.0], [0.5, -4.0]])
    records = {}
    for artifact_id, case_id in (('art_viscous', 'VISCOUS'), ('art_eddy', 'EDDY_CURRENT')):
        artifact = SimpleNamespace(
            artifact_id=artifact_id,
            kind='CSV_TIMESERIES',
            name='timeseries.csv',
            path=f'output/run_1/{case_id}/timeseries.csv',
            sha256=sha256(content).hexdigest(),
        )
        records[artifact_id] = SimpleNamespace(artifact=artifact, content=content)

    class _PlatformStore:
        def get_artifact(self, artifact_id: str):
            return records[artifact_id]

    import importlib

    conversation_module = importlib.import_module('app.services.agent_conversation')
    original_store = conversation_module.platform_store
    conversation_module.platform_store = _PlatformStore()
    try:
        artifacts = AgentService()._inquiry_artifacts({
            'artifactIds': ['art_viscous', 'art_eddy'],
        })
    finally:
        conversation_module.platform_store = original_store

    assert artifacts == {
        'VISCOUS/timeseries.csv': 'art_viscous',
        'EDDY_CURRENT/timeseries.csv': 'art_eddy',
    }


def test_create_message_without_terminal_run_does_not_plan_inquiry(monkeypatch) -> None:
    repo = _InquiryRepository([])
    service = AgentService()
    service.repository = lambda: repo
    called = {'inquiry': 0, 'task': 0}

    def plan_inquiry(*_args, **_kwargs):
        called['inquiry'] += 1
        raise AssertionError('没有终态结果时不应规划追问查询')

    def classify_task(*_args, **_kwargs):
        called['task'] += 1
        return SimpleNamespace(
            route_mode='LLM',
            route=SimpleNamespace(task_type='UNSUPPORTED', confidence=0.5, reason='test'),
        )

    service.planner = SimpleNamespace(
        plan_inquiry=plan_inquiry,
        classify_task=classify_task,
        respond_conversationally=lambda **_kwargs: '当前请求超出支持范围。',
    )
    result = service.create_message('ags_inquiry', '运行新任务', None)

    assert called == {'inquiry': 0, 'task': 1}
    assert result['taskType'] == 'UNSUPPORTED'


def test_create_message_with_terminal_run_creates_inquiry_and_persists_facts(monkeypatch) -> None:
    content = _csv_bytes(['time', 'displacement'], [[0.0, 1.0], [0.5, -4.0], [1.0, 2.0]])
    store = _FakeStore(content)
    repo = _InquiryRepository([_source_run(), {
        'runId': 'agr_prior_inquiry',
        'sessionId': 'ags_inquiry',
        'taskType': 'INQUIRY',
        'status': 'SUCCEEDED',
        'inquiryFacts': {'queries': [{'data': {'peakAbsolute': 3.0}}]},
    }])
    service = AgentService()
    service.repository = lambda: repo
    monkeypatch.setattr('app.services.agent_service.platform_store.get_artifact', store.get_artifact)
    called = {'plan_inquiry': 0, 'classify_task': 0}

    captured: dict = {}

    def plan_inquiry(*_args, **kwargs):
        called['plan_inquiry'] += 1
        captured.update(kwargs)
        return InquiryPlan(
            queries=[InquiryQuery(op='peak', artifact='timeseries.csv', columns=['displacement'])],
            reason='直接读取位移峰值',
        )

    def classify_task(*_args, **_kwargs):
        called['classify_task'] += 1
        raise AssertionError('已有终态结果时，追问不应进入主任务路由')

    service.planner = SimpleNamespace(
        plan_inquiry=plan_inquiry,
        classify_task=classify_task,
        explain_inquiry=lambda **_kwargs: NarrativeResult(
            narrativeMode='LLM',
            text='峰值为 4。',
        ),
    )

    result = service.create_message('ags_inquiry', '为什么位移改善有限？', None)

    assert result['taskType'] == 'INQUIRY'
    assert result['sourceRunId'] == 'agr_source'
    assert result['inquiryFacts']['queries'][0]['data']['peakAbsolute'] == 4.0
    assert result['inquiryPlan']['queries'][0]['op'] == 'peak'
    assert result['status'] == 'SUCCEEDED'
    assert any(message['role'] == 'ASSISTANT' for message in repo.messages)
    assert called == {'plan_inquiry': 1, 'classify_task': 0}
    assert captured['prior_results'] == [{'queries': [{'data': {'peakAbsolute': 3.0}}]}]
    assert result['inquiryFacts']['priorResults'] == captured['prior_results']


def test_create_message_with_terminal_run_renders_requested_figure(monkeypatch) -> None:
    content = _csv_bytes(['time', 'displacement'], [[0.0, 1.0], [0.5, -4.0], [1.0, 2.0]])
    store = _FakeStore(content)
    repo = _InquiryRepository([_source_run()])
    service = AgentService()
    service.repository = lambda: repo
    monkeypatch.setattr('app.services.agent_service.platform_store', store)
    monkeypatch.setattr('app.services.agent_conversation.platform_store', store)
    service.planner = SimpleNamespace(
        plan_inquiry=lambda **_kwargs: InquiryPlan(
            figure=FigureRequest(
                metrics=['max_girder_end_displacement'],
                artifact='timeseries.csv',
                claim='梁端位移时程',
            ),
            reason='按需绘制位移时程',
        ),
        explain_inquiry=lambda **_kwargs: NarrativeResult(
            narrativeMode='LLM',
            text='图展示梁端位移时程。',
        ),
    )

    result = service.create_message('ags_inquiry', '画一下位移时程', None)

    assert result['taskType'] == 'INQUIRY'
    assert len(result['figureArtifactIds']) == 1
    assert result['resultSummary']['figures'][0]['claim'] == '梁端位移时程'
    assert any(record.artifact.kind == 'PLOT' for record in store.artifacts)


def test_inquiry_catalog_caps_runs_and_summarizes_prior_results(monkeypatch) -> None:
    content = _csv_bytes(['time', 'displacement'], [[0.0, 1.0], [1.0, 2.0]])
    store = _FakeStore(content)
    runs = [
        {**_source_run(), 'runId': 'agr_source'},
        *[
            {
                **_source_run(),
                'runId': f'agr_{index}',
                'updatedAt': f'2026-01-01T00:0{index}:00Z',
            }
            for index in range(1, 8)
        ],
        {
            'runId': 'agr_prior_inquiry',
            'sessionId': 'ags_inquiry',
            'taskType': 'INQUIRY',
            'status': 'SUCCEEDED',
            'inquiryFacts': {
                'objectives': {'max_girder_end_displacement': 0.1},
                'queries': [{'op': 'peak', 'data': {'peakAbsolute': 2.0}}],
                'sampleResponses': [{'sampleIndex': 999, 'responses': {'value': 999}}],
            },
        },
    ]
    repo = _InquiryRepository(runs)
    service = AgentService()
    service.repository = lambda: repo
    monkeypatch.setattr('app.services.agent_service.platform_store.get_artifact', store.get_artifact)

    captured: dict = {}
    service.planner = SimpleNamespace(
        plan_inquiry=lambda **kwargs: captured.update(kwargs) or InquiryPlan(
            queries=[InquiryQuery(op='peak', artifact='timeseries.csv', columns=['displacement'])],
            reason='查询',
        ),
        explain_inquiry=lambda **_kwargs: NarrativeResult(narrativeMode='LLM', text='峰值为 2。'),
    )

    result = service.create_message('ags_inquiry', '位移峰值是多少？', None)

    assert result['taskType'] == 'INQUIRY'
    assert len(captured['catalog']['runs']) == 3
    assert 'agr_source' not in {item['runId'] for item in captured['catalog']['runs']}
    assert captured['prior_results'] == [{
        'objectives': {'max_girder_end_displacement': 0.1},
        'queries': [{'op': 'peak', 'data': {'peakAbsolute': 2.0}}],
    }]


def test_inquiry_planning_failure_creates_llm_unavailable_run(monkeypatch) -> None:
    repo = _InquiryRepository([_source_run()])
    service = AgentService()
    service.repository = lambda: repo
    monkeypatch.setattr(
        'app.services.agent_service.platform_store.get_artifact',
        lambda _artifact_id: (_ for _ in ()).throw(KeyError('csv unavailable')),
    )
    service.planner = SimpleNamespace(
        plan_inquiry=lambda **_kwargs: (_ for _ in ()).throw(
            LLMUnavailableError('INQUIRY_PLANNING', 'LLM_CONNECTION_FAILED')
        ),
        explain_inquiry=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError('llm unavailable')),
    )

    result = service.create_message('ags_inquiry', '为什么位移改善有限？', None)

    assert result['taskType'] == 'LLM_UNAVAILABLE'
    assert result['status'] == 'FAILED'
    assert result['llmFailure']['stage'] == 'INQUIRY_PLANNING'
    assert any(message['role'] == 'ASSISTANT' for message in repo.messages)


def test_terminal_result_can_fall_back_to_main_task_route_when_inquiry_is_out_of_scope() -> None:
    repo = _InquiryRepository([_source_run()])
    service = AgentService()
    service.repository = lambda: repo
    called = {'classify': 0}
    service.planner = SimpleNamespace(
        plan_inquiry=lambda **_kwargs: InquiryPlan(
            queries=[],
            needsFollowup=True,
            reason='问题要求新的求解任务，不属于已有结果查询',
        ),
        classify_task=lambda *_args, **_kwargs: (called.update(classify=called['classify'] + 1) or SimpleNamespace(
            route_mode='LLM',
            route=SimpleNamespace(task_type='UNSUPPORTED', confidence=0.9, reason='new task'),
        )),
        respond_conversationally=lambda **_kwargs: '当前请求将交回主任务路由。',
        explain_inquiry=lambda **_kwargs: NarrativeResult(
            narrativeMode='LLM',
            text='已有结果数据不足以回答该问题。',
        ),
    )

    result = service.create_message('ags_inquiry', '请导出新的荷载文件', None)

    assert result['taskType'] == 'UNSUPPORTED'
    assert called['classify'] == 1


def test_pending_clarification_takes_priority_over_inquiry() -> None:
    pending = {
        'runId': 'agr_pending',
        'sessionId': 'ags_inquiry',
        'taskType': 'ANALYSIS',
        'status': 'NEEDS_CLARIFICATION',
        'intent': {'taskType': 'CLARIFICATION', 'missingFields': ['loadKind']},
    }
    repo = _InquiryRepository([_source_run()], pending=pending)
    service = AgentService()
    service.repository = lambda: repo
    called = {'inquiry': 0}

    def plan_inquiry(*_args, **_kwargs):
        called['inquiry'] += 1
        raise AssertionError('澄清优先时不应进入追问')

    service.planner = SimpleNamespace(
        plan_inquiry=plan_inquiry,
        plan_engineering_clarification=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError('stop')),
    )

    with pytest.raises(RuntimeError, match='stop'):
        service.create_message('ags_inquiry', '地震荷载', None)
    assert called['inquiry'] == 0


def test_pending_approval_takes_priority_and_unclear_does_not_start_job() -> None:
    pending_run = {
        'runId': 'agr_pending_approval',
        'sessionId': 'ags_inquiry',
        'taskType': 'ANALYSIS',
        'status': 'WAITING_APPROVAL',
        'pendingApprovalId': 'approval_1',
        'artifactIds': [],
        'resultSummary': {'message': '待确认'},
    }
    approval = {
        'approvalId': 'approval_1',
        'runId': 'agr_pending_approval',
        'status': 'PENDING',
        'summary': '请确认是否执行。',
        'action': 'RUN_ENGINEERING_WORKFLOW',
    }
    repo = _InquiryRepository(
        [_source_run(), pending_run],
        pending_approval={'run': pending_run, 'approval': approval},
    )
    service = AgentService()
    service.repository = lambda: repo
    called = {'classify': 0, 'jobs': 0}
    service.planner = SimpleNamespace(
        classify_approval_reply=lambda *_args, **_kwargs: SimpleNamespace(
            decision='UNCLEAR',
            confidence=0.4,
            modifications=None,
            reason='不明确',
        ),
        classify_task=lambda *_args, **_kwargs: called.update(classify=called['classify'] + 1),
    )
    service._execute_approved_value = lambda *_args, **_kwargs: called.update(jobs=called['jobs'] + 1)

    result = service.create_message('ags_inquiry', '我再看看', None)

    assert result['runId'] == 'agr_pending_approval'
    assert result['status'] == 'WAITING_APPROVAL'
    assert called == {'classify': 0, 'jobs': 0}
    assert repo.messages[-1]['content'].startswith('未能确认您的意见')
