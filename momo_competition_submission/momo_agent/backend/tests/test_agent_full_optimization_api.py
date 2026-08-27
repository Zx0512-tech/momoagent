from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.agent_llm import AgentIntent, PlannerResult
from app.services.agent_service import agent_service
from app.services.platform_dispatcher import platform_dispatcher
from app.services.platform_store import PlatformStore, platform_store


WORKFLOW_PATH = 'docs/examples/templates/ansys_run_joint_baseline_workflow_template.json'
REQUIRED_ARTIFACT_NAMES = (
    'real_workflow_summary.json',
    'real_optimization_summary.json',
    'real_baseline_summary.json',
    'real_earthquake_workflow_overview.json',
    'real_output_manifest.json',
)


@pytest.fixture(autouse=True)
def isolated_platform_state(monkeypatch, tmp_path: Path):
    isolated = PlatformStore(state_path=tmp_path / 'agent_full_state.sqlite3')
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
    monkeypatch.setattr(
        'app.services.agent_service.build_readiness_report',
        lambda *_args, **_kwargs: {
            'status': 'READY',
            'blockingComponents': [],
            'components': {
                name: {'status': 'PASS', 'required': True, 'detail': 'test'}
                for name in ('database', 'artifact_storage', 'disk_space', 'dispatcher', 'ansys_executable')
            },
        },
    )
    monkeypatch.setattr(
        'app.services.agent_service.preflight_config',
        lambda _path: {
            'kind': 'baseline_optimization_workflow',
            'baseline': {
                'solver': 'ansys',
                'execution_mode': 'run',
                'path_checks': {'mapdl': {'path': 'MAPDL.exe', 'exists': True}},
            },
            'optimization': {
                'solver': 'ansys',
                'execution_mode': 'run',
                'path_checks': {'model': {'path': 'STbridge.txt', 'exists': True}},
                'doe_design_count': 15,
                'surrogate_cv': 10,
            },
        },
    )
    monkeypatch.setattr(
        agent_service.planner,
        'plan',
        lambda _goal: PlannerResult(
            plannerMode='LLM',
            intent=AgentIntent(
                taskType='FULL_OPTIMIZATION',
                solver='ANSYS',
                scenario='EARTHQUAKE',
                useVerifiedTemplateLoads=True,
                requiresRealFem=True,
                summary='执行完整优化',
            ),
        ),
    )
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


client = TestClient(app)


def _create_full_run() -> dict:
    session = client.post('/api/v1/agent/sessions', json={'title': '完整优化'}).json()
    response = client.post(
        f'/api/v1/agent/sessions/{session["sessionId"]}/messages',
        json={'content': '执行完整阻尼优化', 'taskType': 'FULL_OPTIMIZATION'},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _approve(run: dict) -> dict:
    response = client.post(
        f'/api/v1/agent/approvals/{run["pendingApproval"]["approvalId"]}/decision',
        json={'approved': True},
    )
    assert response.status_code == 200, response.text
    return response.json()['run']


def _finish_job(run: dict, terminal: str, *, accepted: bool = True) -> None:
    job = platform_store.get_job(run['jobId'])
    job.status = terminal
    if terminal == 'SUCCEEDED':
        artifacts = []
        for name in REQUIRED_ARTIFACT_NAMES:
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
            ))
        output_manifest = next(item for item in artifacts if item.name == 'real_output_manifest.json')
        job.artifacts = artifacts
        job.result = {
            'mode': 'real_baseline_optimization',
            'baselineStatus': 'completed',
            'validationStatus': {'all_verified_execution': True, 'all_accepted': accepted},
            'reviewStatus': {'all_verified_execution': True, 'all_accepted': accepted},
            'finalRecommendationStatus': 'ACCEPTED' if accepted else 'DIAGNOSTIC_NOT_ACCEPTED',
            'solverVersionProfile': job.request['solverVersionProfile'],
            'inputProvenance': job.request['inputProvenance'],
            'outputManifestArtifactId': output_manifest.artifact_id,
            'outputManifestSha256': output_manifest.sha256,
        }
    platform_store.persist()


def test_full_optimization_creates_frozen_plan_without_upload_or_early_job() -> None:
    run = _create_full_run()

    assert run['taskType'] == 'FULL_OPTIMIZATION'
    assert run['plannerMode'] == 'LLM'
    assert run['status'] == 'WAITING_APPROVAL'
    assert run['preflight']['passed'] is True
    assert len(run['plan']) == 10
    assert run['workflowContract']['candidateCount'] == 728
    assert run['workflowContract']['customLoadFilesUsed'] is False
    assert run['pendingApproval']['action'] == 'RUN_FULL_OPTIMIZATION'
    assert run['pendingApproval']['narrativeMode'] in {'LLM', 'TEMPLATE_FALLBACK'}
    assert 'narrativeFallbackReason' in run['pendingApproval']
    frozen = run['pendingApproval']['frozenAction']
    assert {key: frozen[key] for key in (
        'jobType', 'solver', 'scenario', 'executionTarget', 'runMode',
        'workflowConfigPath', 'executionTimeoutS',
    )} == {
        'jobType': 'MULTI_OBJECTIVE_OPTIMIZATION',
        'solver': 'ANSYS',
        'scenario': 'EARTHQUAKE',
        'executionTarget': 'OPTIMIZATION_DECISION',
        'runMode': 'REAL_BASELINE_OPTIMIZATION',
        'workflowConfigPath': WORKFLOW_PATH,
        'executionTimeoutS': 7200,
    }
    assert frozen['solverVersionProfile']['solver']['version'] == '2024 R2'
    assert frozen['solverVersionProfile']['userElement']['calibrationHashVerified'] is True
    assert run['solverVersionProfile'] == frozen['solverVersionProfile']
    assert run['inputProvenance'] == frozen['inputProvenance']
    assert {item['source'] for item in frozen['inputProvenance']} == {
        'VERIFIED_TEMPLATE',
        'BUNDLED_PROJECT_DATA',
        'OPERATIONAL_DEFAULT',
    }
    assert frozen['loadDatasetArtifactId'] in run['artifactIds']
    assert frozen['loadMapping']['source'] == 'BUNDLED_PROJECT_DATA'
    assert not any(job.request.get('agentRunId') == run['runId'] for job in platform_store.jobs)


def test_full_optimization_approval_is_idempotent_after_refresh() -> None:
    run = _create_full_run()
    first = _approve(run)
    second = client.post(
        f'/api/v1/agent/approvals/{run["pendingApproval"]["approvalId"]}/decision',
        json={'approved': True},
    ).json()['run']

    assert first['jobId'] == second['jobId']
    platform_store.refresh()
    jobs = [job for job in platform_store.jobs if job.request.get('agentRunId') == run['runId']]
    assert len(jobs) == 1
    assert jobs[0].request['loadDatasetArtifactId'] == run['pendingApproval']['frozenAction']['loadDatasetArtifactId']
    assert jobs[0].request['loadMapping']['source'] == 'BUNDLED_PROJECT_DATA'


def test_unsupported_llm_intent_does_not_create_approval_or_job(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_service.planner,
        'plan',
        lambda _goal: PlannerResult(
            plannerMode='LLM',
            intent=AgentIntent(
                taskType='FULL_OPTIMIZATION',
                solver='OPENSEESPY_INPROC',
                scenario='WIND',
                useVerifiedTemplateLoads=True,
                requiresRealFem=True,
                summary='运行 OpenSees 风优化',
            ),
        ),
    )

    run = _create_full_run()

    assert run['status'] == 'UNSUPPORTED'
    assert run['pendingApproval'] is None
    assert not any(job.request.get('agentRunId') == run['runId'] for job in platform_store.jobs)


def test_readiness_failure_blocks_approval_and_job(monkeypatch) -> None:
    monkeypatch.setattr(
        'app.services.agent_service.build_readiness_report',
        lambda *_args, **_kwargs: {
            'status': 'NOT_READY',
            'blockingComponents': ['ansys_executable'],
            'components': {'ansys_executable': {'status': 'FAIL', 'required': True, 'detail': 'missing'}},
        },
    )

    run = _create_full_run()

    assert run['status'] == 'FAILED'
    assert run['preflight']['passed'] is False
    assert run['pendingApproval'] is None
    assert not any(job.request.get('agentRunId') == run['runId'] for job in platform_store.jobs)


def test_real_config_path_failure_blocks_approval_and_job(monkeypatch) -> None:
    monkeypatch.setattr(
        'app.services.agent_service.preflight_config',
        lambda _path: {
            'kind': 'baseline_optimization_workflow',
            'baseline': {
                'solver': 'ansys',
                'execution_mode': 'run',
                'path_checks': {'mapdl': {'path': 'MAPDL.exe', 'exists': False}},
            },
            'optimization': {'solver': 'ansys', 'execution_mode': 'run', 'path_checks': {}},
        },
    )

    run = _create_full_run()

    assert run['status'] == 'FAILED'
    assert run['preflight']['passed'] is False
    assert run['pendingApproval'] is None


def test_agent_api_uses_existing_real_workflow_runner(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        'app.services.platform_store.EARTHQUAKE_WORKFLOW_OUTPUT_ROOT',
        tmp_path / 'real_workflows',
    )

    def fake_run(workflow_config_path: Path, execution_timeout_s: float | None) -> dict:
        assert execution_timeout_s == 7200
        output_dir = workflow_config_path.parent
        workflow_config = json.loads(workflow_config_path.read_text(encoding='utf-8'))
        optimization_config = json.loads(
            (output_dir / workflow_config['optimization_config']).read_text(encoding='utf-8')
        )
        assert optimization_config['n_candidates'] == 728
        baseline_path = output_dir / 'undamped_earthquake_summary.json'
        optimization_path = output_dir / 'optimization_summary.json'
        workflow_path = output_dir / 'workflow_summary.json'
        baseline_path.write_text(json.dumps({'status': 'completed'}), encoding='utf-8')
        optimization_path.write_text(
            json.dumps({
                'validation_status': {'all_verified_execution': True, 'all_accepted': True},
                'review_status': {'all_verified_execution': True, 'all_accepted': True},
                'doe_designs': [
                    {
                        'design': [5500.0 + index * 100.0, 0.65],
                        'design_parameters': {'c': 5500.0 + index * 100.0, 'alpha': 0.65},
                        'analysis_results': [
                            {
                                'case_id': f'case_{index + 1:03d}',
                                'status': 'completed',
                                'objectives': {'max_girder_end_displacement': 0.1},
                            },
                        ],
                    }
                    for index in range(15)
                ],
            }),
            encoding='utf-8',
        )
        workflow = {
            'workflow_summary_path': str(workflow_path),
            'baseline_summary_path': str(baseline_path),
            'optimization_summary_path': str(optimization_path),
            'baseline_status': 'completed',
        }
        workflow_path.write_text(json.dumps(workflow), encoding='utf-8')
        (output_dir / 'timeseries.csv').write_text(
            'time,max_girder_end_displacement\n0,0.1\n1,0.2\n',
            encoding='utf-8',
        )
        return workflow

    monkeypatch.setattr(platform_store, '_run_real_baseline_optimization_workflow', fake_run)
    run = _approve(_create_full_run())

    platform_store.execute_queued_job(run['jobId'])
    completed = client.get(f'/api/v1/agent/runs/{run["runId"]}').json()

    assert completed['status'] == 'SUCCEEDED'
    assert 'sampleResponses' in completed['resultSummary']
    report = platform_store.get_artifact(completed['reportArtifactId']).preview
    assert report['evidenceMode'] == 'REAL_FEM'
    assert report['solverVersionProfile']['solver']['version'] == '2024 R2'
    assert report['inputProvenance']
    manifest_artifact = next(
        item for item in platform_store.get_job(run['jobId']).artifacts
        if item.name == 'real_output_manifest.json'
    )
    assert completed['outputManifestArtifactId'] == manifest_artifact.artifact_id
    manifest = platform_store.get_artifact(manifest_artifact.artifact_id).preview
    assert manifest['fileCount'] == len(manifest['files'])
    assert all(len(item['sha256']) == 64 for item in manifest['files'])
    execution_evidence = platform_store.get_artifact(
        next(item for item in platform_store.get_job(run['jobId']).artifacts if item.name == 'real_workflow_summary.json').artifact_id
    ).preview['executionEvidence']
    assert execution_evidence['requestedInitialDoeCount'] == 15
    assert execution_evidence['actualInitialDoeCount'] == 15
    assert execution_evidence['realSolveCount'] == 17
    assert len(execution_evidence['designSetSha256']) == 64


def test_reflection_rejects_dry_run_evidence() -> None:
    run = _approve(_create_full_run())
    _finish_job(run, 'SUCCEEDED', accepted=True)
    job = platform_store.get_job(run['jobId'])
    platform_store.get_artifact(job.artifacts[0].artifact_id).preview = {
        'metadata': {'command_stream': {'dry_run': True}},
    }
    platform_store.persist()

    completed = client.get(f'/api/v1/agent/runs/{run["runId"]}').json()

    assert completed['status'] == 'COMPLETED_DIAGNOSTIC'
    assert completed['resultSummary']['checks']['realArtifactEvidence'] is False


def test_user_cancellation_cancels_linked_job_and_records_report() -> None:
    run = _approve(_create_full_run())

    response = client.post(f'/api/v1/agent/runs/{run["runId"]}/cancel')

    assert response.status_code == 200
    cancelled = response.json()
    assert cancelled['status'] == 'CANCELLED'
    assert platform_store.get_job(run['jobId']).status == 'CANCELLED'
    assert platform_store.get_artifact(cancelled['reportArtifactId']).preview['evidenceMode'] == 'CANCELLED'


@pytest.mark.parametrize(
    ('job_status', 'accepted', 'expected_status', 'evidence_mode'),
    [
        ('SUCCEEDED', True, 'SUCCEEDED', 'REAL_FEM'),
        ('SUCCEEDED', False, 'COMPLETED_DIAGNOSTIC', 'DIAGNOSTIC_ONLY'),
        ('FAILED', False, 'FAILED', 'FAILED'),
        ('CANCELLED', False, 'CANCELLED', 'CANCELLED'),
    ],
)
def test_reflection_maps_job_terminal_states_and_generates_evidence_report(
    job_status: str,
    accepted: bool,
    expected_status: str,
    evidence_mode: str,
) -> None:
    run = _approve(_create_full_run())
    _finish_job(run, job_status, accepted=accepted)

    completed = client.get(f'/api/v1/agent/runs/{run["runId"]}').json()

    assert completed['status'] == expected_status
    report = platform_store.get_artifact(completed['reportArtifactId']).preview
    assert report['evidenceMode'] == evidence_mode
    assert report['jobId'] == run['jobId']
    if job_status == 'SUCCEEDED':
        assert {item['name'] for item in report['artifacts']} == set(REQUIRED_ARTIFACT_NAMES)
        assert all(len(item['sha256']) == 64 for item in report['artifacts'])
