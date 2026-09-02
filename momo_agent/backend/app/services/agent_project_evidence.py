from __future__ import annotations

from hashlib import sha256
from typing import Any

from app.services.agent_project_service import engineering_project_service
from app.services.agent_repository import DEFAULT_OWNER
from app.services.agent_service import agent_service
from app.services.platform_store import platform_store, utc_now


_SUCCESS_STATUSES = {'SUCCEEDED'}
_VERIFIED_EVIDENCE_MODES = {'VERIFIED', 'DETERMINISTIC'}
_INTEGRITY_STATES = ('VALID', 'MISSING_ARTIFACT', 'HASH_MISMATCH', 'RUN_MISMATCH', 'NOT_CHECKED')


class EngineeringProjectEvidenceService:
    """Build a deterministic, read-only evidence projection for one Engineering Project.

    ``trustState`` describes the persisted engineering/evidence contract of a Run. ``integrityState``
    independently checks whether the artifacts referenced by that Run are still registered, bound to
    the expected Run when a runId is present, and byte-identical to their registered SHA256.
    """

    def __init__(
        self,
        project_service=engineering_project_service,
        agent_service_instance=agent_service,
        artifact_store=platform_store,
    ) -> None:
        self.project_service = project_service
        self.agent_service = agent_service_instance
        self.artifact_store = artifact_store

    @staticmethod
    def _trust_state(run: dict[str, Any]) -> str:
        status = str(run.get('status') or '')
        summary = run.get('resultSummary') if isinstance(run.get('resultSummary'), dict) else {}
        evidence_mode = str(summary.get('evidenceMode') or '')
        report_ready = bool(run.get('reportArtifactId'))
        has_evidence_refs = bool(
            report_ready
            or run.get('outputManifestArtifactId')
            or run.get('artifactIds')
            or run.get('figureArtifactIds')
            or run.get('resultArtifacts')
        )
        if status in _SUCCESS_STATUSES and evidence_mode == 'REAL_FEM' and report_ready:
            return 'REAL_FEM'
        if status in _SUCCESS_STATUSES and evidence_mode in _VERIFIED_EVIDENCE_MODES and report_ready:
            return 'VERIFIED'
        if status in _SUCCESS_STATUSES and has_evidence_refs:
            return 'LIMITED'
        return 'NOT_VERIFIED'

    @staticmethod
    def _artifact_refs(run: dict[str, Any]) -> list[dict[str, Any]]:
        refs: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add(artifact_id: Any, role: str, *, name: Any = None, kind: Any = None) -> None:
            resolved = str(artifact_id or '').strip()
            if not resolved or resolved in seen:
                return
            seen.add(resolved)
            item: dict[str, Any] = {'artifactId': resolved, 'role': role}
            if name:
                item['name'] = str(name)
            if kind:
                item['kind'] = str(kind)
            refs.append(item)

        add(run.get('reportArtifactId'), 'REPORT')
        add(run.get('outputManifestArtifactId'), 'OUTPUT_MANIFEST')
        for artifact_id in run.get('figureArtifactIds') or []:
            add(artifact_id, 'FIGURE')
        for artifact in run.get('resultArtifacts') or []:
            if isinstance(artifact, dict):
                add(
                    artifact.get('artifactId'),
                    'RESULT',
                    name=artifact.get('name'),
                    kind=artifact.get('kind'),
                )
        for artifact_id in run.get('artifactIds') or []:
            add(artifact_id, 'REGISTERED')
        return refs

    def _integrity(self, run: dict[str, Any], refs: list[dict[str, Any]]) -> dict[str, Any]:
        if not refs:
            return {'state': 'NOT_CHECKED', 'checkedArtifactCount': 0, 'issues': []}

        run_id = str(run.get('runId') or '')
        issues: list[dict[str, Any]] = []
        checked = 0
        for ref in refs:
            artifact_id = str(ref['artifactId'])
            try:
                record = self.artifact_store.get_artifact(artifact_id)
            except Exception:
                issues.append({'code': 'MISSING_ARTIFACT', 'artifactId': artifact_id})
                continue

            checked += 1
            artifact = record.artifact
            artifact_run_id = str(getattr(artifact, 'run_id', None) or '')
            if artifact_run_id and run_id and artifact_run_id != run_id:
                issues.append({
                    'code': 'RUN_MISMATCH',
                    'artifactId': artifact_id,
                    'expectedRunId': run_id,
                    'actualRunId': artifact_run_id,
                })

            expected_hash = str(getattr(artifact, 'sha256', None) or '')
            if expected_hash:
                try:
                    content = bytes(record.content)
                except Exception:
                    issues.append({'code': 'HASH_MISMATCH', 'artifactId': artifact_id})
                    continue
                actual_hash = sha256(content).hexdigest()
                if actual_hash != expected_hash:
                    issues.append({
                        'code': 'HASH_MISMATCH',
                        'artifactId': artifact_id,
                        'expectedSha256': expected_hash,
                        'actualSha256': actual_hash,
                    })

        issue_codes = {str(item['code']) for item in issues}
        if 'HASH_MISMATCH' in issue_codes:
            state = 'HASH_MISMATCH'
        elif 'RUN_MISMATCH' in issue_codes:
            state = 'RUN_MISMATCH'
        elif 'MISSING_ARTIFACT' in issue_codes:
            state = 'MISSING_ARTIFACT'
        else:
            state = 'VALID'
        return {'state': state, 'checkedArtifactCount': checked, 'issues': issues}

    @staticmethod
    def _comparison_claims(run: dict[str, Any]) -> list[dict[str, Any]]:
        summary = run.get('resultSummary') if isinstance(run.get('resultSummary'), dict) else {}
        comparison = summary.get('inquiryRunComparison')
        if not isinstance(comparison, dict):
            return []
        compatibility = str(comparison.get('compatibility') or '')
        claims: list[dict[str, Any]] = []
        for target in comparison.get('runs') or []:
            if not isinstance(target, dict):
                continue
            metrics = target.get('metrics')
            if not isinstance(metrics, dict):
                continue
            for metric_id, metric in metrics.items():
                if not isinstance(metric, dict) or not isinstance(metric.get('evidence'), dict):
                    continue
                value = metric.get('value')
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                unit = str(metric.get('unit') or '')
                label = str(metric.get('label') or metric_id)
                claims.append({
                    'claimId': f"{run.get('runId')}:{target.get('targetKey')}:{metric_id}",
                    'claimType': 'COMPARISON_METRIC',
                    'label': label,
                    'value': value,
                    'unit': unit,
                    'compatibility': compatibility,
                    'source': {
                        'comparisonRunId': run.get('runId'),
                        'targetRunId': target.get('runId'),
                        'targetKey': target.get('targetKey'),
                        'metricId': metric_id,
                        'evidence': metric['evidence'],
                    },
                })
        return claims

    def _run_evidence(self, run: dict[str, Any]) -> dict[str, Any]:
        summary = run.get('resultSummary') if isinstance(run.get('resultSummary'), dict) else {}
        contract = run.get('engineeringContract') if isinstance(run.get('engineeringContract'), dict) else {}
        artifacts = self._artifact_refs(run)
        integrity = self._integrity(run, artifacts)
        return {
            'runId': run.get('runId'),
            'sessionId': run.get('sessionId'),
            'taskType': run.get('taskType'),
            'status': run.get('status'),
            'currentStage': run.get('currentStage'),
            'createdAt': run.get('createdAt'),
            'updatedAt': run.get('updatedAt'),
            'trustState': self._trust_state(run),
            'integrityState': integrity['state'],
            'integrity': integrity,
            'evidenceMode': summary.get('evidenceMode'),
            'solverVersionProfile': run.get('solverVersionProfile') or (run.get('preflight') or {}).get('solverVersionProfile'),
            'inputProvenance': list(run.get('inputProvenance') or []),
            'contractHash': contract.get('contractHash') or run.get('workflowSha256'),
            'artifacts': artifacts,
            'claims': self._comparison_claims(run),
            'narrativeSummary': summary.get('narrativeSummary') or summary.get('message'),
        }

    def build(self, project_id: str, owner: str = DEFAULT_OWNER) -> dict[str, Any]:
        project = self.project_service.get_project(project_id, owner=owner)
        repository = self.agent_service.repository()
        full_runs: list[dict[str, Any]] = []
        for summary in project.get('runs') or []:
            run_id = str(summary.get('runId') or '')
            if not run_id:
                continue
            run = repository.get_run(run_id)
            if run is not None:
                full_runs.append(run)

        runs = [self._run_evidence(run) for run in full_runs]
        counts = {'REAL_FEM': 0, 'VERIFIED': 0, 'LIMITED': 0, 'NOT_VERIFIED': 0}
        integrity_counts = {state: 0 for state in _INTEGRITY_STATES}
        for run in runs:
            trust_state = str(run['trustState'])
            counts[trust_state] = counts.get(trust_state, 0) + 1
            integrity_state = str(run['integrityState'])
            integrity_counts[integrity_state] = integrity_counts.get(integrity_state, 0) + 1

        claims = [claim for run in runs for claim in run['claims']]
        workspace = dict(project.get('workspace') or {})
        return {
            'schemaVersion': '1.1',
            'generatedAt': utc_now(),
            'projectId': project.get('projectId'),
            'projectName': project.get('name'),
            'workspaceRevision': project.get('workspaceRevision'),
            'workspaceSnapshot': workspace,
            'trustCounts': counts,
            'integrityCounts': integrity_counts,
            'runs': runs,
            'claims': claims,
            'projectReport': {
                'schemaVersion': '1.1',
                'reportType': 'PROJECT_EVIDENCE_INDEX',
                'projectId': project.get('projectId'),
                'projectName': project.get('name'),
                'workspaceRevision': project.get('workspaceRevision'),
                'workspaceSnapshot': workspace,
                'runCount': len(runs),
                'trustedRunCount': counts['REAL_FEM'] + counts['VERIFIED'],
                'integrityValidRunCount': integrity_counts['VALID'],
                'claimCount': len(claims),
                'evidenceIndex': [
                    {
                        'runId': run['runId'],
                        'trustState': run['trustState'],
                        'integrityState': run['integrityState'],
                        'evidenceMode': run['evidenceMode'],
                        'artifactIds': [item['artifactId'] for item in run['artifacts']],
                        'claimIds': [item['claimId'] for item in run['claims']],
                    }
                    for run in runs
                ],
                'limitations': [
                    'This project report is a deterministic evidence index, not a replacement for registered per-run engineering reports.',
                    'trustState describes the persisted Run evidence contract; integrityState independently re-checks currently registered Artifact bytes and ownership.',
                    'Exact numerical claims are included only when an existing result carries an explicit evidence mapping.',
                    'For other engineering numbers, inspect the registered per-run report/artifacts instead of conversation history or summaries.',
                    'CROSS_SOLVER comparison evidence is validation-only and must not be interpreted as scheme ranking.',
                ],
            },
        }


engineering_project_evidence_service = EngineeringProjectEvidenceService()
