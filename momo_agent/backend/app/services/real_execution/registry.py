from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services.real_execution.contracts import CapabilityCatalog, CapabilityDescriptor


class RealExecutionRegistry:
    """真实执行能力的唯一登记表和创建前门禁。"""

    VERSION = '1.0.0'

    def __init__(self) -> None:
        self._catalog = (
            CapabilityDescriptor(
                jobType='ANALYSIS',
                mode='CONTROLLED_AGENT',
                status='LIVE',
                handler='agent.analysis.real',
                solvers=('ANSYS', 'OPENSEESPY_INPROC'),
                scenarios=('EARTHQUAKE', 'WIND', 'TRAFFIC'),
                solverScenarios={
                    'ANSYS': ('EARTHQUAKE', 'WIND', 'TRAFFIC'),
                    'OPENSEESPY_INPROC': ('EARTHQUAKE', 'WIND', 'TRAFFIC'),
                },
                inputArtifacts=('MODEL', 'LOAD_CASE'),
                outputArtifacts=('CSV_TIMESERIES', 'JSON_SUMMARY', 'OUTPUT_MANIFEST'),
                supportsCancel=True,
                supportsResume=False,
                reason=(
                    '受控智能体真实分析路径已接入；'
                    '风工况在 ANSYS 与 OpenSeesPy 上均为单通道节点力，'
                    '车流工况在两个求解器上都是 163 节点逐节点力矩阵。'
                ),
                unlockRequirements=(),
            ),
            CapabilityDescriptor(
                jobType='DAMPER_COMPARISON',
                mode='CONTROLLED_AGENT',
                status='LIVE',
                handler='agent.damper_comparison.real',
                solvers=('ANSYS', 'OPENSEESPY_INPROC'),
                scenarios=('EARTHQUAKE', 'WIND', 'TRAFFIC'),
                solverScenarios={
                    'ANSYS': ('EARTHQUAKE', 'WIND', 'TRAFFIC'),
                    'OPENSEESPY_INPROC': ('EARTHQUAKE', 'WIND', 'TRAFFIC'),
                },
                inputArtifacts=('MODEL', 'LOAD_CASE'),
                outputArtifacts=('CSV_TIMESERIES', 'JSON_SUMMARY', 'OUTPUT_MANIFEST'),
                supportsCancel=True,
                supportsResume=False,
                reason=(
                    '受控智能体地震、风与车流多案例比选路径已接入 ANSYS 与 OpenSeesPy；'
                    '风工况放行单通道节点力，车流放行 163 节点逐节点矩阵；'
                    '逐型 USER300 标定按求解器各自登记，证据不跨求解器复用。'
                ),
                unlockRequirements=(),
            ),
            CapabilityDescriptor(
                jobType='SOLVER_BATCH',
                mode='CONTROLLED_AGENT',
                status='LIVE',
                handler='solver.batch.controlled_agent',
                solvers=('ANSYS', 'OPENSEESPY_INPROC'),
                scenarios=('EARTHQUAKE', 'WIND', 'TRAFFIC'),
                solverScenarios={
                    'ANSYS': ('EARTHQUAKE', 'WIND', 'TRAFFIC'),
                    'OPENSEESPY_INPROC': ('EARTHQUAKE', 'WIND', 'TRAFFIC'),
                },
                inputArtifacts=('MODEL', 'LOAD_CASE'),
                outputArtifacts=('CSV_TIMESERIES', 'JSON_SUMMARY', 'OUTPUT_MANIFEST'),
                supportsCancel=True,
                supportsResume=False,
                reason=(
                    '仅允许由已审批的 ANALYSIS/COMPARISON/PARAMETER_SWEEP Agent 工作流调用；'
                    '地震、风与车流工况的单次分析、批量参数计算和多案例比选'
                    '在 ANSYS 与 OpenSeesPy 上均已放行。'
                ),
                unlockRequirements=(),
            ),
            CapabilityDescriptor(
                jobType='MULTI_OBJECTIVE_OPTIMIZATION',
                mode='CONTROLLED_AGENT',
                status='LIVE',
                handler='agent.optimization.real_baseline',
                solvers=('ANSYS', 'OPENSEESPY_INPROC'),
                scenarios=('EARTHQUAKE', 'WIND', 'TRAFFIC'),
                # 与真实门 platform_store._is_real_baseline_optimization_request 同口径：
                # 它按 OPTIMIZATION_WORKFLOW_CONFIGS_BY_LOAD_KIND 放行。三个工况都已
                # 登记两个求解器的模板。目录必须逐求解器声明，否则新组合会绕过基线
                # 清单守卫。
                solverScenarios={
                    'ANSYS': ('EARTHQUAKE', 'WIND', 'TRAFFIC'),
                    'OPENSEESPY_INPROC': ('EARTHQUAKE', 'WIND', 'TRAFFIC'),
                },
                inputArtifacts=('MODEL', 'LOAD_CASE'),
                outputArtifacts=('CSV_TABLE', 'JSON_SUMMARY', 'OPTIMIZATION_REPORT', 'OUTPUT_MANIFEST'),
                supportsCancel=True,
                supportsResume=False,
                reason='受控智能体真实基准优化路径已接入；地震、风与车流均登记 ANSYS 与 OpenSeesPy 基准配置。',
                unlockRequirements=(),
            ),
            CapabilityDescriptor(
                jobType='DAMPER_OPTIMIZATION',
                mode='CONTROLLED_AGENT',
                status='LIVE',
                handler='agent.optimization.damper',
                solvers=('ANSYS', 'OPENSEESPY_INPROC'),
                scenarios=('EARTHQUAKE', 'WIND', 'TRAFFIC'),
                # 与 MULTI_OBJECTIVE_OPTIMIZATION 同一条真实执行路径，放行面也相同：
                # 见 damper_optimization.OPTIMIZATION_WORKFLOW_PATHS_BY_LOAD_KIND。
                solverScenarios={
                    'ANSYS': ('EARTHQUAKE', 'WIND', 'TRAFFIC'),
                    'OPENSEESPY_INPROC': ('EARTHQUAKE', 'WIND', 'TRAFFIC'),
                },
                inputArtifacts=('MODEL', 'LOAD_CASE'),
                outputArtifacts=('CSV_TABLE', 'OPTIMIZATION_REPORT', 'OUTPUT_MANIFEST'),
                supportsCancel=True,
                supportsResume=False,
                reason='阻尼器优化通过受控 Agent 基准优化路径执行；地震、风与车流均登记两个求解器。',
                unlockRequirements=(),
            ),
            CapabilityDescriptor(
                jobType='FULL_OPTIMIZATION',
                mode='CONTROLLED_AGENT',
                status='LIVE',
                handler='agent.optimization.full',
                # 真实门 is_supported_full_optimization_intent 只认 ANSYS + EARTHQUAKE，
                # 此前广告 OpenSeesPy 属于宽报：目录说支持、建作业阶段直接判 UNSUPPORTED。
                solvers=('ANSYS',),
                scenarios=('EARTHQUAKE',),
                solverScenarios={'ANSYS': ('EARTHQUAKE',)},
                inputArtifacts=('MODEL', 'LOAD_CASE'),
                outputArtifacts=('CSV_TABLE', 'OPTIMIZATION_REPORT', 'OUTPUT_MANIFEST'),
                supportsCancel=True,
                supportsResume=False,
                reason='全流程优化目前仅放行已验证 ANSYS 地震与运营联合基准链。',
                unlockRequirements=(),
            ),
            CapabilityDescriptor(
                jobType='RESULT_INQUIRY',
                mode='CONTROLLED_AGENT',
                status='LIVE',
                handler='result.inquiry.read_only',
                inputArtifacts=('RESULT_CATALOG',),
                outputArtifacts=('JSON_SUMMARY', 'PLOT'),
                supportsCancel=False,
                supportsResume=False,
                reason='只读结果追问仅消费来源运行白名单中的登记结果制品。',
                unlockRequirements=(),
            ),
            CapabilityDescriptor(
                jobType='SOLVER_BATCH',
                mode='PLATFORM_API',
                status='DISABLED',
                handler='solver.batch',
                solvers=('ANSYS', 'OPENSEESPY_INPROC'),
                scenarios=('EARTHQUAKE', 'WIND', 'TRAFFIC', 'GENERIC_NODAL'),
                inputArtifacts=('MODEL', 'LOAD_CASE'),
                outputArtifacts=('CSV_TIMESERIES', 'OUTPUT_MANIFEST'),
                supportsCancel=False,
                supportsResume=False,
                reason='独立生产批处理尚未通过共享 solver/result 执行器验收。',
                unlockRequirements=('PHASE_1_SOLVER_RESULT_PARITY', 'CANCEL_RESUME_SMOKE'),
            ),
            CapabilityDescriptor(
                jobType='SURROGATE_TRAINING',
                mode='PLATFORM_API',
                status='DISABLED',
                handler='surrogate.training',
                solvers=('PYTHON',),
                scenarios=('EARTHQUAKE', 'WIND', 'TRAFFIC'),
                inputArtifacts=('RAW_DATA', 'RESULT_CATALOG'),
                outputArtifacts=('SURROGATE_MODEL', 'JSON_SUMMARY'),
                supportsCancel=False,
                supportsResume=False,
                reason='真实训练数据集、模型字节和 CV 证据尚未完成验收。',
                unlockRequirements=('PHASE_3_MODEL_RELOAD_REPRODUCE', 'REAL_CV_EVIDENCE'),
            ),
            CapabilityDescriptor(
                jobType='ACTIVE_LEARNING',
                mode='PLATFORM_API',
                status='DISABLED',
                handler='active_learning.infill',
                solvers=('PYTHON',),
                scenarios=('EARTHQUAKE', 'WIND', 'TRAFFIC'),
                inputArtifacts=('SURROGATE_MODEL', 'DOE_DATASET'),
                outputArtifacts=('DOE_DESIGN', 'JSON_SUMMARY'),
                supportsCancel=False,
                supportsResume=False,
                reason='真实 infill 回算、去重和冻结预算验收尚未完成。',
                unlockRequirements=('PHASE_3_REAL_INFILL_SOLVE', 'BUDGET_BOUND_TEST'),
            ),
            CapabilityDescriptor(
                jobType='EXPERIMENT_DESIGN',
                mode='PLATFORM_API',
                status='MOCK_ONLY',
                handler='doe.design',
                solvers=('PYTHON',),
                scenarios=('EARTHQUAKE',),
                inputArtifacts=('MODEL', 'LOAD_CASE'),
                outputArtifacts=('CSV_TABLE', 'RAW_DATA'),
                supportsCancel=False,
                supportsResume=False,
                reason='当前独立入口仍包含 seed/placeholder 后续阶段，只允许演示。',
                unlockRequirements=('PHASE_2_REAL_DATASET',),
            ),
            CapabilityDescriptor(
                jobType='RESULT_EXTRACTION',
                mode='PLATFORM_API',
                status='MOCK_ONLY',
                handler='result.extraction',
                solvers=('ANSYS', 'OPENSEESPY_INPROC'),
                scenarios=('EARTHQUAKE', 'WIND', 'TRAFFIC', 'GENERIC_NODAL'),
                inputArtifacts=('SOLVER_OUTPUT_MANIFEST',),
                outputArtifacts=('RESULT_CATALOG',),
                supportsCancel=False,
                supportsResume=False,
                reason='统一 result catalog 尚未替代全部独立入口的摘要分支。',
                unlockRequirements=('PHASE_1_RESULT_CATALOG',),
            ),
            CapabilityDescriptor(
                jobType='MULTI_OBJECTIVE_OPTIMIZATION',
                mode='PLATFORM_API',
                status='DISABLED',
                handler='optimization.multi_objective',
                solvers=('ANSYS', 'OPENSEESPY_INPROC'),
                scenarios=('EARTHQUAKE', 'WIND', 'TRAFFIC'),
                inputArtifacts=('RESULT_CATALOG', 'SURROGATE_MODEL'),
                outputArtifacts=('OPTIMIZATION_REPORT',),
                supportsCancel=False,
                supportsResume=False,
                reason='独立多目标优化尚未切换到共享真实优化执行器。',
                unlockRequirements=('PHASE_4_OPTIMIZATION_EVIDENCE',),
            ),
            CapabilityDescriptor(
                jobType='ENTROPY_TOPSIS_DECISION',
                mode='PLATFORM_API',
                status='MOCK_ONLY',
                handler='optimization.entropy_topsis',
                solvers=('PYTHON',),
                scenarios=('EARTHQUAKE', 'WIND', 'TRAFFIC'),
                inputArtifacts=('OPTIMIZATION_REPORT',),
                outputArtifacts=('DECISION_REPORT',),
                supportsCancel=False,
                supportsResume=False,
                reason='独立决策入口仍依赖示例摘要，等待真实候选和权重制品。',
                unlockRequirements=('PHASE_4_DECISION_REPLAY',),
            ),
            CapabilityDescriptor(
                jobType='OPTIMIZATION_EXPORT',
                mode='PLATFORM_API',
                status='MOCK_ONLY',
                handler='optimization.export',
                solvers=('PYTHON',),
                scenarios=('EARTHQUAKE', 'WIND', 'TRAFFIC'),
                inputArtifacts=('OPTIMIZATION_REPORT', 'DECISION_REPORT'),
                outputArtifacts=('CSV_TABLE', 'PLOT'),
                supportsCancel=False,
                supportsResume=False,
                reason='导出必须等待真实优化和决策制品，当前仅允许演示。',
                unlockRequirements=('PHASE_4_OPTIMIZATION_EVIDENCE',),
            ),
        )
        keys = [(item.job_type, item.mode) for item in self._catalog]
        if len(keys) != len(set(keys)):
            raise RuntimeError('能力目录中的 jobType/mode 不得重复')

    def catalog(self) -> CapabilityCatalog:
        return CapabilityCatalog(version=self.VERSION, data=self._catalog)

    def all(self) -> tuple[CapabilityDescriptor, ...]:
        return self._catalog

    def resolve(self, job_type: str, params: Mapping[str, Any] | None = None) -> CapabilityDescriptor:
        params = params or {}
        run_mode = params.get('runMode')
        source = str(params.get('source') or params.get('executionSource') or '').upper()
        if job_type in {
            'ANALYSIS',
            'DAMPER_COMPARISON',
            'DAMPER_OPTIMIZATION',
            'FULL_OPTIMIZATION',
            'RESULT_INQUIRY',
        } and source in {'AGENT', 'CONTROLLED_AGENT', 'WORKFLOW_HARNESS'}:
            return self._find(job_type, 'CONTROLLED_AGENT')
        if job_type == 'SOLVER_BATCH' and run_mode in {
            'REAL_AGENT_ANALYSIS',
            'REAL_DAMPER_COMPARISON',
            'REAL_DAMPER_PARAMETER_SWEEP',
        }:
            return self._find(job_type, 'CONTROLLED_AGENT')
        if job_type == 'MULTI_OBJECTIVE_OPTIMIZATION' and run_mode == 'REAL_BASELINE_OPTIMIZATION':
            return self._find(job_type, 'CONTROLLED_AGENT')
        return next(
            (item for item in self._catalog if item.job_type == job_type and item.mode == 'PLATFORM_API'),
            CapabilityDescriptor(
                jobType=job_type,
                mode='PLATFORM_API',
                status='DISABLED',
                handler='unregistered',
                reason='该能力尚未登记真实执行 handler。',
                unlockRequirements=('PHASE_0_CAPABILITY_REGISTRATION',),
            ),
        )

    def is_live(self, job_type: str, params: Mapping[str, Any] | None = None) -> bool:
        return self.resolve(job_type, params).status == 'LIVE'

    def _find(self, job_type: str, mode: str) -> CapabilityDescriptor:
        for item in self._catalog:
            if item.job_type == job_type and item.mode == mode:
                return item
        raise RuntimeError(f'能力目录缺少 {job_type}/{mode} 注册项')


real_execution_registry = RealExecutionRegistry()
