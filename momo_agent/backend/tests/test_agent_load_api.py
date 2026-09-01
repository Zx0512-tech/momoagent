from __future__ import annotations

import csv
import json
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.main import app
from app.services.platform_dispatcher import platform_dispatcher
from app.services.platform_store import PlatformStore, platform_store
from app.services.agent_service import agent_service
from app.services.agent_engineering import EngineeringIntent


@pytest.fixture(autouse=True)
def isolated_platform_state(tmp_path: Path):
    isolated = PlatformStore(state_path=tmp_path / 'agent_state.sqlite3')
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
    def plan_engineering(_goal, *, requested_task='AUTO', **_kwargs):
        goal_text = str(_goal)
        task_type = requested_task if requested_task in {
            'ANALYSIS', 'DAMPER_OPTIMIZATION', 'DAMPER_COMPARISON',
        } else 'ANALYSIS'
        damper_type = (
            'FRICTION' if '摩擦' in goal_text else 'VISCOUS'
        ) if task_type == 'DAMPER_OPTIMIZATION' else None
        damper_types = ['VISCOUS', 'EDDY_CURRENT'] if task_type == 'DAMPER_COMPARISON' else []
        response_ids = ['max_tower_base_shear']
        return SimpleNamespace(
            planner_mode='LLM',
            intent=EngineeringIntent(
                taskType=task_type,
                solver='OPENSEESPY_INPROC' if 'OpenSees' in goal_text else 'ANSYS',
                damperType=damper_type,
                damperTypes=damper_types,
                loadKind='EARTHQUAKE',
                responseIds=response_ids,
                missingFields=[],
                summary='测试用受控工程意图',
            ),
        )

    monkeypatch.setattr(agent_service.planner, 'plan_engineering', plan_engineering)


client = TestClient(app)


def _create_run(file_name: str, content: bytes) -> tuple[dict, dict]:
    session = client.post('/api/v1/agent/sessions', json={'title': '荷载导入'}).json()
    uploaded = client.post(f'/api/v1/load-files?fileName={file_name}', content=content)
    assert uploaded.status_code == 201
    load_import = uploaded.json()
    run = client.post(
        f'/api/v1/agent/sessions/{session["sessionId"]}/messages',
        json={
            'content': '标准化这个荷载并准备单次求解',
            'fileId': load_import['fileId'],
            'taskType': 'LOAD_IMPORT',
        },
    ).json()
    return load_import, run


def _mapping(run_id: str, **patch) -> dict:
    payload = {
        'runId': run_id,
        'timeColumn': 'time',
        'timeUnit': 's',
        'valueColumn': 'load',
        'targetType': 'NODE',
        'targetId': '101',
        'component': 'UZ',
        'quantity': 'FORCE',
        'sourceUnit': 'kN',
        'solver': 'OPENSEESPY_INPROC',
    }
    payload.update(patch)
    return payload


def test_csv_standardization_is_approved_and_deterministic() -> None:
    load_import, run = _create_run('wind.csv', b'time,load\n0,1\n0.1,2\n')
    mapping = client.post(
        f'/api/v1/load-imports/{load_import["importId"]}/mapping',
        json=_mapping(run['runId']),
    )
    assert mapping.status_code == 200
    approval_id = mapping.json()['approval']['approvalId']

    approved = client.post(
        f'/api/v1/agent/approvals/{approval_id}/decision',
        json={'approved': True},
    )
    assert approved.status_code == 200
    current = approved.json()['run']
    assert current['currentStage'] == 'SOLVER_APPROVAL'
    standardized = next(
        platform_store.get_artifact(artifact_id).artifact
        for artifact_id in current['artifactIds']
        if platform_store.get_artifact(artifact_id).artifact.kind == 'CSV_TIMESERIES'
    )
    first_sha = standardized.sha256

    mapping_again = client.post(
        f'/api/v1/load-imports/{load_import["importId"]}/mapping',
        json=_mapping(run['runId']),
    ).json()
    client.post(
        f'/api/v1/agent/approvals/{mapping_again["approval"]["approvalId"]}/decision',
        json={'approved': True},
    )
    refreshed = client.get(f'/api/v1/load-imports/{load_import["importId"]}').json()
    assert platform_store.get_artifact(refreshed['standardArtifactId']).artifact.sha256 == first_sha


def test_single_column_requires_time_step() -> None:
    load_import, run = _create_run('earthquake.txt', b'acceleration\n0.1\n0.2\n')
    response = client.post(
        f'/api/v1/load-imports/{load_import["importId"]}/mapping',
        json=_mapping(
            run['runId'],
            timeColumn=None,
            valueColumn='acceleration',
            quantity='ACCELERATION',
            sourceUnit='g',
        ),
    )
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'TIME_STEP_REQUIRED'


def test_millisecond_time_column_is_converted_to_seconds() -> None:
    load_import, run = _create_run('wind.csv', b'time,load\n0,1\n100,2\n')
    mapping = client.post(
        f'/api/v1/load-imports/{load_import["importId"]}/mapping',
        json=_mapping(run['runId'], timeUnit='ms'),
    ).json()
    approved = client.post(
        f'/api/v1/agent/approvals/{mapping["approval"]["approvalId"]}/decision',
        json={'approved': True},
    ).json()['run']
    standard_artifact = next(
        platform_store.get_artifact(artifact_id)
        for artifact_id in approved['artifactIds']
        if platform_store.get_artifact(artifact_id).artifact.kind == 'CSV_TIMESERIES'
    )
    assert standard_artifact.content.decode('utf-8').splitlines()[-1].startswith('0.1,')


def test_xlsx_upload_profiles_first_sheet() -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = 'Wind'
    worksheet.append(['time', 'load'])
    worksheet.append([0, 1.5])
    worksheet.append([0.1, 2.5])
    stream = BytesIO()
    workbook.save(stream)

    response = client.post('/api/v1/load-files?fileName=wind.xlsx', content=stream.getvalue())
    assert response.status_code == 201
    inspection = response.json()['inspection']
    assert inspection['sheetName'] == 'Wind'
    assert inspection['rowCount'] == 2


def test_txt_upload_and_invalid_files_are_handled_without_artifacts() -> None:
    text_response = client.post(
        '/api/v1/load-files?fileName=wind.txt',
        content=b'time load\n0 1\n0.1 2\n',
    )
    assert text_response.status_code == 201
    assert text_response.json()['inspection']['delimiter'] == ' '

    artifact_count = len(platform_store.artifacts)
    for file_name, content, error_code in (
        ('empty.csv', b'', 'EMPTY_FILE'),
        ('load.json', b'{}', 'UNSUPPORTED_FILE_TYPE'),
        ('broken.xlsx', b'not-a-zip', 'INVALID_XLSX'),
    ):
        response = client.post(f'/api/v1/load-files?fileName={file_name}', content=content)
        assert response.status_code == 422
        assert response.json()['error']['code'] == error_code
    assert len(platform_store.artifacts) == artifact_count


def test_invalid_time_axis_does_not_create_standard_artifact() -> None:
    load_import, run = _create_run('wind.csv', b'time,load\n0,1\n0,2\n')
    artifact_count = len(platform_store.artifacts)
    response = client.post(
        f'/api/v1/load-imports/{load_import["importId"]}/mapping',
        json=_mapping(run['runId']),
    )
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'TIME_NOT_STRICTLY_INCREASING'
    assert len(platform_store.artifacts) == artifact_count


def test_unimplemented_load_import_solver_fails_before_creating_platform_job() -> None:
    load_import, run = _create_run('wind.csv', b'time,load\n0,1\n0.1,2\n')
    standardize = client.post(
        f'/api/v1/load-imports/{load_import["importId"]}/mapping',
        json=_mapping(run['runId']),
    ).json()
    after_standardize = client.post(
        f'/api/v1/agent/approvals/{standardize["approval"]["approvalId"]}/decision',
        json={'approved': True},
    ).json()['run']
    solver_approval_id = after_standardize['pendingApproval']['approvalId']
    job_count = len(platform_store.jobs)
    artifact_count = len(platform_store.artifacts)

    response = client.post(
        f'/api/v1/agent/approvals/{solver_approval_id}/decision',
        json={'approved': True},
    )

    assert response.status_code == 501
    assert response.json()['error']['code'] == 'CAPABILITY_NOT_IMPLEMENTED'
    assert len(platform_store.jobs) == job_count
    assert len(platform_store.artifacts) == artifact_count


def test_session_round_trip_includes_messages_and_runs() -> None:
    load_import, run = _create_run('wind.csv', b'time,load\n0,1\n0.1,2\n')
    session_id = run['sessionId']
    restored = client.get(f'/api/v1/agent/sessions/{session_id}')
    assert restored.status_code == 200
    payload = restored.json()
    assert [item['runId'] for item in payload['runs']] == [run['runId']]
    assert [message['role'] for message in payload['messages']] == ['USER', 'ASSISTANT']
    assert load_import['fileArtifactId'] in payload['runs'][0]['artifactIds']


def test_delete_empty_session_removes_it_from_history() -> None:
    session = client.post('/api/v1/agent/sessions', json={'title': '临时测试会话'}).json()

    deleted = client.delete(f'/api/v1/agent/sessions/{session["sessionId"]}')

    assert deleted.status_code == 200
    assert deleted.json() == {
        'sessionId': session['sessionId'],
        'deleted': True,
        'retainedRunCount': 0,
        'cancelledRunCount': 0,
    }
    assert client.get(f'/api/v1/agent/sessions/{session["sessionId"]}').status_code == 404


def test_attachment_stays_inside_damper_optimization_run() -> None:
    session = client.post('/api/v1/agent/sessions', json={'title': '附件优化'}).json()
    uploaded = client.post(
        '/api/v1/load-files?fileName=earthquake.csv',
        content=b'time,acc_x\n0,0.1\n0.1,0.2\n',
    ).json()

    run = client.post(
        f'/api/v1/agent/sessions/{session["sessionId"]}/messages',
        json={
            'content': '使用黏滞阻尼器优化附件地震荷载并提取塔底剪力',
            'fileId': uploaded['fileId'],
            'taskType': 'DAMPER_OPTIMIZATION',
        },
    ).json()

    assert run['taskType'] == 'DAMPER_OPTIMIZATION'
    assert run['status'] == 'WAITING_MAPPING'
    assert run['currentStage'] == 'LOAD_MAPPING'
    assert run['intent']['damperType'] == 'VISCOUS'
    assert run['intent']['loadKind'] == 'EARTHQUAKE'
    assert run['workflowContract']['loadArtifactId'] is None


def test_multichannel_mapping_writes_v2_standard_load_artifact() -> None:
    session = client.post('/api/v1/agent/sessions', json={'title': '多通道荷载'}).json()
    uploaded = client.post(
        '/api/v1/load-files?fileName=forces.csv',
        content=b'time,node36,node107\n0,1,2\n0.1,3,4\n',
    ).json()
    run = client.post(
        f'/api/v1/agent/sessions/{session["sessionId"]}/messages',
        json={'content': '使用附件节点荷载分析位移', 'fileId': uploaded['fileId'], 'taskType': 'ANALYSIS'},
    ).json()

    response = client.post(
        f'/api/v1/load-imports/{uploaded["importId"]}/mapping',
        json={
            'runId': run['runId'],
            'loadKind': 'GENERIC_NODAL',
            'time': {'column': 'time', 'unit': 's'},
            'channels': [
                {
                    'valueColumn': 'node36',
                    'applicationType': 'NODAL_FORCE',
                    'targetType': 'NODE',
                    'targetId': '36',
                    'component': 'UX',
                    'quantity': 'FORCE',
                    'sourceUnit': 'kN',
                },
                {
                    'valueColumn': 'node107',
                    'applicationType': 'NODAL_FORCE',
                    'targetType': 'NODE',
                    'targetId': '107',
                    'component': 'UX',
                    'quantity': 'FORCE',
                    'sourceUnit': 'kN',
                },
            ],
        },
    )

    assert response.status_code == 200
    approval_id = response.json()['approval']['approvalId']
    approved = client.post(
        f'/api/v1/agent/approvals/{approval_id}/decision',
        json={'approved': True},
    ).json()['run']
    artifact = next(
        platform_store.get_artifact(artifact_id)
        for artifact_id in approved['artifactIds']
        if platform_store.get_artifact(artifact_id).artifact.kind == 'CSV_TIMESERIES'
    )
    lines = artifact.content.decode('utf-8').splitlines()
    assert lines[0] == (
        'time_s,load_kind,channel_id,application_type,target_type,target_id,'
        'component,quantity,value,unit'
    )
    assert len(lines) == 5


def test_optimization_freezes_standard_load_before_creating_real_job(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        'app.services.platform_store.EARTHQUAKE_WORKFLOW_OUTPUT_ROOT',
        tmp_path / 'real_workflows',
    )
    monkeypatch.setattr(
        'app.services.agent_service.build_readiness_report',
        lambda *_args, **_kwargs: {'status': 'READY', 'blockingComponents': [], 'components': {}},
    )
    monkeypatch.setattr(
        'app.services.agent_service.preflight_config',
        lambda _path: {
            'kind': 'baseline_optimization_workflow',
            'baseline': {
                'solver': 'ansys',
                'execution_mode': 'run',
                'path_checks': {'model': {'exists': True}},
            },
            'optimization': {
                'solver': 'ansys',
                'execution_mode': 'run',
                'path_checks': {'mapdl': {'exists': True}},
            },
        },
    )
    session = client.post('/api/v1/agent/sessions', json={'title': '附件地震优化'}).json()
    uploaded = client.post(
        '/api/v1/load-files?fileName=earthquake.csv',
        content=b'time,acc_x\n0,0.1\n0.1,0.2\n',
    ).json()
    run = client.post(
        f'/api/v1/agent/sessions/{session["sessionId"]}/messages',
        json={
            'content': '使用黏滞阻尼器优化附件地震荷载并提取塔底剪力',
            'fileId': uploaded['fileId'],
            'taskType': 'DAMPER_OPTIMIZATION',
        },
    ).json()
    mapping = client.post(
        f'/api/v1/load-imports/{uploaded["importId"]}/mapping',
        json={
            'runId': run['runId'],
            'loadKind': 'EARTHQUAKE',
            'time': {'column': 'time', 'unit': 's'},
            'channels': [{
                'valueColumn': 'acc_x',
                'applicationType': 'UNIFORM_EXCITATION',
                'component': 'UX',
                'quantity': 'ACCELERATION',
                'sourceUnit': 'g',
            }],
        },
    ).json()

    standardized = client.post(
        f'/api/v1/agent/approvals/{mapping["approval"]["approvalId"]}/decision',
        json={'approved': True},
    ).json()['run']

    approval = standardized['pendingApproval']
    frozen = approval['frozenAction']
    assert approval['action'] == 'RUN_ENGINEERING_WORKFLOW'
    assert frozen['runMode'] == 'REAL_BASELINE_OPTIMIZATION'
    assert frozen['damper']['type'] == 'VISCOUS'
    assert frozen['loadKind'] == 'EARTHQUAKE'
    assert frozen['loadDatasetArtifactId'] in standardized['artifactIds']
    assert len(frozen['loadDatasetSha256']) == 64
    assert frozen['budget']['doeDesignCount'] == 15
    assert frozen['selectedLayoutId'] == 'TWO_PER_TOWER'
    assert len(frozen['layoutRegistrySha256']) == 64
    assert not any(job.request.get('agentRunId') == run['runId'] for job in platform_store.jobs)

    first = client.post(
        f'/api/v1/agent/approvals/{approval["approvalId"]}/decision',
        json={'approved': True},
    ).json()['run']
    second = client.post(
        f'/api/v1/agent/approvals/{approval["approvalId"]}/decision',
        json={'approved': True},
    ).json()['run']
    assert first['jobId'] == second['jobId']
    job = platform_store.get_job(first['jobId'])
    assert job.type == 'MULTI_OBJECTIVE_OPTIMIZATION'
    assert job.request['loadDatasetArtifactId'] == frozen['loadDatasetArtifactId']
    assert len([item for item in platform_store.jobs if item.request.get('agentRunId') == run['runId']]) == 1

    source = platform_store._baseline_optimization_workflow_config_path(job.request)
    prepared = platform_store._prepare_earthquake_baseline_optimization_workflow(source, job.request)
    baseline_config = json.loads(prepared.baseline_config_path.read_text(encoding='utf-8'))
    optimization_config = json.loads(prepared.optimization_config_path.read_text(encoding='utf-8'))
    solver_input = Path(baseline_config['load_case']['path'])
    assert solver_input.read_text(encoding='utf-8').splitlines() == ['0.980665', '1.96133']
    assert optimization_config['load_cases'][0]['path'] == str(solver_input)
    assert optimization_config['solver_kwargs']['physical_count_per_tower'] == 2
    assert baseline_config['load_case']['metadata']['agent_standard_load']['artifactId'] == frozen['loadDatasetArtifactId']


def test_uncalibrated_damper_is_blocked_before_execution_approval() -> None:
    session = client.post('/api/v1/agent/sessions', json={'title': '摩擦阻尼优化'}).json()
    uploaded = client.post(
        '/api/v1/load-files?fileName=earthquake.csv',
        content=b'time,acc_x\n0,0.1\n0.1,0.2\n',
    ).json()
    run = client.post(
        f'/api/v1/agent/sessions/{session["sessionId"]}/messages',
        json={
            'content': '使用摩擦阻尼器优化附件地震荷载',
            'fileId': uploaded['fileId'],
            'taskType': 'DAMPER_OPTIMIZATION',
        },
    ).json()
    mapping = client.post(
        f'/api/v1/load-imports/{uploaded["importId"]}/mapping',
        json={
            'runId': run['runId'],
            'loadKind': 'EARTHQUAKE',
            'time': {'column': 'time', 'unit': 's'},
            'channels': [{
                'valueColumn': 'acc_x',
                'applicationType': 'UNIFORM_EXCITATION',
                'component': 'UX',
                'quantity': 'ACCELERATION',
                'sourceUnit': 'g',
            }],
        },
    ).json()
    blocked = client.post(
        f'/api/v1/agent/approvals/{mapping["approval"]["approvalId"]}/decision',
        json={'approved': True},
    ).json()['run']

    assert blocked['status'] == 'UNSUPPORTED'
    assert blocked['currentStage'] == 'PREFLIGHT'
    assert blocked['pendingApproval'] is None
    assert '校准' in blocked['resultSummary']['message']
    assert not any(job.request.get('agentRunId') == run['runId'] for job in platform_store.jobs)


def test_analysis_freezes_real_solver_job_after_load_mapping(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        'app.services.platform_store.EARTHQUAKE_WORKFLOW_OUTPUT_ROOT',
        tmp_path / 'real_analyses',
    )
    def fake_run_real_agent_analysis(config_path: Path, *, execution_timeout_s: float | None) -> dict:
        run_dir = config_path.parent
        (run_dir / 'timeseries.csv').write_text(
            'time,tower_base_shear\n0,10\n1,12\n',
            encoding='utf-8',
        )
        (run_dir / 'tower_base_shear_components.csv').write_text(
            'time,section_relative,inertia_relative\n0,4,6\n1,5,7\n',
            encoding='utf-8',
        )
        return {
            'case_id': 'case_real_analysis',
            'solver': 'openseespy_inproc',
            'status': 'completed',
            'objectives': {'max_tower_base_shear': 123.0},
            'timeseries': {},
            'metadata': {'execution_mode': 'run', 'execution_timeout_s': execution_timeout_s},
        }

    monkeypatch.setattr(platform_store, '_run_real_agent_analysis', fake_run_real_agent_analysis)
    monkeypatch.setattr(
        'app.services.agent_service.build_readiness_report',
        lambda *_args, **_kwargs: {'status': 'READY', 'blockingComponents': [], 'components': {}},
    )
    monkeypatch.setattr(
        'app.services.agent_service.preflight_config',
        lambda _path: {
            'kind': 'undamped_baseline',
            'solver': 'openseespy_inproc',
            'execution_mode': 'run',
            'path_checks': {'model': {'exists': True}},
        },
    )
    session = client.post('/api/v1/agent/sessions', json={'title': '附件地震分析'}).json()
    uploaded = client.post(
        '/api/v1/load-files?fileName=earthquake.csv',
        content=b'time,acc_x\n0,0.1\n0.1,0.2\n',
    ).json()
    run = client.post(
        f'/api/v1/agent/sessions/{session["sessionId"]}/messages',
        json={
            'content': '使用 OpenSeesPy 分析附件地震荷载并提取塔底剪力',
            'fileId': uploaded['fileId'],
            'taskType': 'ANALYSIS',
        },
    ).json()
    assert run['agentRuntime'] == {
        'name': 'MOMO_TYPED_AGENT',
        'version': '1',
        'recentMessageCount': 1,
        'tools': ['analysis.preflight', 'analysis.dispatch', 'analysis.review'],
    }
    mapping = client.post(
        f'/api/v1/load-imports/{uploaded["importId"]}/mapping',
        json={
            'runId': run['runId'],
            'loadKind': 'EARTHQUAKE',
            'time': {'column': 'time', 'unit': 's'},
            'channels': [{
                'valueColumn': 'acc_x',
                'applicationType': 'UNIFORM_EXCITATION',
                'component': 'UX',
                'quantity': 'ACCELERATION',
                'sourceUnit': 'g',
            }],
            'solver': 'OPENSEESPY_INPROC',
        },
    ).json()

    standardized = client.post(
        f'/api/v1/agent/approvals/{mapping["approval"]["approvalId"]}/decision',
        json={'approved': True},
    ).json()['run']
    approval = standardized['pendingApproval']
    assert approval['action'] == 'RUN_SOLVER'
    assert approval['frozenAction']['runMode'] == 'REAL_AGENT_ANALYSIS'
    assert approval['frozenAction']['solver'] == 'OPENSEESPY_INPROC'
    assert approval['frozenAction']['responseIds'] == ['max_tower_base_shear']
    assert len(approval['frozenAction']['loadDatasetSha256']) == 64
    assert approval['frozenAction']['solverVersionProfile']['responseContract']['id'] == (
        'OPENSEESPY_SECTION_RELATIVE_R1'
    )
    assert next(
        item for item in approval['frozenAction']['inputProvenance']
        if item['field'] == 'loadDataset'
    )['source'] == 'FILE_DERIVED'
    assert standardized['solverVersionProfile'] == approval['frozenAction']['solverVersionProfile']
    assert standardized['inputProvenance'] == approval['frozenAction']['inputProvenance']
    assert not any(job.request.get('agentRunId') == run['runId'] for job in platform_store.jobs)

    waiting = client.post(
        f'/api/v1/agent/approvals/{approval["approvalId"]}/decision',
        json={'approved': True},
    ).json()['run']
    job = platform_store.get_job(waiting['jobId'])
    assert job.type == 'SOLVER_BATCH'
    assert job.request['runMode'] == 'REAL_AGENT_ANALYSIS'

    platform_store.execute_queued_job(job.job_id)
    completed = client.get(f'/api/v1/agent/runs/{run["runId"]}').json()
    assert completed['status'] == 'SUCCEEDED', platform_store.get_job(job.job_id).error
    assert completed['resultSummary']['evidenceMode'] == 'REAL_FEM'
    report = platform_store.get_artifact(completed['reportArtifactId']).preview
    assert report['isFinalResult'] is True
    assert {item['name'] for item in report['artifacts']} == {
        'real_analysis_summary.json',
        'real_analysis_overview.json',
        'timeseries.csv',
        'tower_base_shear_components.csv',
        'result_catalog.json',
        'real_output_manifest.json',
    }
    assert report['solverVersionProfile']['solver']['name'] == 'OPENSEESPY_INPROC'
    assert report['inputProvenance']
    assert completed['outputManifestArtifactId'] == report['outputManifestArtifactId']


def test_analysis_without_upload_freezes_bundled_earthquake_artifact(monkeypatch) -> None:
    monkeypatch.setattr(
        'app.services.agent_service.build_readiness_report',
        lambda *_args, **_kwargs: {'status': 'READY', 'blockingComponents': [], 'components': {}},
    )
    monkeypatch.setattr(
        'app.services.agent_service.preflight_config',
        lambda _path: {
            'kind': 'undamped_baseline',
            'solver': 'openseespy_inproc',
            'execution_mode': 'run',
            'path_checks': {'model': {'exists': True}},
        },
    )
    session = client.post('/api/v1/agent/sessions', json={'title': '内置地震荷载'}).json()

    run = client.post(
        f'/api/v1/agent/sessions/{session["sessionId"]}/messages',
        json={
            'content': '使用 OpenSeesPy 对内置地震记录做无阻尼分析并提取塔底剪力',
            'taskType': 'ANALYSIS',
        },
    ).json()

    frozen = run['pendingApproval']['frozenAction']
    artifact_id = frozen['loadDatasetArtifactId']
    assert artifact_id in run['artifactIds']
    assert frozen['loadMapping']['source'] == 'BUNDLED_PROJECT_DATA'
    assert frozen['loadMapping']['time'] == {'column': None, 'stepS': 0.01, 'unit': 's'}
    assert frozen['loadMapping']['channels'] == [{
        'valueColumn': 'column_1',
        'applicationType': 'UNIFORM_EXCITATION',
        'targetType': None,
        'targetId': None,
        'component': 'UX',
        'quantity': 'ACCELERATION',
        'sourceUnit': 'm/s2',
        'scale': 1.0,
    }]

    artifact = platform_store.get_artifact(artifact_id)
    rows = list(csv.DictReader(artifact.content.decode('utf-8').splitlines()))
    source_path = Path(__file__).resolve().parents[3] / (
        'analysis_data/earthquake_inputs/earthquake_acceleration_record.txt'
    )
    assert len(rows) == 4001
    assert float(rows[0]['time_s']) == 0.0
    assert float(rows[-1]['time_s']) == 40.0
    assert {row['unit'] for row in rows} == {'m/s2'}
    assert artifact.preview['sourceSha256'] == sha256(source_path.read_bytes()).hexdigest()
    assert artifact.preview['sourcePath'] == 'analysis_data/earthquake_inputs/earthquake_acceleration_record.txt'
    assert frozen['loadDatasetSha256'] == artifact.artifact.sha256
    assert next(
        item for item in frozen['inputProvenance'] if item['field'] == 'loadDataset'
    )['source'] == 'BUNDLED_PROJECT_DATA'


def test_engineering_reflection_requires_frozen_load_and_layout_evidence() -> None:
    artifacts = []
    for name in (
        'real_workflow_summary.json',
        'real_optimization_summary.json',
        'real_baseline_summary.json',
        'real_earthquake_workflow_overview.json',
        'real_output_manifest.json',
    ):
        preview = (
            {
                'schemaVersion': '1.0',
                'rootDirectory': 'test',
                'fileCount': 1,
                'totalBytes': 2,
                'files': [{'path': 'summary.json', 'sizeBytes': 2, 'sha256': 'a' * 64}],
            }
            if name == 'real_output_manifest.json'
            else {'mode': 'real_baseline_optimization'}
        )
        artifacts.append(platform_store.register_artifact(
            kind='JSON_SUMMARY',
            name=name,
            path=f'output/test/{name}',
            mime_type='application/json',
            preview=preview,
            content=json.dumps(preview).encode('utf-8'),
        ).model_dump(by_alias=True))
    manifest_artifact = next(item for item in artifacts if item['name'] == 'real_output_manifest.json')
    load_evidence = {'artifactId': 'art_load', 'sha256': 'a' * 64}
    layout = {
        'nodePairs': [[36, 517], [36, 518], [107, 520], [107, 521]],
        'direction': 'X',
        'physicalCountPerTower': 2,
    }
    job = {
        'status': 'SUCCEEDED',
        'artifacts': artifacts,
        'result': {
            'mode': 'real_baseline_optimization',
            'baselineStatus': 'completed',
            'validationStatus': {'all_verified_execution': True, 'all_accepted': True},
            'reviewStatus': {'all_verified_execution': True, 'all_accepted': True},
            'finalRecommendationStatus': 'ACCEPTED',
            'customLoadEvidence': load_evidence,
            'damperLayoutEvidence': {'layoutId': 'TWO_PER_TOWER', **layout},
            'solverVersionProfile': {
                'schemaVersion': '1.0',
                'solver': {'name': 'ANSYS_MAPDL', 'version': '2024 R2'},
                'responseContract': {'id': 'ANSYS_BEAM4_SMISC_MMOM_R4'},
                'userElement': {
                    'name': 'USER300',
                    'calibrationHashVerified': True,
                },
            },
            'inputProvenance': [
                {'field': 'solver', 'source': 'USER_DECISION', 'value': 'ANSYS'},
                {
                    'field': 'workflowConfigPath',
                    'source': 'VERIFIED_TEMPLATE',
                    'value': 'workflow.json',
                },
                {
                    'field': 'loadDataset',
                    'source': 'FILE_DERIVED',
                    'value': load_evidence,
                },
            ],
            'outputManifestArtifactId': manifest_artifact['artifactId'],
            'outputManifestSha256': manifest_artifact['sha256'],
        },
    }
    run = {
        'taskType': 'DAMPER_OPTIMIZATION',
        'workflowContract': {
            'loadArtifactId': 'art_load',
            'loadSha256': 'a' * 64,
            'selectedLayoutId': 'TWO_PER_TOWER',
            'selectedLayout': layout,
        },
    }

    accepted = agent_service._reflect_full_optimization(job, run=run)
    assert accepted['runStatus'] == 'SUCCEEDED'
    assert accepted['checks']['approvedLoadArtifact'] is True
    assert accepted['checks']['approvedDamperLayout'] is True

    job['result']['customLoadEvidence']['sha256'] = 'b' * 64
    rejected = agent_service._reflect_full_optimization(job, run=run)
    assert rejected['runStatus'] == 'COMPLETED_DIAGNOSTIC'
    assert rejected['checks']['approvedLoadArtifact'] is False


# ---------------------------------------------------------------------------
# 风荷载 × ANSYS 单次分析纵向切片
# ---------------------------------------------------------------------------

WIND_SOURCE_CSV = b'time,wind_fy\n0,100\n1,120\n2,90\n'
WIND_DECK_TARGET_SET = 'STBRIDGE_WIND_DECK_NODES'


def _configure_wind_analysis(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        'app.services.platform_store.EARTHQUAKE_WORKFLOW_OUTPUT_ROOT',
        tmp_path / 'real_wind_analyses',
    )
    monkeypatch.setattr(
        'app.services.agent_service.build_readiness_report',
        lambda *_args, **_kwargs: {'status': 'READY', 'blockingComponents': [], 'components': {}},
    )
    monkeypatch.setattr(
        'app.services.agent_service.preflight_config',
        lambda _path: {
            'kind': 'undamped_baseline',
            'solver': 'ansys',
            'load_type': 'wind',
            'execution_mode': 'run',
            'path_checks': {'model': {'exists': True}},
        },
    )

    def plan_wind(_goal, *, requested_task='AUTO', **_kwargs):
        return SimpleNamespace(
            planner_mode='LLM',
            intent=EngineeringIntent(
                taskType='ANALYSIS',
                solver='ANSYS',
                loadKind='WIND',
                responseIds=['max_girder_end_displacement'],
                missingFields=[],
                summary='受控风荷载分析意图',
            ),
        )

    monkeypatch.setattr(agent_service.planner, 'plan_engineering', plan_wind)


def _fake_wind_solver(monkeypatch) -> None:
    def fake_run_real_agent_analysis(config_path: Path, *, execution_timeout_s: float | None) -> dict:
        run_dir = config_path.parent
        (run_dir / 'timeseries.csv').write_text(
            'time,girder_end_displacement\n0,0.01\n1,0.02\n2,0.015\n',
            encoding='utf-8',
        )
        return {
            'case_id': 'case_real_wind_analysis',
            'solver': 'ansys',
            'status': 'completed',
            'objectives': {'max_girder_end_displacement': 0.02},
            'timeseries': {},
            'metadata': {'execution_mode': 'run', 'execution_timeout_s': execution_timeout_s},
        }

    monkeypatch.setattr(platform_store, '_run_real_agent_analysis', fake_run_real_agent_analysis)


def _start_wind_run() -> tuple[dict, dict]:
    session = client.post('/api/v1/agent/sessions', json={'title': '风荷载分析'}).json()
    uploaded = client.post('/api/v1/load-files?fileName=wind.csv', content=WIND_SOURCE_CSV).json()
    run = client.post(
        f'/api/v1/agent/sessions/{session["sessionId"]}/messages',
        json={
            'content': '使用 ANSYS 分析附件风荷载并提取主梁梁端位移',
            'fileId': uploaded['fileId'],
            'taskType': 'ANALYSIS',
        },
    ).json()
    return uploaded, run


def _wind_mapping_payload(run_id: str, **channel_patch) -> dict:
    channel = {
        'valueColumn': 'wind_fy',
        'applicationType': 'NODAL_FORCE',
        'targetType': 'NODE_GROUP',
        'targetId': WIND_DECK_TARGET_SET,
        'component': 'UY',
        'quantity': 'FORCE',
        'sourceUnit': 'N',
    }
    channel.update(channel_patch)
    return {
        'runId': run_id,
        'loadKind': 'WIND',
        'time': {'column': 'time', 'unit': 's'},
        'channels': [channel],
        'solver': 'ANSYS',
    }


def _approve_wind_load(uploaded: dict, run: dict, **channel_patch) -> dict:
    mapping = client.post(
        f'/api/v1/load-imports/{uploaded["importId"]}/mapping',
        json=_wind_mapping_payload(run['runId'], **channel_patch),
    ).json()
    return client.post(
        f'/api/v1/agent/approvals/{mapping["approval"]["approvalId"]}/decision',
        json={'approved': True},
    ).json()['run']


def test_wind_analysis_runs_real_ansys_job_from_frozen_load_artifact(monkeypatch, tmp_path: Path) -> None:
    _configure_wind_analysis(monkeypatch, tmp_path)
    _fake_wind_solver(monkeypatch)
    uploaded, run = _start_wind_run()

    standardized = _approve_wind_load(uploaded, run)
    approval = standardized['pendingApproval']
    frozen = approval['frozenAction']

    assert approval['action'] == 'RUN_SOLVER'
    assert frozen['runMode'] == 'REAL_AGENT_ANALYSIS'
    assert frozen['solver'] == 'ANSYS'
    assert frozen['loadKind'] == 'WIND'
    assert frozen['loadTargetSetId'] == WIND_DECK_TARGET_SET
    assert frozen['workflowConfigPath'] == 'docs/examples/templates/ansys_run_wind_baseline_template.json'
    assert len(frozen['loadDatasetSha256']) == 64
    assert next(
        item for item in frozen['inputProvenance'] if item['field'] == 'loadTargetSetId'
    )['value'] == WIND_DECK_TARGET_SET
    assert not any(job.request.get('agentRunId') == run['runId'] for job in platform_store.jobs)

    waiting = client.post(
        f'/api/v1/agent/approvals/{approval["approvalId"]}/decision',
        json={'approved': True},
    ).json()['run']
    job = platform_store.get_job(waiting['jobId'])
    assert job.type == 'SOLVER_BATCH'
    assert job.request['loadKind'] == 'WIND'
    assert job.request['loadTargetSetId'] == WIND_DECK_TARGET_SET

    platform_store.execute_queued_job(job.job_id)
    completed = client.get(f'/api/v1/agent/runs/{run["runId"]}').json()

    assert completed['status'] == 'SUCCEEDED', platform_store.get_job(job.job_id).error
    assert completed['resultSummary']['evidenceMode'] == 'REAL_FEM'
    report = platform_store.get_artifact(completed['reportArtifactId']).preview
    assert report['isFinalResult'] is True
    assert {item['name'] for item in report['artifacts']} == {
        'real_analysis_summary.json',
        'real_analysis_overview.json',
        'timeseries.csv',
        'result_catalog.json',
        'real_output_manifest.json',
    }
    summary = next(
        platform_store.get_artifact(item['artifactId']).preview
        for item in report['artifacts']
        if item['name'] == 'real_analysis_summary.json'
    )
    load_evidence = summary['customLoadEvidence']
    assert load_evidence['artifactId'] == frozen['loadDatasetArtifactId']
    assert load_evidence['sha256'] == frozen['loadDatasetSha256']
    assert load_evidence['targetSetId'] == WIND_DECK_TARGET_SET
    assert load_evidence['targetNodes'] == [1, 23, 43, 61, 132, 114, 94, 72]
    assert load_evidence['unit'] == 'N'
    assert load_evidence['timeStepS'] == 1.0
    solver_input = Path(load_evidence['solverInputPath'])
    assert solver_input.read_text(encoding='utf-8').splitlines() == ['100', '120', '90']


# 逐节点风荷载上传件：列名里显式声明了单位（N），属于文件内容里的声明，够格
# 跳过映射确认。列序故意按节点号升序给，与目标集登记顺序不同，用来钉住重排。
WIND_PER_NODE_NODES = (1, 23, 43, 61, 132, 114, 94, 72)
WIND_PER_NODE_CSV = (
    ','.join(['time', *[f'fy_node_{node}(N)' for node in sorted(WIND_PER_NODE_NODES)]])
    + '\n'
    + '\n'.join(
        ','.join([f'{index}', *[f'{100 + index + offset}' for offset in range(len(WIND_PER_NODE_NODES))]])
        for index in range(4)
    )
    + '\n'
).encode('utf-8')


def test_uploaded_per_node_wind_file_auto_standardizes_into_ordered_channels(
    monkeypatch, tmp_path: Path
) -> None:
    """逐节点风文件自带单位声明时直接生成标准荷载，通道按目标集登记顺序排列。

    钉的是 _try_auto_standardize 里按工况类型重算建议这条接线：inspect() 不知道
    工况类型，填的是地震形状的单通道加速度建议，只有走到风分支才会得到 8 通道。
    """

    _configure_wind_analysis(monkeypatch, tmp_path)
    session = client.post('/api/v1/agent/sessions', json={'title': '逐节点风荷载'}).json()
    uploaded = client.post(
        '/api/v1/load-files?fileName=wind_per_node.csv', content=WIND_PER_NODE_CSV
    ).json()

    run = client.post(
        f'/api/v1/agent/sessions/{session["sessionId"]}/messages',
        json={
            'content': '使用 ANSYS 分析附件逐节点风荷载并提取主梁梁端位移',
            'fileId': uploaded['fileId'],
            'taskType': 'ANALYSIS',
        },
    ).json()

    # 标准化已自动完成，停在下一道独立的求解审批上——人工闸门没有被绕过。
    approval = run['pendingApproval']
    assert approval['action'] == 'RUN_SOLVER'
    assert approval['frozenAction']['loadKind'] == 'WIND'

    suggestion = client.get(f'/api/v1/load-imports/{uploaded["importId"]}').json()['suggestion']
    assert suggestion['standardizeDecision'] == 'AUTO'
    assert [channel['valueColumn'] for channel in suggestion['mapping']['channels']] == [
        f'fy_node_{node}(N)' for node in WIND_PER_NODE_NODES
    ]
    assert {channel['quantity'] for channel in suggestion['mapping']['channels']} == {'FORCE'}
    assert {channel['sourceUnit'] for channel in suggestion['mapping']['channels']} == {'N'}

    rows = list(csv.DictReader(
        platform_store.get_artifact(
            approval['frozenAction']['loadDatasetArtifactId']
        ).content.decode('utf-8').splitlines()
    ))
    # 位置绑定的落点：channel_i 必须对应目标集第 i 个节点。
    assert {row['channel_id'] for row in rows} == {
        f'channel_{index}' for index in range(1, len(WIND_PER_NODE_NODES) + 1)
    }
    assert {row['unit'] for row in rows} == {'N'}

    messages = client.get(f'/api/v1/agent/sessions/{session["sessionId"]}').json()['messages']
    message = next(item for item in reversed(messages) if item['role'] == 'ASSISTANT')
    assert '8 列逐节点通道' in message['content']
    assert '统一输出 N' in message['content']


def test_wind_analysis_without_uploaded_load_uses_bundled_momo_record(monkeypatch, tmp_path: Path) -> None:
    _configure_wind_analysis(monkeypatch, tmp_path)
    session = client.post('/api/v1/agent/sessions', json={'title': '无附件风分析'}).json()

    run = client.post(
        f'/api/v1/agent/sessions/{session["sessionId"]}/messages',
        json={'content': '使用 ANSYS 做一次风荷载分析', 'taskType': 'ANALYSIS'},
    ).json()

    frozen = run['pendingApproval']['frozenAction']
    source_path = Path(__file__).resolve().parents[3] / (
        'analysis_data/wind_inputs/wind_vertical_8nodes_10mps_3600s.csv'
    )
    artifact = platform_store.get_artifact(frozen['loadDatasetArtifactId'])
    rows = list(csv.DictReader(artifact.content.decode('utf-8').splitlines()))
    target_nodes = [1, 23, 43, 61, 132, 114, 94, 72]

    assert run['status'] != 'UNSUPPORTED'
    assert frozen['loadKind'] == 'WIND'
    assert frozen['loadMapping']['source'] == 'BUNDLED_PROJECT_DATA'
    assert frozen['loadTargetSetId'] == WIND_DECK_TARGET_SET
    # 逐节点独立力：一个节点一个通道，列名按目标集节点顺序排列。
    assert frozen['loadMapping']['channels'] == [
        {
            'valueColumn': f'fy_node_{node}',
            'applicationType': 'NODAL_FORCE',
            'targetType': 'NODE_GROUP',
            'targetId': WIND_DECK_TARGET_SET,
            'component': 'UY',
            'quantity': 'FORCE',
            'sourceUnit': 'N',
            'scale': 1.0,
        }
        for node in target_nodes
    ]
    assert frozen['loadDatasetArtifactId'] in run['artifactIds']
    assert len(rows) == 3601 * len(target_nodes)
    assert float(rows[0]['time_s']) == 0.0
    assert float(rows[-1]['time_s']) == 3600.0
    assert {row['unit'] for row in rows} == {'N'}
    assert {row['target_id'] for row in rows} == {WIND_DECK_TARGET_SET}
    assert {row['channel_id'] for row in rows} == {
        f'channel_{index}' for index in range(1, len(target_nodes) + 1)
    }
    assert artifact.preview['sourcePath'] == (
        'analysis_data/wind_inputs/wind_vertical_8nodes_10mps_3600s.csv'
    )
    assert artifact.preview['sourceSha256'] == sha256(source_path.read_bytes()).hexdigest()
    assert artifact.preview['aggregation'] == 'PER_NODE_INDEPENDENT_FORCE_COLUMNS'
    assert artifact.preview['nodeColumns'] == [f'fy_node_{node}' for node in target_nodes]
    assert frozen['loadDatasetSha256'] == artifact.artifact.sha256
    assert next(
        item for item in frozen['inputProvenance'] if item['field'] == 'loadDataset'
    )['source'] == 'BUNDLED_PROJECT_DATA'
    assert not any(job.request.get('agentRunId') == run['runId'] for job in platform_store.jobs)


def test_wind_analysis_rejects_load_mapped_to_another_target(monkeypatch, tmp_path: Path) -> None:
    _configure_wind_analysis(monkeypatch, tmp_path)
    uploaded, run = _start_wind_run()

    blocked = _approve_wind_load(uploaded, run, targetType='NODE', targetId='101')

    assert blocked['status'] == 'UNSUPPORTED'
    assert blocked['pendingApproval'] is None
    assert WIND_DECK_TARGET_SET in blocked['resultSummary']['message']
    assert not any(job.request.get('agentRunId') == run['runId'] for job in platform_store.jobs)


def test_wind_analysis_run_can_be_cancelled_before_execution(monkeypatch, tmp_path: Path) -> None:
    _configure_wind_analysis(monkeypatch, tmp_path)
    _fake_wind_solver(monkeypatch)
    uploaded, run = _start_wind_run()
    standardized = _approve_wind_load(uploaded, run)
    waiting = client.post(
        f'/api/v1/agent/approvals/{standardized["pendingApproval"]["approvalId"]}/decision',
        json={'approved': True},
    ).json()['run']

    cancelled = client.post(f'/api/v1/agent/runs/{run["runId"]}/cancel').json()

    assert cancelled['status'] == 'CANCELLED'
    assert platform_store.get_job(waiting['jobId']).status == 'CANCELLED'
    assert platform_store.get_artifact(cancelled['reportArtifactId']).preview['evidenceMode'] == 'CANCELLED'
