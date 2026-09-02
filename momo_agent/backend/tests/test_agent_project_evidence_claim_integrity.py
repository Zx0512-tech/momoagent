from __future__ import annotations

from hashlib import sha256
from types import SimpleNamespace

from app.services.agent_project_evidence import EngineeringProjectEvidenceService
from app.services.agent_repository import DEFAULT_OWNER


class _ProjectService:
    def __init__(self, run: dict) -> None:
        self.run = run

    def get_project(self, project_id: str, owner: str):
        assert owner == DEFAULT_OWNER
        return {
            'projectId': project_id,
            'name': 'Comparison Integrity',
            'workspaceRevision': 1,
            'workspace': {},
            'runs': [{'runId': self.run['runId']}],
        }


class _AgentService:
    def __init__(self, run: dict) -> None:
        self.run = run

    def repository(self):
        return self

    def get_run(self, run_id: str):
        return self.run if run_id == self.run['runId'] else None


class _Store:
    def __init__(self, records: dict[str, object]) -> None:
        self.records = records

    def get_artifact(self, artifact_id: str):
        if artifact_id not in self.records:
            raise KeyError(artifact_id)
        return self.records[artifact_id]


def _record(artifact_id: str, run_id: str, content: bytes):
    return SimpleNamespace(
        artifact=SimpleNamespace(
            artifact_id=artifact_id,
            run_id=run_id,
            sha256=sha256(content).hexdigest(),
        ),
        content=content,
    )


def test_comparison_claim_source_artifact_is_checked_against_target_run() -> None:
    run = {
        'runId': 'agr_compare',
        'sessionId': 'ags_1',
        'taskType': 'INQUIRY',
        'status': 'SUCCEEDED',
        'currentStage': 'COMPLETED',
        'resultSummary': {
            'evidenceMode': 'REAL_FEM',
            'inquiryRunComparison': {
                'compatibility': 'DIRECT',
                'runs': [{
                    'targetKey': 'agr_target:case-a',
                    'runId': 'agr_target',
                    'metrics': {
                        'tower_base_shear': {
                            'value': 100.0,
                            'unit': 'kN',
                            'label': '塔底剪力',
                            'evidence': {'artifactId': 'art_source'},
                        },
                    },
                }],
            },
        },
        'reportArtifactId': 'art_compare_report',
    }
    store = _Store({
        'art_compare_report': _record('art_compare_report', 'agr_compare', b'comparison'),
        'art_source': _record('art_source', 'agr_target', b'source evidence'),
    })
    service = EngineeringProjectEvidenceService(_ProjectService(run), _AgentService(run), store)

    payload = service.build('agp_claim')

    evidence_run = payload['runs'][0]
    assert evidence_run['integrityState'] == 'VALID'
    claim_ref = next(item for item in evidence_run['artifacts'] if item['role'] == 'CLAIM_EVIDENCE')
    assert claim_ref['artifactId'] == 'art_source'
    assert claim_ref['sourceRunId'] == 'agr_target'


def test_comparison_claim_source_artifact_run_mismatch_fails_integrity() -> None:
    run = {
        'runId': 'agr_compare',
        'sessionId': 'ags_1',
        'taskType': 'INQUIRY',
        'status': 'SUCCEEDED',
        'currentStage': 'COMPLETED',
        'resultSummary': {
            'evidenceMode': 'REAL_FEM',
            'inquiryRunComparison': {
                'compatibility': 'DIRECT',
                'runs': [{
                    'targetKey': 'agr_target',
                    'runId': 'agr_target',
                    'metrics': {
                        'tower_base_shear': {
                            'value': 100.0,
                            'unit': 'kN',
                            'label': '塔底剪力',
                            'evidence': {'artifactId': 'art_source'},
                        },
                    },
                }],
            },
        },
        'reportArtifactId': 'art_compare_report',
    }
    store = _Store({
        'art_compare_report': _record('art_compare_report', 'agr_compare', b'comparison'),
        'art_source': _record('art_source', 'agr_wrong', b'source evidence'),
    })
    service = EngineeringProjectEvidenceService(_ProjectService(run), _AgentService(run), store)

    payload = service.build('agp_claim')

    assert payload['runs'][0]['integrityState'] == 'RUN_MISMATCH'
    assert any(
        issue['artifactId'] == 'art_source' and issue['expectedRunId'] == 'agr_target'
        for issue in payload['runs'][0]['integrity']['issues']
    )
