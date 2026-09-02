from __future__ import annotations

from hashlib import sha256
from types import SimpleNamespace

import pytest

from app.services.agent_project_evidence import EngineeringProjectEvidenceService
from app.services.agent_repository import DEFAULT_OWNER


class _ProjectService:
    def __init__(self, run: dict) -> None:
        self.run = run

    def get_project(self, project_id: str, owner: str):
        assert project_id == 'agp_integrity'
        assert owner == DEFAULT_OWNER
        return {
            'projectId': project_id,
            'name': 'Integrity Project',
            'workspaceRevision': 3,
            'workspace': {'solver': 'OPENSEESPY_INPROC'},
            'runs': [{'runId': self.run['runId']}],
        }


class _Repository:
    def __init__(self, run: dict) -> None:
        self.run = run

    def get_run(self, run_id: str):
        return self.run if run_id == self.run['runId'] else None


class _AgentService:
    def __init__(self, run: dict) -> None:
        self.repo = _Repository(run)

    def repository(self):
        return self.repo


class _ArtifactStore:
    def __init__(self, records: dict[str, object]) -> None:
        self.records = records

    def get_artifact(self, artifact_id: str):
        if artifact_id not in self.records:
            raise KeyError(artifact_id)
        return self.records[artifact_id]


def _record(artifact_id: str, content: bytes, *, run_id: str = 'agr_integrity', digest: str | None = None):
    return SimpleNamespace(
        artifact=SimpleNamespace(
            artifact_id=artifact_id,
            run_id=run_id,
            sha256=digest or sha256(content).hexdigest(),
        ),
        content=content,
    )


def _build(store: _ArtifactStore) -> dict:
    run = {
        'runId': 'agr_integrity',
        'sessionId': 'ags_1',
        'taskType': 'ANALYSIS',
        'status': 'SUCCEEDED',
        'currentStage': 'COMPLETED',
        'resultSummary': {'evidenceMode': 'REAL_FEM'},
        'reportArtifactId': 'art_report',
        'artifactIds': ['art_result'],
    }
    service = EngineeringProjectEvidenceService(
        _ProjectService(run),
        _AgentService(run),
        store,
    )
    return service.build('agp_integrity')


def test_evidence_integrity_validates_registered_bytes_and_run_binding() -> None:
    payload = _build(_ArtifactStore({
        'art_report': _record('art_report', b'{"ok":true}'),
        'art_result': _record('art_result', b'time,value\n0,1\n'),
    }))

    run = payload['runs'][0]
    assert run['trustState'] == 'REAL_FEM'
    assert run['integrityState'] == 'VALID'
    assert run['integrity']['checkedArtifactCount'] == 2
    assert run['integrity']['issues'] == []
    assert payload['integrityCounts']['VALID'] == 1
    assert payload['projectReport']['integrityValidRunCount'] == 1
    assert payload['projectReport']['evidenceIndex'][0]['integrityState'] == 'VALID'


@pytest.mark.parametrize(
    ('records', 'expected_state', 'expected_code'),
    [
        (
            {'art_report': _record('art_report', b'changed', digest='0' * 64),
             'art_result': _record('art_result', b'ok')},
            'HASH_MISMATCH',
            'HASH_MISMATCH',
        ),
        (
            {'art_report': _record('art_report', b'ok'),
             'art_result': _record('art_result', b'ok', run_id='agr_other')},
            'RUN_MISMATCH',
            'RUN_MISMATCH',
        ),
        (
            {'art_report': _record('art_report', b'ok')},
            'MISSING_ARTIFACT',
            'MISSING_ARTIFACT',
        ),
    ],
)
def test_evidence_integrity_fails_closed_for_current_artifact_drift(
    records: dict[str, object],
    expected_state: str,
    expected_code: str,
) -> None:
    payload = _build(_ArtifactStore(records))

    run = payload['runs'][0]
    assert run['trustState'] == 'REAL_FEM'
    assert run['integrityState'] == expected_state
    assert expected_code in {item['code'] for item in run['integrity']['issues']}
    assert payload['projectReport']['integrityValidRunCount'] == 0
