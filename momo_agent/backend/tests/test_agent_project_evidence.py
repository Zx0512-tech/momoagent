from __future__ import annotations

from app.services.agent_project_evidence import EngineeringProjectEvidenceService
from app.services.agent_repository import DEFAULT_OWNER


class _ProjectService:
    def __init__(self, runs: list[dict]) -> None:
        self.runs = runs

    def get_project(self, project_id: str, owner: str):
        assert project_id == 'agp_1'
        assert owner == DEFAULT_OWNER
        return {
            'projectId': 'agp_1',
            'name': 'Bridge Project',
            'workspaceRevision': 4,
            'workspace': {
                'solver': 'OPENSEESPY_INPROC',
                'loadKind': 'EARTHQUAKE',
                'responseIds': ['max_tower_base_shear'],
            },
            'runs': [{'runId': run['runId']} for run in self.runs],
        }


class _Repository:
    def __init__(self, runs: list[dict]) -> None:
        self.runs = {run['runId']: run for run in runs}

    def get_run(self, run_id: str):
        return self.runs.get(run_id)


class _AgentService:
    def __init__(self, runs: list[dict]) -> None:
        self._repository = _Repository(runs)

    def repository(self):
        return self._repository


def _service(runs: list[dict]) -> EngineeringProjectEvidenceService:
    return EngineeringProjectEvidenceService(_ProjectService(runs), _AgentService(runs))


def test_project_evidence_classifies_trust_and_deduplicates_artifacts(monkeypatch) -> None:
    monkeypatch.setattr('app.services.agent_project_evidence.utc_now', lambda: '2026-09-02T00:00:00Z')
    runs = [
        {
            'runId': 'agr_real',
            'sessionId': 'ags_1',
            'taskType': 'ANALYSIS',
            'status': 'SUCCEEDED',
            'currentStage': 'COMPLETED',
            'resultSummary': {'evidenceMode': 'REAL_FEM', 'objectives': {'shear': 123.4}},
            'reportArtifactId': 'art_report',
            'outputManifestArtifactId': 'art_manifest',
            'figureArtifactIds': ['art_figure'],
            'resultArtifacts': [
                {'artifactId': 'art_result', 'name': 'responses.csv', 'kind': 'TIMESERIES'},
            ],
            'artifactIds': ['art_report', 'art_result', 'art_extra'],
        },
        {
            'runId': 'agr_limited',
            'sessionId': 'ags_1',
            'taskType': 'ANALYSIS',
            'status': 'SUCCEEDED',
            'currentStage': 'COMPLETED',
            'resultSummary': {'evidenceMode': 'UNKNOWN'},
            'artifactIds': ['art_only'],
        },
        {
            'runId': 'agr_failed',
            'sessionId': 'ags_1',
            'taskType': 'ANALYSIS',
            'status': 'FAILED',
            'currentStage': 'FAILED',
            'resultSummary': {'evidenceMode': 'REAL_FEM'},
            'reportArtifactId': 'art_failed_report',
        },
    ]

    payload = _service(runs).build('agp_1')

    assert payload['generatedAt'] == '2026-09-02T00:00:00Z'
    assert payload['trustCounts'] == {
        'REAL_FEM': 1,
        'VERIFIED': 0,
        'LIMITED': 1,
        'NOT_VERIFIED': 1,
    }
    real = payload['runs'][0]
    assert real['trustState'] == 'REAL_FEM'
    assert [(item['artifactId'], item['role']) for item in real['artifacts']] == [
        ('art_report', 'REPORT'),
        ('art_manifest', 'OUTPUT_MANIFEST'),
        ('art_figure', 'FIGURE'),
        ('art_result', 'RESULT'),
        ('art_extra', 'REGISTERED'),
    ]
    assert payload['projectReport']['trustedRunCount'] == 1
    assert payload['projectReport']['workspaceRevision'] == 4


def test_project_evidence_does_not_promote_unmapped_result_numbers_to_claims() -> None:
    runs = [
        {
            'runId': 'agr_1',
            'sessionId': 'ags_1',
            'taskType': 'ANALYSIS',
            'status': 'SUCCEEDED',
            'currentStage': 'COMPLETED',
            'resultSummary': {
                'evidenceMode': 'REAL_FEM',
                'message': '塔底剪力为 18.5 MN。',
                'objectives': {'tower_base_shear': 18.5},
            },
            'reportArtifactId': 'art_report',
        },
    ]

    payload = _service(runs).build('agp_1')

    assert payload['claims'] == []
    assert payload['runs'][0]['claims'] == []
    assert payload['runs'][0]['narrativeSummary'] == '塔底剪力为 18.5 MN。'
    assert payload['projectReport']['claimCount'] == 0


def test_project_evidence_exposes_only_comparison_metrics_with_explicit_evidence() -> None:
    runs = [
        {
            'runId': 'agr_compare',
            'sessionId': 'ags_1',
            'taskType': 'INQUIRY',
            'status': 'SUCCEEDED',
            'currentStage': 'COMPLETED',
            'resultSummary': {
                'evidenceMode': 'REAL_FEM',
                'inquiryRunComparison': {
                    'compatibility': 'CROSS_SOLVER',
                    'runs': [
                        {
                            'targetKey': 'agr_base:case-1',
                            'runId': 'agr_base',
                            'metrics': {
                                'tower_base_shear': {
                                    'value': 100.0,
                                    'unit': 'kN',
                                    'label': '塔底剪力',
                                    'evidence': {
                                        'artifactId': 'art_base',
                                        'sourceColumn': 'base_shear',
                                    },
                                },
                                'missing_evidence': {
                                    'value': 12.0,
                                    'unit': 'mm',
                                    'label': '无证据值',
                                },
                            },
                        },
                    ],
                },
            },
            'reportArtifactId': 'art_compare_report',
        },
    ]

    payload = _service(runs).build('agp_1')

    assert len(payload['claims']) == 1
    claim = payload['claims'][0]
    assert claim['claimType'] == 'COMPARISON_METRIC'
    assert claim['label'] == '塔底剪力'
    assert claim['value'] == 100.0
    assert claim['unit'] == 'kN'
    assert claim['compatibility'] == 'CROSS_SOLVER'
    assert claim['source']['evidence']['artifactId'] == 'art_base'
    assert payload['projectReport']['evidenceIndex'][0]['claimIds'] == [claim['claimId']]
    assert any('CROSS_SOLVER' in item for item in payload['projectReport']['limitations'])


def test_verified_mode_requires_success_and_registered_report() -> None:
    runs = [
        {
            'runId': 'agr_verified',
            'sessionId': 'ags_1',
            'taskType': 'INQUIRY',
            'status': 'SUCCEEDED',
            'currentStage': 'COMPLETED',
            'resultSummary': {'evidenceMode': 'DETERMINISTIC'},
            'reportArtifactId': 'art_report',
        },
        {
            'runId': 'agr_no_report',
            'sessionId': 'ags_1',
            'taskType': 'INQUIRY',
            'status': 'SUCCEEDED',
            'currentStage': 'COMPLETED',
            'resultSummary': {'evidenceMode': 'DETERMINISTIC'},
        },
    ]

    payload = _service(runs).build('agp_1')

    assert payload['runs'][0]['trustState'] == 'VERIFIED'
    assert payload['runs'][1]['trustState'] == 'NOT_VERIFIED'
