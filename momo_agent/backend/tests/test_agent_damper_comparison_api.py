from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.services.agent_service import agent_service
from app.services.agent_engineering import EngineeringIntent
from app.services.platform_dispatcher import platform_dispatcher
from app.services.platform_store import PlatformStore, platform_store


@pytest.fixture(autouse=True)
def isolated_platform_state(tmp_path: Path):
    isolated = PlatformStore(state_path=tmp_path / 'agent_comparison_state.sqlite3')
    original = (
        platform_store.state_path,
        platform_store.repository,
        platform_store.jobs,
        platform_store.artifacts,
        platform_store.engineering_config,
        platform_dispatcher.store,
    )
    platform_store.state_path = isolated.state_path
    platform_store.repository = isolated.repository
    platform_store.jobs = isolated.jobs
    platform_store.artifacts = isolated.artifacts
    platform_store.engineering_config = isolated.engineering_config
    platform_dispatcher.store = platform_store
    try:
        yield
    finally:
        platform_dispatcher.stop()
        (
            platform_store.state_path,
            platform_store.repository,
            platform_store.jobs,
            platform_store.artifacts,
            platform_store.engineering_config,
            platform_dispatcher.store,
        ) = original


@pytest.fixture(autouse=True)
def configured_engineering_planner(monkeypatch):
    """执行层 API 测试显式注入受控意图，避免依赖真实 LLM。"""
    monkeypatch.setattr(
        agent_service.planner,
        'plan_engineering',
        lambda _goal, *, requested_task='AUTO', **_kwargs: SimpleNamespace(
            planner_mode='LLM',
            intent=EngineeringIntent(
                taskType='DAMPER_COMPARISON',
                solver='ANSYS',
                damperTypes=['VISCOUS', 'EDDY_CURRENT'],
                loadKind='EARTHQUAKE',
                responseIds=['max_tower_base_shear', 'max_damper_force'],
                missingFields=[],
                summary='测试用阻尼器对比意图',
            ),
        ),
    )


client = TestClient(app)


def _ready_preflight(monkeypatch) -> None:
    monkeypatch.setattr(
        'app.services.agent_service.build_readiness_report',
        lambda *_args, **_kwargs: {'status': 'READY', 'blockingComponents': [], 'components': {}},
    )
    monkeypatch.setattr(
        'app.services.agent_service.preflight_config',
        lambda _path: {
            'kind': 'undamped_baseline',
            'solver': 'ansys',
            'execution_mode': 'run',
            'path_checks': {'model': {'exists': True}, 'load': {'exists': True}},
        },
    )
    monkeypatch.setattr(
        'app.services.agent_service.build_solver_version_profile',
        lambda *_args, **_kwargs: {
            'schemaVersion': '1.0',
            'solver': {'name': 'ANSYS_MAPDL', 'version': '2024 R2'},
            'responseContract': {'id': 'ANSYS_BEAM4_SMISC_MMOM_R4'},
            'userElement': {'name': 'USER300', 'calibrationHashVerified': True},
        },
    )


def _fake_case_runner(config_path: Path, *, execution_timeout_s: float | None) -> dict:
    config = json.loads(config_path.read_text(encoding='utf-8'))
    module = config['solver_kwargs']['damper_module']
    command_path = config_path.parent / f'{module}_executed.apdl'
    command_path.write_text(f'/PREP7\n! executed {module}\nSOLVE\n', encoding='utf-8')
    (config_path.parent / 'timeseries.csv').write_text(
        'time,max_girder_end_displacement\n0,0.1\n1,0.2\n',
        encoding='utf-8',
    )
    factor = 1.0 if module == 'damper_user300_viscous' else 0.9
    return {
        'case_id': module,
        'solver': 'ansys',
        'status': 'completed',
        'objectives': {
            'max_girder_end_displacement': 0.1 * factor,
            'max_tower_base_shear': 100.0 * factor,
            'max_damper_force': 4_000_000.0,
        },
        'timeseries': {},
        'metadata': {
            'execution_mode': 'run',
            'is_verified_solver_output': True,
            'command_stream': {'path': str(command_path)},
            'execution_timeout_s': execution_timeout_s,
        },
    }


def test_two_case_comparison_freezes_commands_and_creates_one_real_job(monkeypatch, tmp_path: Path) -> None:
    _ready_preflight(monkeypatch)
    monkeypatch.setattr(
        'app.services.platform_store.EARTHQUAKE_WORKFLOW_OUTPUT_ROOT',
        tmp_path / 'real_comparisons',
    )
    monkeypatch.setattr(platform_store, '_run_real_damper_comparison_case', _fake_case_runner)
    session = client.post('/api/v1/agent/sessions', json={'title': '阻尼器对比'}).json()

    run = client.post(
        f'/api/v1/agent/sessions/{session["sessionId"]}/messages',
        json={
            'content': '地震荷载下对比黏滞阻尼器和电涡流阻尼器，提取塔底剪力和阻尼器力',
            'taskType': 'DAMPER_COMPARISON',
        },
    ).json()

    assert run['taskType'] == 'DAMPER_COMPARISON'
    assert run['status'] == 'WAITING_APPROVAL'
    assert not any(job.request.get('agentRunId') == run['runId'] for job in platform_store.jobs)
    approval = run['pendingApproval']
    assert approval['action'] == 'RUN_DAMPER_COMPARISON'
    frozen = approval['frozenAction']
    assert frozen['runMode'] == 'REAL_DAMPER_COMPARISON'
    assert [case['damperType'] for case in frozen['cases']] == ['VISCOUS', 'EDDY_CURRENT']
    assert all(len(item['sha256']) == 64 for item in frozen['preExecutionCommandStreams'])
    assert all(item['phase'] == 'PRE_EXECUTION' for item in frozen['preExecutionCommandStreams'])

    first = client.post(
        f'/api/v1/agent/approvals/{approval["approvalId"]}/decision',
        json={'approved': True},
    ).json()['run']
    second = client.post(
        f'/api/v1/agent/approvals/{approval["approvalId"]}/decision',
        json={'approved': True},
    ).json()['run']
    assert first['jobId'] == second['jobId']
    assert len([job for job in platform_store.jobs if job.request.get('agentRunId') == run['runId']]) == 1

    platform_store.execute_queued_job(first['jobId'])
    completed = client.get(f'/api/v1/agent/runs/{run["runId"]}').json()
    assert completed['status'] == 'SUCCEEDED'
    assert completed['resultSummary']['evidenceMode'] == 'REAL_FEM'
    assert 'responseComparison' in completed['resultSummary']
    report = platform_store.get_artifact(completed['reportArtifactId']).preview
    assert report['isFinalResult'] is True
    assert report['comparisonBasis'] == 'EQUAL_PEAK_FORCE'
    assert {case['damperType'] for case in report['caseResults']} == {'VISCOUS', 'EDDY_CURRENT'}
    assert len([item for item in report['artifacts'] if item['kind'] == 'COMMAND_STREAM']) == 2


def test_compare_case_objectives_supports_three_cases_with_pairwise_deltas() -> None:
    comparison = platform_store._compare_case_objectives([
        {'caseId': 'viscous', 'objectives': {'max_tower_base_shear': 10.0}},
        {'caseId': 'friction', 'objectives': {'max_tower_base_shear': 8.0}},
        {'caseId': 'eddy', 'objectives': {'max_tower_base_shear': 12.0}},
    ])

    assert comparison['caseIds'] == ['viscous', 'friction', 'eddy']
    entry = comparison['metrics']['max_tower_base_shear']
    assert entry['viscous'] == 10.0
    assert entry['friction'] == 8.0
    assert entry['eddy'] == 12.0
    # 前两个工况的既有字段保持向后兼容。
    assert entry['secondMinusFirst'] == -2.0
    assert entry['relativeToFirst'] == -0.2
    pairs = {(item['baseCaseId'], item['otherCaseId']): item for item in entry['pairwise']}
    assert set(pairs) == {('viscous', 'friction'), ('viscous', 'eddy'), ('friction', 'eddy')}
    assert pairs[('friction', 'eddy')]['difference'] == 4.0
    assert pairs[('friction', 'eddy')]['relativeChange'] == 0.5


def test_compare_case_objectives_keeps_two_case_contract() -> None:
    comparison = platform_store._compare_case_objectives([
        {'caseId': 'viscous', 'objectives': {'m': 10.0}},
        {'caseId': 'eddy', 'objectives': {'m': 8.0, 'extra': 1.0}},
    ])

    entry = comparison['metrics']['m']
    assert entry['secondMinusFirst'] == -2.0
    assert entry['relativeToFirst'] == -0.2
    assert comparison['firstCaseId'] == 'viscous'
    assert comparison['secondCaseId'] == 'eddy'
    # 只对比共有指标。
    assert 'extra' not in comparison['metrics']


def test_comparison_reflection_rejects_unverified_case() -> None:
    reflection = agent_service._reflect_damper_comparison({
        'status': 'SUCCEEDED',
        'artifacts': [],
        'result': {
            'mode': 'real_damper_comparison',
            'caseResults': [
                {'damperType': 'VISCOUS', 'status': 'completed', 'isVerifiedSolverOutput': True},
                {'damperType': 'EDDY_CURRENT', 'status': 'completed', 'isVerifiedSolverOutput': False},
            ],
        },
    })

    assert reflection['runStatus'] == 'COMPLETED_DIAGNOSTIC'
    assert reflection['accepted'] is False


@pytest.mark.parametrize('status', ['FAILED', 'CANCELLED'])
def test_comparison_reflection_preserves_abnormal_terminal_status(status: str) -> None:
    reflection = agent_service._reflect_damper_comparison({
        'status': status,
        'artifacts': [],
        'result': {},
    })

    assert reflection['runStatus'] == status
    assert reflection['evidenceMode'] == status
    assert reflection['accepted'] is False


def test_platform_rejects_unregistered_comparison_module() -> None:
    with pytest.raises(HTTPException) as error:
        platform_store.create_job('SOLVER_BATCH', {
            'runMode': 'REAL_DAMPER_COMPARISON',
            'solver': 'ANSYS',
            'scenario': 'EARTHQUAKE',
            'comparisonBasis': 'EQUAL_PEAK_FORCE',
            'forceCapN': 4_000_000.0,
            'forceCapScope': 'PER_PHYSICAL_DAMPER',
            'designVelocityMps': 0.2,
            'selectedLayoutId': 'TWO_PER_TOWER',
            'selectedLayout': {
                'nodePairs': [[36, 517], [36, 518], [107, 520], [107, 521]],
                'direction': 'X',
                'physicalCountPerTower': 2,
            },
            'cases': [
                {
                    'damperType': 'VISCOUS',
                    'solverModule': 'arbitrary_module',
                    'theoreticalPeakForceN': 4_000_000.0,
                    'designVelocityMps': 0.2,
                    'parameterSource': 'VERIFIED_TEMPLATE',
                },
                {
                    'damperType': 'EDDY_CURRENT',
                    'solverModule': 'damper_eddy_current',
                    'theoreticalPeakForceN': 4_000_000.0,
                    'designVelocityMps': 0.2,
                    'parameterSource': 'VERIFIED_TEMPLATE',
                },
            ],
        })

    assert error.value.status_code == 422
    assert error.value.detail['code'] == 'INVALID_DAMPER_MODULE'
