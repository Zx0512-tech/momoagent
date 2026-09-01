from __future__ import annotations

import json
import math
from hashlib import sha256
from typing import Any, Callable

from fastapi import HTTPException

from app.agents.inquiry import RESPONSE_COLUMN_ALIASES, RESPONSE_METRIC_SPECS, resolve_column
from app.services.agent_project_context import engineering_project_context_service
from app.services.agent_repository import AgentRepository, DEFAULT_OWNER
from app.services.platform_store import platform_store
from app.services.result_inquiry import ResultInquiryError, ResultInquiryService


DIRECT = 'DIRECT'
CROSS_SOLVER = 'CROSS_SOLVER'
LIMITED = 'LIMITED'
NOT_COMPARABLE = 'NOT_COMPARABLE'


class RunComparisonError(ValueError):
    """Cross-run comparison rejected because its engineering contract is not comparable."""


class CrossRunComparisonService:
    """Compare verified engineering runs without making the LLM a numeric evidence source.

    ANALYSIS values are read from the verified result catalog and registered CSVs.
    Optimization values are read from the registered optimization summary/TOPSIS rows.
    Multi-case comparison/sweep runs require a case selector unless exactly one verified case exists.
    """

    max_targets = 8

    def compare(
        self,
        *,
        repository: AgentRepository,
        session_id: str,
        targets: list[dict[str, Any]],
        baseline_run_id: str | None = None,
        metric_ids: list[str] | None = None,
        owner: str | None = None,
        catalog_loader: Callable[[dict[str, Any]], dict[str, Any] | None],
    ) -> dict[str, Any]:
        normalized_targets = self._normalize_targets(targets, baseline_run_id=baseline_run_id)
        run_ids = [item['runId'] for item in normalized_targets]
        runs = [self._required_run(repository, run_id) for run_id in run_ids]
        resolved_owner = str(owner or DEFAULT_OWNER)
        if any(str(run.get('ownerId') or resolved_owner) != resolved_owner for run in runs):
            raise RunComparisonError('比较对象不属于当前 owner。')
        scoped = engineering_project_context_service.filter_runs_to_project(
            runs,
            session_id=session_id,
            owner=resolved_owner,
        )
        scoped_ids = {str(run.get('runId') or '') for run in scoped}
        if scoped_ids != set(run_ids):
            raise RunComparisonError('比较对象必须属于当前 Project；跨 Project Run 不允许进入同一比较。')

        selected_metrics = self._normalize_metric_ids(metric_ids)
        snapshots = [
            self._snapshot(
                run,
                selector=target,
                metric_ids=selected_metrics,
                catalog_loader=catalog_loader,
            )
            for run, target in zip(runs, normalized_targets)
        ]
        available = self._available_metric_ids(snapshots, selected_metrics)
        if not available:
            raise RunComparisonError('所选 Run 没有共同的已登记工程指标可比较。')

        baseline_id = str(baseline_run_id or '') or None
        baseline = next((item for item in snapshots if item['runId'] == baseline_id), None)
        comparisons = []
        if baseline is not None:
            for item in snapshots:
                if item['runId'] == baseline['runId']:
                    continue
                compatibility = self._compatibility(baseline, item)
                comparisons.append({
                    'baselineRunId': baseline['runId'],
                    'runId': item['runId'],
                    'compatibility': compatibility,
                    'metrics': self._deltas(
                        baseline,
                        item,
                        available,
                        compatibility=compatibility,
                    ),
                })

        overall = self._overall_compatibility(snapshots, baseline=baseline)
        rankings = self._rankings(snapshots, available) if overall == DIRECT else []
        warnings = self._warnings(snapshots, overall=overall, baseline=baseline)
        return {
            'schemaVersion': '1.0',
            'sessionId': session_id,
            'projectId': self._project_id(session_id, resolved_owner),
            'baselineRunId': baseline_id,
            'compatibility': overall,
            'metricIds': available,
            'runs': snapshots,
            'comparisons': comparisons,
            'rankings': rankings,
            'warnings': warnings,
            'interpretationLimit': (
                'DIRECT 才表示同模型/同荷载条件下的方案性能比较；CROSS_SOLVER 只用于求解器一致性验证；'
                'LIMITED/NOT_COMPARABLE 不得据此给出方案优劣或改善率结论。'
            ),
        }

    def _project_id(self, session_id: str, owner: str) -> str | None:
        project = engineering_project_context_service.project_for_session(session_id, owner=owner)
        return str(project.get('projectId')) if project else None

    def _normalize_targets(
        self,
        targets: list[dict[str, Any]],
        *,
        baseline_run_id: str | None,
    ) -> list[dict[str, Any]]:
        if not isinstance(targets, list) or len(targets) < 2 or len(targets) > self.max_targets:
            raise RunComparisonError(f'跨 Run 比较需要 2–{self.max_targets} 个比较对象。')
        normalized: list[dict[str, Any]] = []
        seen: set[tuple[str, str | None, int | None]] = set()
        for raw in targets:
            run_id = str((raw or {}).get('runId') or '').strip()
            if not run_id:
                raise RunComparisonError('每个比较对象都必须提供 runId。')
            case_id = str((raw or {}).get('caseId') or '').strip() or None
            rank_raw = (raw or {}).get('candidateRank')
            candidate_rank = int(rank_raw) if rank_raw is not None else None
            if candidate_rank is not None and not 1 <= candidate_rank <= 50:
                raise RunComparisonError('candidateRank 必须在 1–50 之间。')
            key = (run_id, case_id, candidate_rank)
            if key in seen:
                continue
            seen.add(key)
            normalized.append({
                'runId': run_id,
                **({'caseId': case_id} if case_id else {}),
                **({'candidateRank': candidate_rank} if candidate_rank is not None else {}),
            })
        if baseline_run_id and str(baseline_run_id) not in {item['runId'] for item in normalized}:
            normalized.insert(0, {'runId': str(baseline_run_id)})
        if len(normalized) > self.max_targets:
            raise RunComparisonError(f'加入 baseline 后比较对象超过 {self.max_targets} 个。')
        return normalized

    @staticmethod
    def _required_run(repository: AgentRepository, run_id: str) -> dict[str, Any]:
        run = repository.get_run(run_id)
        if run is None:
            raise RunComparisonError(f'运行 {run_id} 不存在。')
        summary = run.get('resultSummary') if isinstance(run.get('resultSummary'), dict) else {}
        if (
            run.get('status') != 'SUCCEEDED'
            or summary.get('evidenceMode') != 'REAL_FEM'
            or not run.get('reportArtifactId')
        ):
            raise RunComparisonError(f'运行 {run_id} 不是 SUCCEEDED + REAL_FEM 的正式结果。')
        return run

    @staticmethod
    def _normalize_metric_ids(metric_ids: list[str] | None) -> list[str]:
        if not metric_ids:
            return list(RESPONSE_METRIC_SPECS)
        result = list(dict.fromkeys(str(item) for item in metric_ids if str(item)))
        unknown = [item for item in result if item not in RESPONSE_METRIC_SPECS]
        if unknown:
            raise RunComparisonError(f'未登记的比较指标: {", ".join(unknown)}')
        return result

    def _snapshot(
        self,
        run: dict[str, Any],
        *,
        selector: dict[str, Any],
        metric_ids: list[str],
        catalog_loader: Callable[[dict[str, Any]], dict[str, Any] | None],
    ) -> dict[str, Any]:
        task_type = str(run.get('taskType') or '')
        if task_type == 'ANALYSIS':
            metrics = self._analysis_metrics(run, metric_ids, catalog_loader=catalog_loader)
            selector_meta: dict[str, Any] = {}
        elif task_type == 'DAMPER_OPTIMIZATION':
            rank = int(selector.get('candidateRank') or 1)
            metrics = self._optimization_metrics(run, metric_ids, candidate_rank=rank)
            selector_meta = {'candidateRank': rank}
        elif task_type in {'DAMPER_COMPARISON', 'DAMPER_PARAMETER_SWEEP'}:
            case = self._selected_case(run, selector.get('caseId'))
            metrics = self._objective_metrics(
                case.get('objectives') or {},
                metric_ids,
                evidence={'artifactId': run.get('reportArtifactId'), 'caseId': case.get('caseId')},
            )
            selector_meta = {'caseId': str(case.get('caseId') or '')}
        else:
            raise RunComparisonError(f'运行 {run.get("runId")} 的任务类型 {task_type} 暂不支持跨 Run 比较。')

        contract = run.get('workflowContract') if isinstance(run.get('workflowContract'), dict) else {}
        intent = run.get('intent') if isinstance(run.get('intent'), dict) else {}
        model_sha = contract.get('modelSha256')
        load_sha = contract.get('loadSha256')
        default_model = contract.get('model') == 'STbridge' and not (contract.get('modelArtifactId') or intent.get('modelArtifactId'))
        default_load = not contract.get('loadArtifactId') and str(contract.get('loadKind') or intent.get('loadKind') or '') in {'EARTHQUAKE', 'WIND', 'TRAFFIC'}
        return {
            'runId': str(run.get('runId') or ''),
            'taskType': task_type,
            'solver': contract.get('solver') or intent.get('solver'),
            'loadKind': contract.get('loadKind') or intent.get('loadKind'),
            'modelArtifactId': contract.get('modelArtifactId') or intent.get('modelArtifactId'),
            'modelSha256': model_sha,
            'modelIdentity': (f'SHA256:{model_sha}' if model_sha else 'REGISTERED_MODEL:STbridge' if default_model else None),
            'loadArtifactId': contract.get('loadArtifactId'),
            'loadSha256': load_sha,
            'loadIdentity': (f'SHA256:{load_sha}' if load_sha else f'REGISTERED_DEFAULT_LOAD:{contract.get("loadKind") or intent.get("loadKind")}' if default_load else None),
            'responseIds': list(contract.get('responseIds') or intent.get('responseIds') or []),
            'reportArtifactId': run.get('reportArtifactId'),
            **selector_meta,
            'metrics': metrics,
        }

    def _analysis_metrics(
        self,
        run: dict[str, Any],
        metric_ids: list[str],
        *,
        catalog_loader: Callable[[dict[str, Any]], dict[str, Any] | None],
    ) -> dict[str, Any]:
        catalog = catalog_loader(run)
        if catalog is None:
            raise RunComparisonError(f'运行 {run.get("runId")} 缺少已验证 result_catalog.json。')
        inquiry = ResultInquiryService(platform_store)
        result: dict[str, Any] = {}
        for metric_id in metric_ids:
            spec = RESPONSE_METRIC_SPECS[metric_id]
            candidates: list[tuple[dict[str, Any], str]] = []
            for entry in catalog.get('entries') or []:
                column = resolve_column(list(entry.get('columns') or []), spec['semantic'], RESPONSE_COLUMN_ALIASES)
                if column:
                    candidates.append((entry, column))
            if len(candidates) != 1:
                continue
            entry, column = candidates[0]
            declared_unit = str((entry.get('units') or {}).get(column) or '')
            if declared_unit != str(spec['unit']):
                continue
            try:
                peak = inquiry.peak(str(entry['artifactId']), column=column)
            except ResultInquiryError:
                continue
            result[metric_id] = {
                'value': float(peak['peakAbsolute']),
                'unit': spec['unit'],
                'label': spec['label'],
                'direction': 'LOWER_IS_BETTER',
                'evidence': {
                    'artifactId': entry['artifactId'],
                    'artifactSha256': entry['sha256'],
                    'column': column,
                },
            }
        return result

    def _optimization_metrics(
        self,
        run: dict[str, Any],
        metric_ids: list[str],
        *,
        candidate_rank: int,
    ) -> dict[str, Any]:
        artifact_id = self._optimization_summary_artifact(run)
        try:
            topsis = ResultInquiryService(platform_store).topsis(artifact_id, limit=candidate_rank)
        except ResultInquiryError as exc:
            raise RunComparisonError(str(exc)) from exc
        rows = list(topsis.get('rows') or [])
        if len(rows) < candidate_rank:
            raise RunComparisonError(f'运行 {run.get("runId")} 没有 TOPSIS 第 {candidate_rank} 名候选。')
        row = rows[candidate_rank - 1]
        return self._objective_metrics(
            row.get('objectives') or {},
            metric_ids,
            evidence={
                'artifactId': artifact_id,
                'candidateRank': candidate_rank,
                'paretoIndex': row.get('paretoIndex'),
            },
        )

    @staticmethod
    def _optimization_summary_artifact(run: dict[str, Any]) -> str:
        candidates = []
        for artifact_id in run.get('artifactIds') or []:
            try:
                record = platform_store.get_artifact(str(artifact_id))
            except Exception:
                continue
            if str(getattr(record.artifact, 'name', '')) in {
                'real_optimization_summary.json', 'optimization_summary.json',
            }:
                candidates.append(str(artifact_id))
        if len(candidates) != 1:
            raise RunComparisonError(f'运行 {run.get("runId")} 没有唯一登记的优化摘要。')
        return candidates[0]

    def _selected_case(self, run: dict[str, Any], case_id: Any) -> dict[str, Any]:
        report = self._load_report(run)
        cases = [
            item for item in report.get('caseResults') or []
            if isinstance(item, dict) and item.get('isVerifiedSolverOutput') is True
        ]
        if case_id:
            selected = next((item for item in cases if str(item.get('caseId')) == str(case_id)), None)
            if selected is None:
                raise RunComparisonError(f'运行 {run.get("runId")} 不存在已验证 caseId={case_id}。')
            return selected
        if len(cases) != 1:
            raise RunComparisonError(
                f'运行 {run.get("runId")} 包含 {len(cases)} 个已验证 case；跨 Run 比较必须明确 caseId。'
            )
        return cases[0]

    @staticmethod
    def _load_report(run: dict[str, Any]) -> dict[str, Any]:
        artifact_id = str(run.get('reportArtifactId') or '')
        try:
            record = platform_store.get_artifact(artifact_id)
        except Exception as exc:
            raise RunComparisonError(f'运行 {run.get("runId")} 的登记报告不存在。') from exc
        artifact = record.artifact
        if getattr(artifact, 'kind', None) not in {'JSON_SUMMARY', 'OPTIMIZATION_REPORT'}:
            raise RunComparisonError('登记报告制品类型无效。')
        content = bytes(record.content)
        if sha256(content).hexdigest() != str(getattr(artifact, 'sha256', '')):
            raise RunComparisonError('登记报告 SHA256 校验失败。')
        try:
            payload = json.loads(content.decode('utf-8-sig'))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RunComparisonError('登记报告不是有效 JSON。') from exc
        if not isinstance(payload, dict) or payload.get('agentRunId') != run.get('runId'):
            raise RunComparisonError('登记报告与 Run 身份不一致。')
        return payload

    @staticmethod
    def _objective_metrics(
        objectives: dict[str, Any],
        metric_ids: list[str],
        *,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = {
            str(key).split(':', 1)[-1]: value
            for key, value in objectives.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))
        }
        result = {}
        for metric_id in metric_ids:
            if metric_id not in normalized:
                continue
            spec = RESPONSE_METRIC_SPECS[metric_id]
            result[metric_id] = {
                'value': float(normalized[metric_id]),
                'unit': spec['unit'],
                'label': spec['label'],
                'direction': 'LOWER_IS_BETTER',
                'evidence': dict(evidence),
            }
        return result

    @staticmethod
    def _available_metric_ids(snapshots: list[dict[str, Any]], requested: list[str]) -> list[str]:
        return [metric_id for metric_id in requested if all(metric_id in item['metrics'] for item in snapshots)]

    @staticmethod
    def _identity_state(left: Any, right: Any) -> str:
        if left and right:
            return 'SAME' if str(left) == str(right) else 'DIFFERENT'
        return 'UNKNOWN'

    def _compatibility(self, left: dict[str, Any], right: dict[str, Any]) -> str:
        if left.get('loadKind') and right.get('loadKind') and left['loadKind'] != right['loadKind']:
            return NOT_COMPARABLE
        model = self._identity_state(left.get('modelIdentity'), right.get('modelIdentity'))
        load = self._identity_state(left.get('loadIdentity'), right.get('loadIdentity'))
        if model == 'DIFFERENT' or load == 'DIFFERENT':
            return NOT_COMPARABLE
        if model != 'SAME' or load != 'SAME':
            return LIMITED
        if left.get('solver') != right.get('solver'):
            return CROSS_SOLVER
        return DIRECT

    def _overall_compatibility(
        self,
        snapshots: list[dict[str, Any]],
        *,
        baseline: dict[str, Any] | None,
    ) -> str:
        if baseline is not None:
            levels = [self._compatibility(baseline, item) for item in snapshots if item is not baseline]
        else:
            anchor = snapshots[0]
            levels = [self._compatibility(anchor, item) for item in snapshots[1:]]
        if NOT_COMPARABLE in levels:
            return NOT_COMPARABLE
        if LIMITED in levels:
            return LIMITED
        if CROSS_SOLVER in levels:
            return CROSS_SOLVER
        return DIRECT

    @staticmethod
    def _deltas(
        baseline: dict[str, Any],
        item: dict[str, Any],
        metric_ids: list[str],
        *,
        compatibility: str,
    ) -> dict[str, Any]:
        if compatibility not in {DIRECT, CROSS_SOLVER}:
            return {}
        result = {}
        for metric_id in metric_ids:
            left = float(baseline['metrics'][metric_id]['value'])
            right = float(item['metrics'][metric_id]['value'])
            difference = right - left
            relative = None if left == 0 else difference / abs(left)
            result[metric_id] = {
                'baseline': left,
                'candidate': right,
                'difference': difference,
                'relativeChange': relative,
                'relativeChangePercent': None if relative is None else round(relative * 100.0, 6),
                'unit': baseline['metrics'][metric_id]['unit'],
                'interpretation': 'SOLVER_DIFFERENCE' if compatibility == CROSS_SOLVER else 'PERFORMANCE_CHANGE',
            }
        return result

    @staticmethod
    def _rankings(snapshots: list[dict[str, Any]], metric_ids: list[str]) -> list[dict[str, Any]]:
        rankings = []
        for metric_id in metric_ids:
            ordered = sorted(snapshots, key=lambda item: float(item['metrics'][metric_id]['value']))
            rankings.append({
                'metricId': metric_id,
                'direction': 'LOWER_IS_BETTER',
                'rows': [
                    {'rank': rank, 'runId': item['runId'], 'value': item['metrics'][metric_id]['value']}
                    for rank, item in enumerate(ordered, start=1)
                ],
            })
        return rankings

    @staticmethod
    def _warnings(
        snapshots: list[dict[str, Any]],
        *,
        overall: str,
        baseline: dict[str, Any] | None,
    ) -> list[str]:
        warnings: list[str] = []
        if baseline is None:
            warnings.append('未指定 baseline：不会生成相对基线改善率。')
        if overall == CROSS_SOLVER:
            warnings.append('存在不同求解器：差值只用于求解器一致性验证，不表示方案优劣。')
        elif overall == LIMITED:
            warnings.append('模型或荷载 SHA256 不完整：仅展示登记值，不生成改善率或方案排名。')
        elif overall == NOT_COMPARABLE:
            warnings.append('模型、荷载或工况不一致：这些 Run 不允许直接做性能排序。')
        if any(not item.get('modelIdentity') for item in snapshots):
            warnings.append('至少一个 Run 缺少可核验的模型身份。')
        if any(not item.get('loadIdentity') for item in snapshots):
            warnings.append('至少一个 Run 缺少可核验的荷载身份。')
        return list(dict.fromkeys(warnings))


cross_run_comparison_service = CrossRunComparisonService()
