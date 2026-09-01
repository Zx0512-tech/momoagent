from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding='utf-8')
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{path}: expected exactly one match, got {count}: {old[:120]!r}')
    target.write_text(text.replace(old, new, 1), encoding='utf-8')


# inquiry models: compare_runs is a Harness-context tool, deliberately not registered in InquiryTools.
replace_once(
    'momo_agent/backend/app/agents/inquiry.py',
    'from typing import Any\n',
    'from typing import Any, Literal\n',
)
replace_once(
    'momo_agent/backend/app/agents/inquiry.py',
    "class InquiryTools:\n",
    '''class ResultRunTarget(StrictToolModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)

    run_id: str = Field(alias='runId', pattern=r'^[A-Za-z0-9_-]{1,128}$')
    case_id: str | None = Field(default=None, alias='caseId', min_length=1, max_length=128)
    candidate_rank: int | None = Field(default=None, alias='candidateRank', ge=1, le=50)


class ResultCompareRunsInput(StrictToolModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)

    targets: list[ResultRunTarget] = Field(min_length=2, max_length=8)
    baseline_run_id: str | None = Field(
        default=None,
        alias='baselineRunId',
        pattern=r'^[A-Za-z0-9_-]{1,128}$',
    )
    metric_ids: list[str] = Field(default_factory=list, alias='metricIds', max_length=16)


class ResultCompareRunsOutput(StrictToolModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)

    schema_version: str = Field(alias='schemaVersion')
    session_id: str = Field(alias='sessionId')
    project_id: str | None = Field(default=None, alias='projectId')
    baseline_run_id: str | None = Field(default=None, alias='baselineRunId')
    compatibility: Literal['DIRECT', 'CROSS_SOLVER', 'LIMITED', 'NOT_COMPARABLE']
    metric_ids: list[str] = Field(alias='metricIds')
    runs: list[dict[str, Any]]
    comparisons: list[dict[str, Any]]
    rankings: list[dict[str, Any]]
    warnings: list[str]
    interpretation_limit: str = Field(alias='interpretationLimit')


class InquiryTools:
''',
)

# RESULT_INQUIRY workflow gains the first-class cross-run comparison tool.
replace_once(
    'momo_agent/backend/app/agents/workflows.py',
    "workflowId='result_inquiry',\n        version='1.0.0',",
    "workflowId='result_inquiry',\n        version='1.1.0',",
)
replace_once(
    'momo_agent/backend/app/agents/workflows.py',
    "('result.columns', 'result.peak', 'result.at_time', 'result.correlate', 'result.compare', 'result.topsis', 'result.sweep_cases')",
    "('result.columns', 'result.peak', 'result.at_time', 'result.correlate', 'result.compare', 'result.compare_runs', 'result.topsis', 'result.sweep_cases')",
)

# Harness imports and tool registration.
replace_once(
    'momo_agent/backend/app/services/agent_harness.py',
    "    ResultCorrelateInput,\n    ResultDerivedInput,\n",
    "    ResultCorrelateInput,\n    ResultCompareRunsInput,\n    ResultCompareRunsOutput,\n    ResultDerivedInput,\n",
)
replace_once(
    'momo_agent/backend/app/services/agent_harness.py',
    "from app.services.result_inquiry import ResultInquiryService\n",
    "from app.services.result_inquiry import ResultInquiryService\nfrom app.services.agent_run_comparison import RunComparisonError, cross_run_comparison_service\n",
)
replace_once(
    'momo_agent/backend/app/services/agent_harness.py',
    "    'result.topsis': HarnessToolSpec(\n",
    "    'result.compare_runs': HarnessToolSpec(\n        '当用户需要比较 2–8 个同一 Project 的 SUCCEEDED + REAL_FEM 历史 Run 时使用；服务端先校验模型/荷载身份和单位，再计算基线差值、相对变化与允许的排名。跨求解器只用于一致性验证，不把差异解释为方案优劣。',\n        ResultCompareRunsInput,\n    ),\n    'result.topsis': HarnessToolSpec(\n",
)
replace_once(
    'momo_agent/backend/app/services/agent_harness.py',
    "    'result.compare',\n    'result.correlate',",
    "    'result.compare',\n    'result.compare_runs',\n    'result.correlate',",
)

# Native inquiry execution: compare_runs is repository/project scoped, not single-artifact scoped.
old_exec = '''                    artifact_id = call.arguments.get('artifact_id') or call.arguments.get('artifactId')
                    if artifact_id not in set(artifacts.values()):
                        raise ToolExecutionError(
                            'ARTIFACT_NOT_REGISTERED',
                            '结果追问只能读取当前结果目录登记的只读制品。',
                        )
                    output = tools.call(call.name, call.arguments).model_dump(by_alias=True, mode='json')
                    effective_arguments = _HARNESS_TOOL_SPECS[call.name].input_model.model_validate(
                        call.arguments,
                    ).model_dump(by_alias=True, mode='json')
'''
new_exec = '''                    if call.name == 'result.compare_runs':
                        try:
                            validated = ResultCompareRunsInput.model_validate(call.arguments)
                            effective_arguments = validated.model_dump(by_alias=True, mode='json')
                            comparison = cross_run_comparison_service.compare(
                                repository=repository,
                                session_id=session['sessionId'],
                                targets=effective_arguments['targets'],
                                baseline_run_id=effective_arguments.get('baselineRunId'),
                                metric_ids=effective_arguments.get('metricIds') or None,
                                owner=str(session.get('ownerId') or 'local'),
                                catalog_loader=self._load_result_catalog,
                            )
                            output = ResultCompareRunsOutput.model_validate(comparison).model_dump(
                                by_alias=True,
                                mode='json',
                            )
                        except (RunComparisonError, ValidationError) as exc:
                            raise ToolExecutionError(
                                'RUN_COMPARISON_INVALID',
                                str(exc),
                            ) from exc
                    else:
                        artifact_id = call.arguments.get('artifact_id') or call.arguments.get('artifactId')
                        if artifact_id not in set(artifacts.values()):
                            raise ToolExecutionError(
                                'ARTIFACT_NOT_REGISTERED',
                                '结果追问只能读取当前结果目录登记的只读制品。',
                            )
                        output = tools.call(call.name, call.arguments).model_dump(by_alias=True, mode='json')
                        effective_arguments = _HARNESS_TOOL_SPECS[call.name].input_model.model_validate(
                            call.arguments,
                        ).model_dump(by_alias=True, mode='json')
'''
replace_once('momo_agent/backend/app/services/agent_harness.py', old_exec, new_exec)

# Store comparison facts alongside existing inquiry projections.
replace_once(
    'momo_agent/backend/app/services/agent_harness.py',
    "                inquiry_topsis_weights = self._structured_inquiry_topsis_weights(query_results)\n                run.update({",
    "                inquiry_topsis_weights = self._structured_inquiry_topsis_weights(query_results)\n                inquiry_run_comparison = self._structured_inquiry_run_comparison(query_results)\n                run.update({",
)
replace_once(
    'momo_agent/backend/app/services/agent_harness.py',
    "                        **({'inquiryTopsisWeights': inquiry_topsis_weights} if inquiry_topsis_weights else {}),\n                        'queryProgress': {\n                            'completed': len(inquiry_metrics) + len(inquiry_topsis),",
    "                        **({'inquiryTopsisWeights': inquiry_topsis_weights} if inquiry_topsis_weights else {}),\n                        **({'inquiryRunComparison': inquiry_run_comparison} if inquiry_run_comparison else {}),\n                        'queryProgress': {\n                            'completed': len(inquiry_metrics) + len(inquiry_topsis) + (1 if inquiry_run_comparison else 0),",
)
replace_once(
    'momo_agent/backend/app/services/agent_harness.py',
    "        inquiry_topsis_weights = self._structured_inquiry_topsis_weights(query_results)\n        visible_message = (\n            self._deterministic_inquiry_answer(query_results)\n            if inquiry_metrics or inquiry_topsis\n            else answer\n        )",
    "        inquiry_topsis_weights = self._structured_inquiry_topsis_weights(query_results)\n        inquiry_run_comparison = self._structured_inquiry_run_comparison(query_results)\n        visible_message = (\n            self._deterministic_inquiry_answer(query_results)\n            if inquiry_metrics or inquiry_topsis or inquiry_run_comparison\n            else answer\n        )",
)
replace_once(
    'momo_agent/backend/app/services/agent_harness.py',
    "                **({'inquiryTopsisWeights': inquiry_topsis_weights} if inquiry_topsis_weights else {}),\n                'queryProgress': {\n                    'completed': len(inquiry_metrics) + len(inquiry_topsis),",
    "                **({'inquiryTopsisWeights': inquiry_topsis_weights} if inquiry_topsis_weights else {}),\n                **({'inquiryRunComparison': inquiry_run_comparison} if inquiry_run_comparison else {}),\n                'queryProgress': {\n                    'completed': len(inquiry_metrics) + len(inquiry_topsis) + (1 if inquiry_run_comparison else 0),",
)
replace_once(
    'momo_agent/backend/app/services/agent_harness.py',
    '''        topsis = WorkflowHarnessMixin._structured_inquiry_topsis(query_results)
        if topsis:
            return f'已读取 TOPSIS 前 {len(topsis)} 项候选，排名和数值均来自已登记的优化摘要。'
        count = len(WorkflowHarnessMixin._structured_inquiry_metrics(query_results))
''',
    '''        comparison = WorkflowHarnessMixin._structured_inquiry_run_comparison(query_results)
        if comparison:
            return (
                f'已完成 {len(comparison.get("runs") or [])} 个工程对象的跨 Run 比较；'
                f'可比性为 {comparison.get("compatibility")}，数值均来自已登记工程证据。'
            )
        topsis = WorkflowHarnessMixin._structured_inquiry_topsis(query_results)
        if topsis:
            return f'已读取 TOPSIS 前 {len(topsis)} 项候选，排名和数值均来自已登记的优化摘要。'
        count = len(WorkflowHarnessMixin._structured_inquiry_metrics(query_results))
''',
)
replace_once(
    'momo_agent/backend/app/services/agent_harness.py',
    "    @staticmethod\n    def _structured_inquiry_topsis(query_results: list[dict[str, Any]]) -> list[dict[str, Any]]:\n",
    "    @staticmethod\n    def _structured_inquiry_run_comparison(query_results: list[dict[str, Any]]) -> dict[str, Any] | None:\n        for item in reversed(query_results):\n            if item.get('tool') == 'result.compare_runs' and isinstance(item.get('output'), dict):\n                return dict(item['output'])\n        return None\n\n    @staticmethod\n    def _structured_inquiry_topsis(query_results: list[dict[str, Any]]) -> list[dict[str, Any]]:\n",
)

# Comparison service: owner isolation and stable registered identities for default STbridge inputs.
replace_once(
    'momo_agent/backend/app/services/agent_run_comparison.py',
    "        runs = [self._required_run(repository, run_id) for run_id in run_ids]\n        resolved_owner = str(owner or DEFAULT_OWNER)\n        scoped = engineering_project_context_service.filter_runs_to_project(",
    "        runs = [self._required_run(repository, run_id) for run_id in run_ids]\n        resolved_owner = str(owner or DEFAULT_OWNER)\n        if any(str(run.get('ownerId') or resolved_owner) != resolved_owner for run in runs):\n            raise RunComparisonError('比较对象不属于当前 owner。')\n        scoped = engineering_project_context_service.filter_runs_to_project(",
)
replace_once(
    'momo_agent/backend/app/services/agent_run_comparison.py',
    "        return {\n            'runId': str(run.get('runId') or ''),",
    "        model_sha = contract.get('modelSha256')\n        load_sha = contract.get('loadSha256')\n        default_model = contract.get('model') == 'STbridge' and not (contract.get('modelArtifactId') or intent.get('modelArtifactId'))\n        default_load = not contract.get('loadArtifactId') and str(contract.get('loadKind') or intent.get('loadKind') or '') in {'EARTHQUAKE', 'WIND', 'TRAFFIC'}\n        return {\n            'runId': str(run.get('runId') or ''),",
)
replace_once(
    'momo_agent/backend/app/services/agent_run_comparison.py',
    "            'modelSha256': contract.get('modelSha256'),\n            'loadArtifactId': contract.get('loadArtifactId'),\n            'loadSha256': contract.get('loadSha256'),",
    "            'modelSha256': model_sha,\n            'modelIdentity': (f'SHA256:{model_sha}' if model_sha else 'REGISTERED_MODEL:STbridge' if default_model else None),\n            'loadArtifactId': contract.get('loadArtifactId'),\n            'loadSha256': load_sha,\n            'loadIdentity': (f'SHA256:{load_sha}' if load_sha else f'REGISTERED_DEFAULT_LOAD:{contract.get(\"loadKind\") or intent.get(\"loadKind\")}' if default_load else None),",
)
replace_once(
    'momo_agent/backend/app/services/agent_run_comparison.py',
    "        model = self._identity_state(left.get('modelSha256'), right.get('modelSha256'))\n        load = self._identity_state(left.get('loadSha256'), right.get('loadSha256'))",
    "        model = self._identity_state(left.get('modelIdentity'), right.get('modelIdentity'))\n        load = self._identity_state(left.get('loadIdentity'), right.get('loadIdentity'))",
)
replace_once(
    'momo_agent/backend/app/services/agent_run_comparison.py',
    "        if any(not item.get('modelSha256') for item in snapshots):\n            warnings.append('至少一个 Run 缺少 modelSha256。')\n        if any(not item.get('loadSha256') for item in snapshots):\n            warnings.append('至少一个 Run 缺少 loadSha256。')",
    "        if any(not item.get('modelIdentity') for item in snapshots):\n            warnings.append('至少一个 Run 缺少可核验的模型身份。')\n        if any(not item.get('loadIdentity') for item in snapshots):\n            warnings.append('至少一个 Run 缺少可核验的荷载身份。')",
)

# Harness catalog test.
replace_once(
    'momo_agent/backend/tests/test_agent_harness.py',
    "        'result.compare',\n        'result.correlate',",
    "        'result.compare',\n        'result.compare_runs',\n        'result.correlate',",
)
replace_once(
    'momo_agent/backend/tests/test_agent_harness.py',
    "    assert 'result.compare' in names\n    assert 'result.peak' in names",
    "    assert 'result.compare' in names\n    assert 'result.compare_runs' in names\n    assert 'result.peak' in names",
)
replace_once(
    'momo_agent/backend/tests/test_agent_harness.py',
    "    inquiry_compare = first[names.index('result.compare')]\n    engineering_compare = _HARNESS_TOOL_SPECS['comparison.compare']",
    "    inquiry_compare = first[names.index('result.compare')]\n    cross_run_compare = first[names.index('result.compare_runs')]\n    engineering_compare = _HARNESS_TOOL_SPECS['comparison.compare']",
)
replace_once(
    'momo_agent/backend/tests/test_agent_harness.py',
    "    assert 'CSV' in inquiry_compare['description']\n    engineering_schema = engineering_compare.input_model.model_json_schema(by_alias=True)",
    "    assert 'CSV' in inquiry_compare['description']\n    assert set(cross_run_compare['inputSchema']['properties']) == {'targets', 'baselineRunId', 'metricIds'}\n    assert cross_run_compare['inputSchema']['properties']['targets']['maxItems'] == 8\n    assert 'Project' in cross_run_compare['description']\n    engineering_schema = engineering_compare.input_model.model_json_schema(by_alias=True)",
)

# Frontend API types and resultSummary projection.
replace_once(
    'platform-ui/src/api/agentApi.ts',
    "export interface InquiryTopsisWeights {\n  objectiveNames: string[];\n  weights: number[];\n}\n\nexport type AgentMessageStreamEvent",
    '''export interface InquiryTopsisWeights {
  objectiveNames: string[];
  weights: number[];
}

export type RunComparisonCompatibility = "DIRECT" | "CROSS_SOLVER" | "LIMITED" | "NOT_COMPARABLE";

export interface RunComparisonMetric {
  value: number;
  unit: string;
  label: string;
  direction: "LOWER_IS_BETTER" | "HIGHER_IS_BETTER";
  evidence: Record<string, unknown>;
}

export interface RunComparisonRun {
  runId: string;
  taskType: string;
  solver?: string | null;
  loadKind?: string | null;
  modelArtifactId?: string | null;
  modelSha256?: string | null;
  modelIdentity?: string | null;
  loadArtifactId?: string | null;
  loadSha256?: string | null;
  loadIdentity?: string | null;
  responseIds: string[];
  reportArtifactId?: string | null;
  caseId?: string;
  candidateRank?: number;
  metrics: Record<string, RunComparisonMetric>;
}

export interface RunComparisonDelta {
  baseline: number;
  candidate: number;
  difference: number;
  relativeChange: number | null;
  relativeChangePercent: number | null;
  unit: string;
  interpretation: "PERFORMANCE_CHANGE" | "SOLVER_DIFFERENCE";
}

export interface RunComparisonPair {
  baselineRunId: string;
  runId: string;
  compatibility: RunComparisonCompatibility;
  metrics: Record<string, RunComparisonDelta>;
}

export interface RunComparisonResult {
  schemaVersion: "1.0";
  sessionId: string;
  projectId: string | null;
  baselineRunId: string | null;
  compatibility: RunComparisonCompatibility;
  metricIds: string[];
  runs: RunComparisonRun[];
  comparisons: RunComparisonPair[];
  rankings: Array<{
    metricId: string;
    direction: string;
    rows: Array<{ rank: number; runId: string; value: number }>;
  }>;
  warnings: string[];
  interpretationLimit: string;
}

export type AgentMessageStreamEvent''',
)
replace_once(
    'platform-ui/src/api/agentApi.ts',
    "    inquiryTopsisWeights?: InquiryTopsisWeights;\n    queryProgress?: { completed: number; message: string };",
    "    inquiryTopsisWeights?: InquiryTopsisWeights;\n    inquiryRunComparison?: RunComparisonResult;\n    queryProgress?: { completed: number; message: string };",
)

# Render the comparison card in both agent entry surfaces; frontend only renders server facts.
replace_once(
    'platform-ui/src/pages/agent/FullOptimizationPanel.tsx',
    'import { agentApi, type AgentRun } from "../../api/agentApi";\n',
    'import { agentApi, type AgentRun } from "../../api/agentApi";\nimport RunComparisonCard from "./RunComparisonCard";\n',
)
replace_once(
    'platform-ui/src/pages/agent/FullOptimizationPanel.tsx',
    "          {run.resultSummary?.message && <p>{run.resultSummary.message}</p>}\n        </section>",
    "          {run.resultSummary?.message && <p>{run.resultSummary.message}</p>}\n          {run.resultSummary?.inquiryRunComparison && <RunComparisonCard comparison={run.resultSummary.inquiryRunComparison} />}\n        </section>",
)
replace_once(
    'platform-ui/src/pages/agent/AgentWorkbenchPage.tsx',
    'import FullOptimizationPanel from "./FullOptimizationPanel";\n',
    'import FullOptimizationPanel from "./FullOptimizationPanel";\nimport RunComparisonCard from "./RunComparisonCard";\n',
)
replace_once(
    'platform-ui/src/pages/agent/AgentWorkbenchPage.tsx',
    "          <AgentEvidencePanel run={run} />\n          <div style={styles.artifacts}",
    "          <AgentEvidencePanel run={run} />\n          {run.resultSummary?.inquiryRunComparison && <RunComparisonCard comparison={run.resultSummary.inquiryRunComparison} />}\n          <div style={styles.artifacts}",
)

print('PR6 integration patch applied')
