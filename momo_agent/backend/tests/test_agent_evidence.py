from __future__ import annotations

import hashlib
import json
from importlib import metadata
from pathlib import Path

import pytest

from app.services.agent_evidence import (
    build_agent_input_provenance,
    build_damper_calibration_profiles,
    build_output_manifest,
    build_solver_version_profile,
)


def test_solver_version_profile_reads_registered_ansys_contract(monkeypatch, tmp_path: Path) -> None:
    calibration = tmp_path / 'calibration.txt'
    calibration.write_text('verified USER300 calibration\n', encoding='utf-8')
    calibration_sha256 = hashlib.sha256(calibration.read_bytes()).hexdigest()
    baseline = tmp_path / 'baseline.json'
    baseline.write_text(json.dumps({'solver': 'ansys'}), encoding='utf-8')
    optimization = tmp_path / 'optimization.json'
    optimization.write_text(
        json.dumps({
            'solver': 'ansys',
            'solver_kwargs': {
                'mapdl_executable': 'D:/Program Files/ANSYS Inc/v242/ansys/bin/winx64/MAPDL.exe',
                'damper_module': 'damper_user300_viscous',
                'damper_calibration': {
                    'status': 'verified',
                    'artifact_path': calibration.name,
                    'sha256': calibration_sha256,
                },
            },
        }),
        encoding='utf-8',
    )
    workflow = tmp_path / 'workflow.json'
    workflow.write_text(
        json.dumps({
            'baseline_config': baseline.name,
            'optimization_config': optimization.name,
        }),
        encoding='utf-8',
    )
    monkeypatch.setattr('app.services.agent_evidence.metadata.version', lambda _name: '0.71.0')

    profile = build_solver_version_profile(workflow, solver='ANSYS')

    assert profile['schemaVersion'] == '1.0'
    assert profile['solver'] == {
        'name': 'ANSYS_MAPDL',
        'version': '2024 R2',
        'versionSource': 'CONFIGURED_EXECUTABLE_PATH',
    }
    assert profile['sdk'] == {'package': 'ansys-mapdl-core', 'version': '0.71.0'}
    assert profile['responseContract']['id'] == 'ANSYS_BEAM4_SMISC_MMOM_R4'
    assert profile['userElement']['name'] == 'USER300'
    assert profile['userElement']['calibrationSha256'] == calibration_sha256
    assert profile['userElement']['calibrationHashVerified'] is True


def test_solver_version_profile_falls_back_to_installed_openseespy_distribution(
    monkeypatch,
    tmp_path: Path,
) -> None:
    workflow = tmp_path / 'opensees.json'
    workflow.write_text(json.dumps({'solver': {'type': 'openseespy_inproc'}}), encoding='utf-8')

    def fake_version(package: str) -> str:
        if package == 'openseespy':
            raise metadata.PackageNotFoundError(package)
        if package == 'openseespywin':
            return '3.8.0.0'
        raise AssertionError(package)

    monkeypatch.setattr('app.services.agent_evidence.metadata.version', fake_version)
    monkeypatch.setattr('app.services.agent_evidence._openseespy_runtime_version', lambda: None)

    profile = build_solver_version_profile(workflow, solver='OPENSEESPY_INPROC')

    assert profile['solver']['version'] == '3.8.0.0'
    assert profile['sdk'] == {'package': 'openseespywin', 'version': '3.8.0.0'}


def test_solver_version_profile_uses_bundled_opensees_runtime_version(
    monkeypatch,
    tmp_path: Path,
) -> None:
    workflow = tmp_path / 'opensees.json'
    workflow.write_text(json.dumps({'solver': {'type': 'openseespy_inproc'}}), encoding='utf-8')
    monkeypatch.setattr(
        'app.services.agent_evidence.metadata.version',
        lambda package: (_ for _ in ()).throw(metadata.PackageNotFoundError(package)),
    )
    monkeypatch.setattr('app.services.agent_evidence._openseespy_runtime_version', lambda: '3.8.0')

    profile = build_solver_version_profile(workflow, solver='OPENSEESPY_INPROC')

    assert profile['solver'] == {
        'name': 'OPENSEESPY_INPROC',
        'version': '3.8.0',
        'versionSource': 'SOLVER_RUNTIME',
    }
    assert profile['sdk'] == {'package': 'bundled-openseespy', 'version': '3.8.0'}


def test_input_provenance_distinguishes_user_template_file_and_default_sources() -> None:
    provenance = build_agent_input_provenance({
        'solver': 'ANSYS',
        'workflowConfigPath': 'docs/examples/templates/workflow.json',
        'executionTimeoutS': 7200,
        'loadDatasetArtifactId': 'art_load',
        'loadDatasetSha256': 'a' * 64,
        'damper': {'type': 'VISCOUS'},
        'selectedLayoutId': 'TWO_PER_TOWER',
        'responseIds': ['max_tower_base_shear'],
        'budget': {'doeDesignCount': 15},
    }, task_type='DAMPER_OPTIMIZATION')

    by_field = {item['field']: item for item in provenance}
    assert by_field['solver']['source'] == 'USER_DECISION'
    assert by_field['damper.type']['source'] == 'USER_DECISION'
    assert by_field['responseIds']['source'] == 'USER_DECISION'
    assert by_field['loadDataset']['source'] == 'FILE_DERIVED'
    assert by_field['workflowConfigPath']['source'] == 'VERIFIED_TEMPLATE'
    assert by_field['selectedLayoutId']['source'] == 'VERIFIED_TEMPLATE'
    assert by_field['budget']['source'] == 'VERIFIED_TEMPLATE'
    assert by_field['executionTimeoutS']['source'] == 'OPERATIONAL_DEFAULT'
    assert len(by_field['loadDataset']['value']['sha256']) == 64


def test_output_manifest_is_hash_complete_and_scoped_to_job_directory(tmp_path: Path) -> None:
    allowed_root = tmp_path / 'real_workflows'
    run_dir = allowed_root / 'job_1'
    nested = run_dir / 'results'
    nested.mkdir(parents=True)
    (run_dir / 'summary.json').write_text('{"status":"completed"}\n', encoding='utf-8')
    (nested / 'response.csv').write_text('time,value\n0,1\n', encoding='utf-8')
    (run_dir / 'real_output_manifest.json').write_text('{}', encoding='utf-8')

    manifest = build_output_manifest(run_dir, allowed_root=allowed_root)

    assert manifest['schemaVersion'] == '1.0'
    assert manifest['rootDirectory'] == 'job_1'
    assert manifest['fileCount'] == 2
    assert [item['path'] for item in manifest['files']] == [
        'results/response.csv',
        'summary.json',
    ]
    assert all(item['sizeBytes'] > 0 for item in manifest['files'])
    assert all(len(item['sha256']) == 64 for item in manifest['files'])

    with pytest.raises(ValueError, match='outside allowed root'):
        build_output_manifest(tmp_path, allowed_root=allowed_root)


def test_all_user300_damper_calibration_profiles_are_hash_verified() -> None:
    profiles = build_damper_calibration_profiles(['VISCOUS', 'EDDY_CURRENT', 'FRICTION'])

    assert [item['damperType'] for item in profiles] == ['VISCOUS', 'EDDY_CURRENT', 'FRICTION']
    assert [item['user300Type'] for item in profiles] == [3, 1, 2]
    assert all(item['status'] == 'VERIFIED' for item in profiles)
    assert all(len(item['sha256']) == 64 for item in profiles)
    assert all(item['maxTargetRelativeError'] <= 0.001 for item in profiles)
