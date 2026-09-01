from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from app.api.v1.schemas import Job
from app.services.platform_store import PlatformStore


def test_real_case_verification_accepts_nested_run_mode_and_rejects_dry_run() -> None:
    """真实 OpenSeesPy 摘要的执行模式位于 solver_design 下。"""
    completed = {
        'status': 'completed',
        'metadata': {
            'is_verified_solver_output': True,
            'solver_design': {'execution_mode': 'run'},
            'command_stream': {'dry_run': False},
        },
    }

    assert PlatformStore._is_verified_real_case_output(completed) is True
    completed['metadata']['command_stream']['dry_run'] = True
    assert PlatformStore._is_verified_real_case_output(completed) is False


def _sha256(content: bytes) -> str:
    return sha256(content).hexdigest()


def _register_json(store: PlatformStore, *, name: str, path: str, payload: dict):
    content = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    return store._register_artifact(
        kind='JSON_SUMMARY',
        name=name,
        path=path,
        mime_type='application/json',
        preview=payload,
        content=content,
        source='REAL_SOLVER_RESULT',
    )


def test_reverify_sweep_uses_registered_outputs_without_calling_solver(tmp_path: Path) -> None:
    store = PlatformStore(state_path=tmp_path / 'platform.sqlite3', recover_orphans=False)
    root = 'output/platform_store/real_workflows/sweep_1'
    case_id = 'case_viscous_c1000_alpha03'
    solver_case_id = 'solver_case_1'
    command_content = b'ops.analyze(4000, 0.01)\n'
    timeseries_content = b'time,displacement\n0,0\n1,0.1\n'
    source_summary_content = b'{"status":"completed"}'
    command_path = f'{root}/{case_id}/solver_outputs/{solver_case_id}/openseespy_inproc_command.py'
    timeseries_path = f'{root}/{case_id}/solver_outputs/{solver_case_id}/timeseries.csv'
    source_summary_path = f'{root}/{case_id}/solver_outputs/{solver_case_id}/summary.json'
    case_summary_path = f'{root}/{case_id}/real_case_summary.json'
    sweep_summary_path = f'{root}/real_damper_parameter_sweep_summary.json'
    case_summary = {
        'caseId': case_id,
        'damperType': 'VISCOUS',
        'parameters': {'c': 1000.0, 'alpha': 0.3, 'vfloor': 0.001},
        'status': 'completed',
        'isVerifiedSolverOutput': False,
        'objectives': {'max_girder_end_displacement': 0.1},
        'solverCaseId': solver_case_id,
    }
    old_case = _register_json(
        store,
        name=f'real_{case_id}_case_summary.json',
        path=case_summary_path,
        payload=case_summary,
    )
    old_summary = _register_json(
        store,
        name='real_damper_parameter_sweep_summary.json',
        path=sweep_summary_path,
        payload={'mode': 'real_damper_parameter_sweep', 'caseResults': [case_summary], 'allVerifiedExecution': False},
    )
    command = store._register_artifact(
        kind='COMMAND_STREAM',
        name=f'{case_id}_executed_command_stream.txt',
        path=command_path,
        mime_type='text/plain; charset=utf-8',
        preview={'phase': 'EXECUTED'},
        content=command_content,
        source='REAL_SOLVER_RESULT',
    )
    timeseries = store._register_artifact(
        kind='CSV_TIMESERIES',
        name='timeseries.csv',
        path=timeseries_path,
        mime_type='text/csv',
        preview={'headers': ['time', 'displacement']},
        content=timeseries_content,
        source='REAL_SOLVER_RESULT',
    )
    manifest = _register_json(
        store,
        name='real_output_manifest.json',
        path=f'{root}/real_output_manifest.json',
        payload={
            'schemaVersion': '1.0',
            'rootDirectory': 'sweep_1',
            'fileCount': 3,
            'files': [
                {'path': f'{case_id}/solver_outputs/{solver_case_id}/openseespy_inproc_command.py', 'sizeBytes': len(command_content), 'sha256': _sha256(command_content)},
                {'path': f'{case_id}/solver_outputs/{solver_case_id}/timeseries.csv', 'sizeBytes': len(timeseries_content), 'sha256': _sha256(timeseries_content)},
                {'path': f'{case_id}/solver_outputs/{solver_case_id}/summary.json', 'sizeBytes': len(source_summary_content), 'sha256': _sha256(source_summary_content)},
            ],
        },
    )
    job = Job(
        jobId='job_sweep_1',
        type='SOLVER_BATCH',
        status='SUCCEEDED',
        title='参数批量',
        createdAt='2026-08-21T00:00:00Z',
        request={
            'runMode': 'REAL_DAMPER_PARAMETER_SWEEP',
            'solver': 'OPENSEESPY_INPROC',
            'scenario': 'EARTHQUAKE',
            'cases': [{'caseId': case_id}],
        },
        result={
            'mode': 'real_damper_parameter_sweep',
            'caseResults': [case_summary],
            'outputManifestArtifactId': manifest.artifact_id,
            'outputManifestSha256': manifest.sha256,
        },
        artifacts=[old_summary, old_case, command, timeseries, manifest],
    )
    store.jobs.append(job)
    store.persist()

    repaired = store.reverify_real_damper_parameter_sweep(job.job_id)

    assert repaired.result['allVerifiedExecution'] is True
    assert repaired.result['caseResults'][0]['isVerifiedSolverOutput'] is True
    assert repaired.result['evidenceReverification']['mode'] == 'REGISTERED_OUTPUTS'
    assert command.artifact_id in repaired.result['evidenceReverification']['sourceArtifactIds']
    assert timeseries.artifact_id in repaired.result['evidenceReverification']['sourceArtifactIds']
    assert old_case.artifact_id not in {artifact.artifact_id for artifact in repaired.artifacts}
    assert store.get_artifact(command.artifact_id).content == command_content
    assert store.get_artifact(timeseries.artifact_id).content == timeseries_content
