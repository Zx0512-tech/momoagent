from __future__ import annotations

import base64
import csv
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
import json
import logging
import math
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from pyansys_bridge.core.ansys_damper import ANSYS_DAMPER_C_SCALE
from pyansys_bridge.core.opensees_common import OPENSEES_DAMPER_C_SCALE
from pyansys_bridge.core.ansys_load_targets import DEFAULT_STBRIDGE_RANDOM_TRAFFIC_DECK_NODES
from pyansys_bridge.core.progress_sink import (
    clear_progress,
    read_all_progress,
    read_batch_progress,
    refresh_ansys_output_probes,
    write_batch_progress,
)
from pyansys_bridge.core.ansys_load_targets import DEFAULT_STBRIDGE_WIND_GIRDER_NODES
from pyansys_bridge.surrogate.model_zoo import PRODUCTION_SURROGATE_MODEL_NAMES
from pyansys_bridge.optimization.config_runner import preflight_config

from app.api.v1.schemas import (
    Artifact,
    ArtifactKind,
    CaseProgress,
    Job,
    JobError,
    JobProgress,
    JobStatus,
    JobType,
    JobWorker,
)
from app.core.engineering_limits import (
    DOE_ACTIVE_LEARNING_BATCH,
    DOE_ACTIVE_LEARNING_MAX_ITERATIONS,
    DOE_FIXED_REAL_SOLVE_OVERHEAD,
    DOE_INITIAL_DEFAULT,
    DOE_INITIAL_MAX,
    DOE_INITIAL_MIN,
)
from app.core.json_safety import coerce_non_finite_floats, strict_json_dumps
from app.services.agent_engineering import DAMPER_TYPES
from app.services.agent_evidence import build_output_manifest
from app.services.load_import_service import StandardizedLoad, load_import_service
from app.services.platform_repository import SQLitePlatformRepository
from app.services.platform_processes import process_exists
from app.services.real_execution import (
    ConfigExecutionRequest,
    RealSolverExecutor,
    design_set_sha256,
    generate_two_factor_doe,
    real_execution_registry,
)
from app.services.traffic_library import build_existing_traffic_payload, build_random_traffic_payload


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def platform_execution_mode() -> str:
    """返回平台入口模式；生产部署必须显式使用 LIVE。"""

    return str(os.environ.get('MOMO_PLATFORM_MODE', 'MOCK')).strip().upper() or 'MOCK'


def _execute_damper_parameter_sweep_case(
    config_path: Path,
    execution_timeout_s: float | None,
) -> dict[str, Any]:
    """在独立子进程中运行一个 OpenSeesPy 参数扫描算例。"""

    import sys

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from pyansys_bridge.optimization import run_solver_acceptance_case_config

    execution = RealSolverExecutor().execute_config(
        ConfigExecutionRequest(config_path=config_path, timeout_s=execution_timeout_s),
        runner=run_solver_acceptance_case_config,
    )
    return {**execution.payload, 'executionUsage': execution.usage}


STANDALONE_DEMO_JOB_TYPES = {
    'SOLVER_BATCH',
    'RESULT_EXTRACTION',
    'EXPERIMENT_DESIGN',
    'SURROGATE_TRAINING',
    'ACTIVE_LEARNING',
    'MULTI_OBJECTIVE_OPTIMIZATION',
    'ENTROPY_TOPSIS_DECISION',
    'OPTIMIZATION_EXPORT',
}
PLACEHOLDER_MODEL_BYTES = {
    b'surrogate-model-placeholder',
    b'doe-surrogate-model-placeholder',
}


def mock_demo_fields() -> dict[str, Any]:
    return {'executionMode': 'MOCK', 'simulation': True}


# index.csv 是求解器内部索引，不属于用户结果。除它以外，求解输出目录下的 CSV
# 都是可追问结果：结果目录与可追问制品必须用同一判定，否则目录会声明未登记的
# 来源，_load_result_catalog 将其视为篡改并让追问接口 500。
NON_RESULT_CSV_NAMES = {'index.csv'}


def is_inquiry_result_csv(path: Path) -> bool:
    return path.is_file() and path.name.lower() not in NON_RESULT_CSV_NAMES


DEFAULT_STATE_PATH = Path(
    os.environ.get(
        'MOMO_PLATFORM_STATE_PATH',
        Path(__file__).resolve().parents[4] / 'output' / 'platform_store' / 'api_v1_state.sqlite3',
    )
).resolve()
LEGACY_STATE_PATH = DEFAULT_STATE_PATH.with_name('api_v1_state.json')
REPO_ROOT = Path(__file__).resolve().parents[4]
TEMPLATE_ROOT = REPO_ROOT / 'docs' / 'examples' / 'templates'
BASELINE_OPTIMIZATION_WORKFLOW_CONFIGS = {
    'ANSYS': 'ansys_run_joint_baseline_workflow_template.json',
    'OPENSEESPY_INPROC': 'openseespy_inproc_run_joint_baseline_workflow_template.json',
}
# 风工况两个求解器都有已登记的 baseline-first 优化模板，与 AGENT_WIND_ANALYSIS_CONFIGS 一致。
WIND_OPTIMIZATION_WORKFLOW_CONFIGS = {
    'ANSYS': 'ansys_run_wind_baseline_workflow_template.json',
    'OPENSEESPY_INPROC': 'openseespy_inproc_run_wind_baseline_workflow_template.json',
}
# 车流工况两个求解器都有已登记的 baseline-first 优化模板，与 AGENT_TRAFFIC_ANALYSIS_CONFIGS 一致。
TRAFFIC_OPTIMIZATION_WORKFLOW_CONFIGS = {
    'ANSYS': 'ansys_run_traffic_baseline_workflow_template.json',
    'OPENSEESPY_INPROC': 'openseespy_inproc_run_traffic_baseline_workflow_template.json',
}
OPTIMIZATION_WORKFLOW_CONFIGS_BY_LOAD_KIND = {
    'EARTHQUAKE': BASELINE_OPTIMIZATION_WORKFLOW_CONFIGS,
    'WIND': WIND_OPTIMIZATION_WORKFLOW_CONFIGS,
    'TRAFFIC': TRAFFIC_OPTIMIZATION_WORKFLOW_CONFIGS,
}
AGENT_ANALYSIS_CONFIGS = {
    'ANSYS': 'ansys_run_earthquake_baseline_template.json',
    'OPENSEESPY_INPROC': 'openseespy_inproc_run_earthquake_baseline_template.json',
}
AGENT_WIND_ANALYSIS_CONFIGS = {
    'ANSYS': 'ansys_run_wind_baseline_template.json',
    'OPENSEESPY_INPROC': 'openseespy_inproc_run_wind_baseline_template.json',
}
AGENT_TRAFFIC_ANALYSIS_CONFIGS = {
    'ANSYS': 'ansys_run_traffic_baseline_template.json',
    'OPENSEESPY_INPROC': 'openseespy_inproc_run_traffic_baseline_template.json',
}
AGENT_ANALYSIS_CONFIGS_BY_LOAD_KIND = {
    'EARTHQUAKE': AGENT_ANALYSIS_CONFIGS,
    'WIND': AGENT_WIND_ANALYSIS_CONFIGS,
    'TRAFFIC': AGENT_TRAFFIC_ANALYSIS_CONFIGS,
}
# 阻尼器链的放行口径必须独立于单次 ANALYSIS，而且比选与参数扫描各自一张表：
# 三条链除了荷载绑定还要跑阻尼器算例，给 ANALYSIS 登记一个新工况不等于阻尼器链
# 也已验证；比选放行也不等于扫描放行。共用一张表会让新工况在未实现的链上被顺带放行。
AGENT_COMPARISON_CONFIGS_BY_LOAD_KIND = {
    'EARTHQUAKE': AGENT_ANALYSIS_CONFIGS,
    'WIND': AGENT_WIND_ANALYSIS_CONFIGS,
    # 车流比选复用车流 ANALYSIS 基线模板，逐案例覆盖阻尼器参数。
    'TRAFFIC': AGENT_TRAFFIC_ANALYSIS_CONFIGS,
}
AGENT_SWEEP_CONFIGS_BY_LOAD_KIND = {
    'EARTHQUAKE': AGENT_ANALYSIS_CONFIGS,
    'WIND': AGENT_WIND_ANALYSIS_CONFIGS,
    # 车流批量逐案例覆盖阻尼器参数，荷载绑定与单次分析同口径。
    'TRAFFIC': AGENT_TRAFFIC_ANALYSIS_CONFIGS,
}
# 参数扫描的黏滞 C 来自界面工程单位（kN·s/m）；求解器输入采用 N·s/m。
ENGINEERING_VISCOUS_C_SCALE_BY_SOLVER = {
    'ANSYS': ANSYS_DAMPER_C_SCALE,
    'OPENSEESPY_INPROC': OPENSEES_DAMPER_C_SCALE,
}
# ANSYS 的执行命令流是 APDL，OpenSeesPy 的是 Python；制品名按求解器取扩展名。
COMPARISON_COMMAND_STREAM_SUFFIX = {'ANSYS': '.apdl', 'OPENSEESPY_INPROC': '.py'}
# 目标集是审批冻结的施加对象，必须映射到求解器可解析的具体节点，不允许求解侧再猜测默认值。
AGENT_LOAD_TARGET_SETS = {
    'STBRIDGE_WIND_DECK_NODES': list(DEFAULT_STBRIDGE_WIND_GIRDER_NODES),
    # 车流是移动荷载：163 个主梁节点各有独立时程，不能像风那样压成总力再等权分配。
    'STBRIDGE_TRAFFIC_DECK_NODES': list(DEFAULT_STBRIDGE_RANDOM_TRAFFIC_DECK_NODES),
}
# pyansys_bridge 把风荷载固定施加在 +Y（竖向），配置中的 direction 不会被采纳。
# 只放行 UY，避免审批冻结的方向与求解器实际施加方向不一致。
AGENT_WIND_FORCE_COMPONENT = 'UY'
# OpenSees 逐节点 mapping 用自己的 dof 记法（FX/FY/FZ），对应上面的 UY。
OPENSEES_WIND_FORCE_DOF = 'FY'
# 车流竖向轮载与风同轴（UY），符号由源数据自带（向下为负），模板 direction 是 (0,-1,0)。
AGENT_TRAFFIC_FORCE_COMPONENT = 'UY'
OPENSEES_TRAFFIC_FORCE_DOF = 'FY'
EARTHQUAKE_WORKFLOW_OUTPUT_ROOT = REPO_ROOT / 'output' / 'platform_store' / 'real_workflows'
# 求解进度目录必须只由 job_id 决定：写入方在执行子进程里，读取方在 worker
# 进程里，两者不共享内存，也无法把 run_dir 传给对方。环境变量覆盖沿用
# MOMO_PLATFORM_STATE_PATH 的做法，子进程继承后仍然算出同一个根目录。
JOB_PROGRESS_ROOT = Path(
    os.environ.get(
        'MOMO_PLATFORM_PROGRESS_ROOT',
        REPO_ROOT / 'output' / 'platform_store' / 'job_progress',
    )
).resolve()


def job_progress_dir(job_id: str) -> Path:
    """任务求解进度目录，由 job_id 唯一确定。"""

    return JOB_PROGRESS_ROOT / str(job_id)


def collect_case_progress(job: Job) -> JobProgress | None:
    """从文件通道汇总一个 Job 的求解进度，不读取历史 Artifact。"""

    directory = job_progress_dir(job.job_id)
    refresh_ansys_output_probes(directory)
    batch = read_batch_progress(directory)
    cases = read_all_progress(directory)
    if batch is None and not cases:
        return None

    validated_cases = [
        progress
        for case in cases
        if (progress := _validated_case_progress(case)) is not None
    ]
    active = [progress for progress in validated_cases if progress.percent < 100]
    try:
        completed = None if batch is None else max(0, int(batch.get('completedCases') or 0))
        total = None if batch is None else max(0, int(batch.get('totalCases') or 0))
    except (TypeError, ValueError):
        logger.warning('忽略无效的批次进度记录', extra={'event': 'invalid_batch_progress'})
        completed = None
        total = None
    fallback_percent = job.progress.percent if job.progress else None
    percent = _solver_progress_percent(completed, total, active, fallback_percent)
    # 文件可能先写完批次计数，随后才由执行器落库终态；RUNNING 期间不能提前显示 100%。
    if percent is not None and percent >= 100:
        percent = 99
    return JobProgress(
        phase='求解中',
        message=_case_progress_message(completed, total, len(active)),
        percent=percent,
        completedCases=completed,
        totalCases=total,
        activeCases=active,
    )
EARTHQUAKE_SURROGATE_MODEL_NAMES = tuple(name.upper() for name in PRODUCTION_SURROGATE_MODEL_NAMES)
EARTHQUAKE_RESPONSE_TARGETS = (
    {
        'targetId': 'beamEndDisplacement',
        'label': '梁端位移',
        'objective': 'max_girder_end_displacement',
        'fullObjective': 'earthquake:max_girder_end_displacement',
        'sourceUnit': 'm',
        'displayUnit': 'm',
        'displayScale': 1.0,
    },
    {
        'targetId': 'towerBaseShear',
        'label': '塔底剪力',
        'objective': 'max_tower_base_shear',
        'fullObjective': 'earthquake:max_tower_base_shear',
        'sourceUnit': 'N',
        'displayUnit': 'kN',
        'displayScale': 0.001,
    },
    {
        'targetId': 'towerBaseMoment',
        'label': '塔底弯矩',
        'objective': 'max_tower_base_moment',
        'fullObjective': 'earthquake:max_tower_base_moment',
        'sourceUnit': 'N*m',
        'displayUnit': 'kN*m',
        'displayScale': 0.001,
    },
)
OPERATION_RESPONSE_TARGET = {
    'targetId': 'operationCumulativeDisplacement',
    'label': '运营累计位移',
    'objective': 'cumulative_displacement',
    'fullObjective': 'operation:cumulative_displacement',
    'sourceUnit': 'm',
    'displayUnit': 'm',
    'displayScale': 1.0,
}
# 风工况优化的唯一目标：梁端 X 向累计位移最小。风荷载竖向施加，位移与阻尼器
# 同为 X 向，这个方向组合由模板固化，不随工况改变。
#
# 单目标是项目规定的口径，不要"顺手"加塔底内力。已实测过加塔底剪力/弯矩的后果，
# 记录在此以免重复试探：
#   * 塔底内力本身提取得到（objectives_from_timeseries 与 ansys-dpf-rst 都不按
#     工况分支，塔底节点取自 postprocessor 模块默认值），真实风工况求解能拿到
#     非零剪力与弯矩，所以这不是"提取不出来"的问题；
#   * 但竖向风荷载下阻尼器抬高塔底弯矩（实测 4/5 个设计劣于无控基线，最高
#     1.22 倍），把弯矩纳入目标会让"不得劣于无控基线"的约束集与目标集打架，
#     需要额外区分"参与排序"与"硬可行性门"两种角色，凭空增加一层耦合。
# 单目标下 Pareto 前沿塌缩成一点、熵权退化为 [1.0]、TOPSIS closeness 全为 0
# （span 触 1e-12 地板），决策层等价于对唯一目标取 argmin —— 这是数学必然而非
# 缺陷，fem_review.py:78-79 对单目标也有同样的 argmin 短路。
WIND_RESPONSE_TARGETS = (
    {
        'targetId': 'windBeamEndCumulativeDisplacement',
        'label': '梁端累计位移',
        'objective': 'cumulative_displacement',
        'fullObjective': 'wind:cumulative_displacement',
        'sourceUnit': 'm',
        'displayUnit': 'm',
        'displayScale': 1.0,
    },
)
# 车流优化的唯一目标同样是梁端累计位移。用户明确要求不纳入塔底剪力和弯矩：
# 车流是竖向移动荷载，阻尼器沿 X 向工作，剪力/弯矩不是这条链要控的量。
# 取数节点是 1（两个梁端里累计位移更大的一侧，见 momo traffic_base 实测）。
TRAFFIC_RESPONSE_TARGETS = (
    {
        'targetId': 'trafficBeamEndCumulativeDisplacement',
        'label': '梁端累计位移',
        'objective': 'cumulative_displacement',
        'fullObjective': 'traffic:cumulative_displacement',
        'sourceUnit': 'm',
        'displayUnit': 'm',
        'displayScale': 1.0,
    },
)
# 累计位移的取数节点。ANSYS 后处理有 cumulative_displacement_node 选择器，
# OpenSees 的 opensees-csv 没有：它把 response_nodes 全部节点做逐步 max-abs
# 包络后才累加增量。两侧要得到同一个物理量，OpenSees 侧必须把 response_nodes
# 收窄成这一个节点，否则同名目标在两个求解器下定义不同、数值不可比。
WIND_CUMULATIVE_DISPLACEMENT_NODE = 107
# 风工况的"不得劣于无控基线"约束集。单目标下与目标集相同，保留独立常量是因为
# 两者语义不同：目标集决定 Pareto 前沿与熵权，约束集是硬可行性门。地震 ANSYS
# 链两者就不相等（joint 模板 4 个 objective_specs、3 个 baseline limits）。
WIND_BASELINE_LIMIT_TARGETS = WIND_RESPONSE_TARGETS
# 节点 1 与 72 是两个梁端：momo traffic_base 实测累计位移 1.2479 m（节点 1）
# > 1.2419 m（节点 72），优化目标是累计位移，因此取 1。
TRAFFIC_CUMULATIVE_DISPLACEMENT_NODE = 1
# 按荷载类型反查响应目录，避免每处再写一遍 if/else 而各自漂移。
RESPONSE_TARGETS_BY_LOAD_KIND = {
    'EARTHQUAKE': EARTHQUAKE_RESPONSE_TARGETS,
    'WIND': WIND_RESPONSE_TARGETS,
    'TRAFFIC': TRAFFIC_RESPONSE_TARGETS,
}
JOINT_RESPONSE_TARGETS = (*EARTHQUAKE_RESPONSE_TARGETS, OPERATION_RESPONSE_TARGET)
# 概览制品按荷载类型命名：风工况的结论不应写进名为 earthquake 的文件里，
# 否则下游读取方和证据审查会把风的结果当成地震的。
OPTIMIZATION_OVERVIEW_ARTIFACT_NAMES = {
    'EARTHQUAKE': 'real_earthquake_workflow_overview.json',
    'WIND': 'real_wind_workflow_overview.json',
    'TRAFFIC': 'real_traffic_workflow_overview.json',
}
# 无控基线摘要同样按荷载类型命名。风工况模板自己声明的就是 *_undamped_wind_summary.json，
# 而装配阶段此前把两种工况都写成 undamped_earthquake_summary.json，与模板声明不一致。
UNDAMPED_BASELINE_SUMMARY_NAMES = {
    'EARTHQUAKE': 'undamped_earthquake_summary.json',
    'WIND': 'undamped_wind_summary.json',
}
# 各阻尼器类型在两个求解器下的命令流模块名，必须与 agent_engineering.DAMPER_TYPES
# 的 solverModules 逐项一致。只有 VISCOUS 分叉：ANSYS 侧走 USER300 别名，
# OpenSees 侧只登记 damper_viscous（damper_user300_viscous 在 OpenSees 的
# template registry 里没有条目，会解析失败）。
# 比选与批量两个校验器共用这一张表，避免两份副本各自漂移。
DAMPER_SOLVER_MODULES = {
    'VISCOUS': {'ANSYS': 'damper_user300_viscous', 'OPENSEESPY_INPROC': 'damper_viscous'},
    'FRICTION': {'ANSYS': 'damper_friction', 'OPENSEESPY_INPROC': 'damper_friction'},
    'EDDY_CURRENT': {'ANSYS': 'damper_eddy_current', 'OPENSEESPY_INPROC': 'damper_eddy_current'},
}
EARTHQUAKE_DOE_BOUNDS = {'c': (1000.0, 10000.0), 'alpha': (0.3, 1.0)}
EARTHQUAKE_OPTIMIZATION_STEPS = {'c': 100.0, 'alpha': 0.1}
EARTHQUAKE_CANDIDATE_COUNT = 728
EARTHQUAKE_SURROGATE_CV = 10
EARTHQUAKE_MAX_ACTIVE_LEARNING_ITERATIONS = DOE_ACTIVE_LEARNING_MAX_ITERATIONS
EARTHQUAKE_MAX_REVIEW_ITERATIONS = 1
EARTHQUAKE_REVIEW_RELATIVE_ERROR_LIMIT = 0.05
logger = logging.getLogger(__name__)


def _case_progress_message(completed: int | None, total: int | None, active: int) -> str:
    parts: list[str] = []
    if total:
        parts.append(f'已完成 {completed or 0}/{total} 个算例')
    if active:
        parts.append(f'{active} 个正在求解')
    return '，'.join(parts) or 'worker 心跳正常'


def _validated_case_progress(payload: dict[str, Any]) -> CaseProgress | None:
    """校验观测文件；无效信号只告警，绝不能中断真实求解。"""

    try:
        progress = CaseProgress.model_validate(payload)
    except (TypeError, ValueError):
        logger.warning('忽略无效的算例进度记录', extra={'event': 'invalid_case_progress'})
        return None
    if not 0 <= progress.percent <= 100:
        logger.warning('忽略越界的算例进度记录', extra={'event': 'invalid_case_progress'})
        return None
    if progress.step is not None and progress.step < 0:
        return None
    if progress.total_steps is not None and progress.total_steps < 0:
        return None
    return progress


def _solver_progress_percent(
    completed: int | None,
    total: int | None,
    active: list[CaseProgress],
    fallback: int | None,
) -> int | None:
    """把批次完成数和进行中算例合成为用户可见的真实求解百分比。"""

    if total is not None and total > 0:
        completed_fraction = float(max(completed or 0, 0))
        active_fraction = sum(case.percent / 100.0 for case in active)
        # 只要仍有活动算例，任务就不能显示 100%；100% 只代表所有算例均已结束。
        ceiling = 99 if active else 100
        return max(0, min(ceiling, int(round((completed_fraction + active_fraction) / total * 100))))
    if active:
        return max(0, min(99, int(round(sum(case.percent for case in active) / len(active)))))
    return fallback


def gen_id(prefix: str) -> str:
    return f'{prefix}_{uuid4().hex[:12]}'


def _real_training_rows(
    doe_records: list[dict[str, Any]],
    active_learning_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sample_source, records in (
        ('INITIAL_DOE', doe_records),
        ('ACTIVE_LEARNING', active_learning_records),
    ):
        for record in records:
            rows.append({
                'designIndex': len(rows),
                'sampleSource': sample_source,
                'design': record.get('design'),
                'designParameters': record.get('design_parameters') or {},
                'sourceCaseIds': [
                    str(result.get('case_id'))
                    for result in (record.get('analysis_results') or [])
                    if isinstance(result, dict) and result.get('case_id')
                ],
                'analysisResults': record.get('analysis_results') or [],
            })
    return rows


@dataclass
class ArtifactRecord:
    artifact: Artifact
    preview: Any
    content: bytes


@dataclass(frozen=True)
class PreparedEarthquakeWorkflow:
    workflow_config_path: Path
    baseline_config_path: Path
    optimization_config_path: Path
    source_workflow_config_path: Path
    output_dir: Path
    doe_designs: list[dict[str, float]]
    requested_doe_count: int
    doe_design_sha256: str
    solver: str
    solver_parallel: dict[str, Any]


class PlatformStore:
    def __init__(
        self,
        state_path: Path | None = None,
        *,
        recover_orphans: bool = True,
        real_workflow_root: Path | None = None,
    ) -> None:
        self._state_lock = RLock()
        self._inline_execution_count = 0
        self.state_path = state_path or DEFAULT_STATE_PATH
        self.real_workflow_root = real_workflow_root or EARTHQUAKE_WORKFLOW_OUTPUT_ROOT
        self._real_executor = RealSolverExecutor()
        self.repository = SQLitePlatformRepository(self.state_path)
        self.jobs: list[Job] = []
        self.artifacts: list[ArtifactRecord] = []
        self.engineering_config: dict[str, Any] | None = None
        self._loaded_engineering_config: dict[str, Any] | None = None
        loaded = self._load_state()
        if not loaded and self.state_path == DEFAULT_STATE_PATH:
            loaded = self._load_legacy_state()
        if not loaded:
            self._seed_defaults()
            self.persist()
        elif recover_orphans:
            self._fail_orphaned_running_jobs()

    def refresh(self, *, recover_orphans: bool = False) -> None:
        with self._state_lock:
            # 内联 handler 在锁外执行，但仍会向当前内存快照登记制品；重载会丢掉
            # 尚未最终持久化的变更，因此这段时间只读当前快照。
            if self._inline_execution_count:
                return
            # 版本戳未变化时跳过全量重载：_load_state 会把所有制品二进制
            # 重新读入内存并重建全部 Job 模型，前端每次轮询都触发，代价高。
            # 标记挂在 repository 实例上，测试替换仓储时随之切换。
            if not recover_orphans:
                current = self.repository.state_revision()
                loaded = getattr(self.repository, '_last_loaded_revision', None)
                if current is not None and loaded is not None and current == loaded:
                    return
            self._load_state()
            if recover_orphans:
                self._fail_orphaned_running_jobs()

    @contextmanager
    def state_transaction(self) -> Iterator[None]:
        """串行化需要跨 refresh、制品登记和读取的内存状态事务。"""

        with self._state_lock:
            yield

    def _seed_defaults(self) -> None:
        gate = self._register_artifact(
            kind='STATUS_REPORT',
            name='project_optimization_gates_live.md',
            path='docs/status_reports/project_optimization_gates_live.md',
            mime_type='text/markdown',
            preview='# 平台后端状态\n\n/api/v1 已启用；FEM Job 使用 SQLite 持久化队列。',
        )
        parity = self._register_artifact(
            kind='PARITY_REPORT',
            name='baseline_parity_report.json',
            path='output/run_templates/solver_parity/baseline_parity_report.json',
            mime_type='application/json',
            preview=self.latest_parity_report(),
        )
        self.jobs.extend([
            Job(
                jobId='job_live_gate_001',
                type='PROJECT_GATE_FAST',
                status='SUCCEEDED',
                title='快速回归门槛测试 (Regression Gate)',
                createdAt=utc_now(),
                startedAt=utc_now(),
                finishedAt=utc_now(),
                request={},
                artifacts=[gate],
            ),
            Job(
                jobId='job_live_parity_001',
                type='SOLVER_PARITY',
                status='SUCCEEDED',
                title='求解器一致性对齐测试 (Baseline Solver Parity)',
                createdAt=utc_now(),
                startedAt=utc_now(),
                finishedAt=utc_now(),
                request={'configPath': 'docs/examples/templates/solver_parity_baseline_workflow_template.json'},
                artifacts=[parity],
            ),
        ])

    def dashboard_summary(self) -> dict[str, Any]:
        latest_finished_at = self.jobs[0].finished_at if self.jobs else None
        if platform_execution_mode() == 'LIVE':
            latest_gate_job = next(
                (job for job in self.jobs if job.type == 'PROJECT_GATE_FAST'),
                None,
            )
            gate_status = (
                'PASS' if latest_gate_job and latest_gate_job.status == 'SUCCEEDED'
                else 'FAIL' if latest_gate_job and latest_gate_job.status == 'FAILED'
                else 'UNKNOWN'
            )
            return {
                'repo': {
                    'status': 'UNKNOWN',
                    'branch': None,
                    'isClean': None,
                    'lastCommit': None,
                },
                'latestGate': {
                    'status': gate_status,
                    'summaryPath': None,
                    'finishedAt': latest_gate_job.finished_at if latest_gate_job else None,
                },
                'latestParity': {
                    'status': 'UNKNOWN',
                    'reportPath': None,
                    'maxRelativeError': None,
                },
                'runtime': {
                    'status': 'UNKNOWN',
                    'ansysAvailable': None,
                    'openseespyInprocAvailable': None,
                    'user300PatchPackage': None,
                },
            }
        return {
            'repo': {'branch': 'main', 'isClean': True, 'lastCommit': 'live-api'},
            'latestGate': {
                'status': 'PASS',
                'summaryPath': 'docs/status_reports/project_optimization_gates_live.md',
                'finishedAt': latest_finished_at,
            },
            'latestParity': {
                'status': 'FAIL',
                'reportPath': 'output/run_templates/solver_parity/baseline_parity_report.json',
                'maxRelativeError': 0.1835,
            },
            'runtime': {
                'ansysAvailable': False,
                'openseespyInprocAvailable': False,
                'user300PatchPackage': 'MOCK_HANDLER',
            },
        }

    def list_jobs(self, status: JobStatus | None, job_type: JobType | None, page: int, page_size: int) -> dict[str, Any]:
        jobs = self.jobs
        if status:
            jobs = [job for job in jobs if job.status == status]
        if job_type:
            jobs = [job for job in jobs if job.type == job_type]
        return self._paginate(jobs, page, page_size)

    def get_job(self, job_id: str) -> Job:
        for job in self.jobs:
            if job.job_id == job_id:
                return job
        raise HTTPException(status_code=404, detail={'code': 'NOT_FOUND', 'message': f'任务 {job_id} 不存在'})

    def create_job(self, job_type: JobType, params: dict[str, Any]) -> Job:
        inline_started = False
        with self.state_transaction():
            job = self._create_job_locked(job_type, params, execute_inline=False)
            if job.status == 'RUNNING':
                self._inline_execution_count += 1
                inline_started = True
        try:
            return self._execute_job(job) if job.status == 'RUNNING' else job
        finally:
            if inline_started:
                with self._state_lock:
                    self._inline_execution_count = max(0, self._inline_execution_count - 1)

    def _create_job_locked(
        self,
        job_type: JobType,
        params: dict[str, Any],
        *,
        execute_inline: bool = True,
    ) -> Job:
        self._reject_unimplemented_standalone_capability(job_type, params)
        if params.get('inputArtifactId'):
            self._standardized_uploaded_load(job_type, params)
        if job_type == 'LOAD_TRAFFIC_RANDOM':
            self._validate_traffic_source(params)
        if job_type == 'LOAD_CURVE_EXPORT':
            self._validate_load_curve_source(params)
        if job_type == 'SURROGATE_TRAINING':
            self._validate_surrogate_training_source(params)
        if params.get('runMode') == 'REAL_BASELINE_OPTIMIZATION' and not self._is_real_baseline_optimization_request(job_type, params):
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'UNSUPPORTED_REAL_WORKFLOW_REQUEST',
                    'message': (
                        'REAL_BASELINE_OPTIMIZATION 仅支持已登记基准模板的 OPTIMIZATION_DECISION 任务：'
                        '地震、风、车流三个工况都放行 ANSYS 与 OPENSEESPY_INPROC。'
                    ),
                },
            )
        if params.get('runMode') == 'REAL_DAMPER_COMPARISON' and not self._is_real_damper_comparison_request(job_type, params):
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'UNSUPPORTED_REAL_COMPARISON_REQUEST',
                    'message': 'REAL_DAMPER_COMPARISON 仅支持 ANSYS 地震或风工况的多工况 SOLVER_BATCH',
                },
            )
        if params.get('runMode') == 'REAL_DAMPER_PARAMETER_SWEEP' and not self._is_real_damper_parameter_sweep_request(job_type, params):
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'UNSUPPORTED_REAL_PARAMETER_SWEEP_REQUEST',
                    'message': (
                        'REAL_DAMPER_PARAMETER_SWEEP 仅支持已登记批量模板的受控 SOLVER_BATCH：'
                        '地震与风工况均放行 ANSYS 与 OPENSEESPY_INPROC，案例数 1–64。'
                    ),
                },
            )
        if self._is_real_baseline_optimization_request(job_type, params):
            self._baseline_optimization_workflow_config_path(params)
        if self._is_real_damper_comparison_request(job_type, params):
            self._validate_real_damper_comparison_params(params)
        if self._is_real_damper_parameter_sweep_request(job_type, params):
            self._validate_real_damper_parameter_sweep_params(params)
        now = utc_now()
        queued = self._should_queue_job(job_type, params)
        job = Job(
            jobId=gen_id('job'),
            type=job_type,
            status='QUEUED' if queued else 'RUNNING',
            title=self._job_title(job_type, params),
            createdAt=now,
            startedAt=None if queued else now,
            request=params,
            artifacts=[],
            progress=JobProgress(
                phase='等待执行' if queued else '执行中',
                message='任务已持久化，等待独立工作进程' if queued else '本地 handler 正在生成平台制品',
                percent=0 if queued else 30,
            ),
            queueVersion=1 if queued else None,
        )
        self.jobs.insert(0, job)
        if queued:
            self.persist()
            return job
        self.persist()
        return self._execute_job(job) if execute_inline else job

    def execute_queued_job(self, job_id: str) -> Job:
        job = self.get_job(job_id)
        if job.status != 'QUEUED':
            raise ValueError(f'任务 {job_id} 当前状态 {job.status}，不能开始执行')
        job.status = 'RUNNING'
        job.started_at = utc_now()
        job.progress = JobProgress(phase='执行中', message='独立工作进程正在生成平台制品', percent=30)
        self.persist()
        return self._execute_job(job)

    def execute_running_job(self, job_id: str) -> Job:
        job = self.get_job(job_id)
        if job.status != 'RUNNING':
            raise ValueError(f'任务 {job_id} 当前状态 {job.status}，不能由 worker 执行')
        return self._execute_job(job)

    def mark_job_running(self, job_id: str, *, pid: int) -> Job:
        self.refresh()
        job = self.get_job(job_id)
        if job.status != 'QUEUED':
            raise ValueError(f'任务 {job_id} 当前状态 {job.status}，不能启动 worker')
        now = utc_now()
        job.status = 'RUNNING'
        job.started_at = now
        job.progress = JobProgress(phase='启动 worker', message='独立工作进程已启动', percent=1)
        job.worker = JobWorker(pid=pid, heartbeatAt=now)
        if not self.repository.update_job(
            job.model_dump(by_alias=True, mode='json'), expected_status='QUEUED'
        ):
            raise ValueError(f'任务 {job_id} 已被其他进程改写，不能启动 worker')
        return job

    def record_worker_heartbeat(self, job_id: str, *, phase: str = '执行中') -> Job:
        self.refresh()
        job = self.get_job(job_id)
        if job.status != 'RUNNING' or job.worker is None:
            return job
        job.worker.heartbeat_at = utc_now()
        if job.progress is None:
            job.progress = JobProgress(phase=phase, message='worker 心跳正常', percent=None)
        else:
            job.progress = job.progress.model_copy(
                update={'phase': phase, 'message': 'worker 心跳正常'}
            )
        return self._update_running_job(job)

    def record_worker_observation(self, job_id: str, *, phase: str = '执行中') -> Job:
        """一次条件更新同时持久化 worker 心跳和可用的完整求解进度。"""

        self.refresh()
        job = self.get_job(job_id)
        if job.status != 'RUNNING' or job.worker is None:
            return job
        job.worker.heartbeat_at = utc_now()
        try:
            progress = self._collect_case_progress(job)
        except Exception:
            logger.exception(
                '求解进度汇总失败，保留上一帧并继续记录心跳',
                extra={
                    'event': 'job_progress_collection_failed',
                    'job_id': job_id,
                    'worker_pid': job.worker.pid,
                },
            )
            progress = None
        if progress is not None:
            job.progress = progress
        elif job.progress is None:
            job.progress = JobProgress(phase=phase, message='worker 心跳正常', percent=None)
        else:
            job.progress = job.progress.model_copy(
                update={'phase': phase, 'message': 'worker 心跳正常'}
            )
        return self._update_running_job(job)

    @staticmethod
    def _begin_job_progress(job_id: str | None, *, total_cases: int | None = None) -> Path | None:
        """为一次真实求解开启进度通道，返回写入目录；没有 job_id 时整体关闭。

        进度目录只由 job_id 决定，写入方（求解子进程）与读取方（worker 心跳）
        因此无需互相传参。``total_cases`` 给出时先写一条 0/N 的完成计数，
        让前端在第一个算例出结果之前就能显示"已完成 0/N"，而不是空白。
        """

        if job_id is None:
            return None
        directory = job_progress_dir(job_id)
        clear_progress(directory)
        if total_cases:
            write_batch_progress(directory, completed=0, total=total_cases)
        return directory

    def record_case_progress(self, job_id: str) -> Job:
        """把求解子进程落盘的 case 进度汇总进任务状态。

        由 worker 的心跳循环调用。没有任何进度文件时保持原状，不覆盖
        `_execute_job` 写入的阶段百分比。
        """

        self.refresh()
        job = self.get_job(job_id)
        if job.status != 'RUNNING':
            return job
        progress = self._collect_case_progress(job)
        if progress is None:
            return job
        job.progress = progress
        return self._update_running_job(job)

    def _collect_case_progress(self, job: Job) -> JobProgress | None:
        """读取并校验进度通道；没有有效新帧时返回 ``None``。"""

        return collect_case_progress(job)

    def _update_running_job(self, job: Job) -> Job:
        """只在数据库里仍是 RUNNING 时写回，否则放弃并返回已落库的终态。

        心跳与进度汇总跑在 worker 进程，终态由执行子进程写入；两者都持有
        自己的 Job 副本。盲写会把已经写好的 SUCCEEDED 覆盖成 RUNNING，
        worker 随后误判为 EXECUTOR_INCOMPLETE，把成功的任务标成失败。
        """

        written = self.repository.update_job(
            job.model_dump(by_alias=True, mode='json'), expected_status='RUNNING'
        )
        if written:
            return job
        latest = self.repository.load_job(job.job_id)
        return Job.model_validate(latest) if latest else job

    def record_worker_exit(self, job_id: str, *, exit_code: int) -> Job:
        """登记 worker 退出码。状态由别人负责，这里只在状态没变时写。

        一次重试即可：状态机是单向的（RUNNING → 终态），重试后读到的状态不会
        再被 worker 侧改写。
        """

        for _ in range(2):
            self.refresh()
            job = self.get_job(job_id)
            if job.worker is None:
                return job
            observed_status = job.status
            job.worker.exit_code = exit_code
            if self.repository.update_job(
                job.model_dump(by_alias=True, mode='json'), expected_status=observed_status
            ):
                return job
        latest = self.repository.load_job(job_id)
        return Job.model_validate(latest) if latest else self.get_job(job_id)

    def fail_running_job(self, job_id: str, *, code: str, message: str, exit_code: int | None = None) -> Job:
        self.refresh()
        job = self.get_job(job_id)
        if job.status not in ('QUEUED', 'RUNNING'):
            return job
        observed_status = job.status
        job.status = 'FAILED'
        job.finished_at = utc_now()
        job.error = JobError(code=code, message=message)
        job.progress = JobProgress(phase='执行失败', message=message, percent=None)
        if job.worker is not None:
            job.worker.exit_code = exit_code
        # 执行子进程可能在上面这次读取之后刚写入终态，条件更新让落后的一方放弃，
        # 不把已经成功的任务改判为失败。
        written = self.repository.update_job(
            job.model_dump(by_alias=True, mode='json'), expected_status=observed_status
        )
        if written:
            return job
        latest = self.repository.load_job(job_id)
        return Job.model_validate(latest) if latest else job

    def fail_legacy_queued_jobs(self) -> None:
        self.refresh()
        legacy_job_ids = [
            job.job_id
            for job in self.jobs
            if job.status == 'QUEUED' and job.queue_version != 1
        ]
        for job_id in legacy_job_ids:
            self.fail_running_job(
                job_id,
                code='QUEUE_VERSION_UNSUPPORTED',
                message='任务创建于独立 worker 协议启用之前，已阻止自动执行；请重新提交',
            )

    def _execute_job(self, job: Job) -> Job:
        try:
            artifacts = self._generate_artifacts(job.type, job.request, job_id=job.job_id)
            latest_payload = self.repository.load_job(job.job_id)
            latest = Job.model_validate(latest_payload) if latest_payload else job
            if latest.status == 'CANCELLED':
                return latest
            job.worker = latest.worker
            job.status = 'SUCCEEDED'
            job.finished_at = utc_now()
            job.progress = JobProgress(phase='已完成', message='任务完成并登记制品', percent=100)
            job.artifacts = artifacts
            self._label_standalone_mock_artifacts(job.type, job.request, artifacts)
            job.result = self._mark_standalone_mock_demo(
                job.type,
                job.request,
                self._job_result(job.type, job.request, artifacts),
            )
        except Exception as exc:  # pragma: no cover - defensive boundary
            job.status = 'FAILED'
            job.finished_at = utc_now()
            job.error = JobError(code='HANDLER_ERROR', message=str(exc))
        self.persist()
        return job

    def cancel_job(self, job_id: str) -> dict[str, bool]:
        job = self.get_job(job_id)
        if job.status in ('SUCCEEDED', 'FAILED', 'CANCELLED'):
            return {'success': False}
        job.status = 'CANCELLED'
        job.finished_at = utc_now()
        self.persist()
        return {'success': True}

    def list_artifacts(
        self,
        job_id: str | None,
        kind: str | None,
        page: int,
        page_size: int,
        *,
        source: str | None = None,
    ) -> dict[str, Any]:
        if job_id:
            artifacts = self.get_job(job_id).artifacts
        else:
            artifacts = [record.artifact for record in self.artifacts]
        if kind:
            artifacts = [artifact for artifact in artifacts if artifact.kind == kind]
        if source:
            artifacts = [artifact for artifact in artifacts if artifact.source == source]
        return self._paginate(artifacts, page, page_size)

    def sync_real_optimization_history(self) -> dict[str, Any]:
        root = self.real_workflow_root
        stats: dict[str, Any] = {
            'scannedRuns': 0,
            'importedRuns': 0,
            'importedArtifacts': 0,
            'updatedArtifacts': 0,
            'existingArtifacts': 0,
            'skippedRuns': 0,
            'runs': [],
        }
        if not root.exists():
            return stats

        run_pattern = re.compile(r'^(?:ansys|openseespy_inproc)_earthquake_[0-9a-f]{12}$')
        changed = False
        for run_dir in sorted(path for path in root.iterdir() if path.is_dir() and run_pattern.fullmatch(path.name)):
            stats['scannedRuns'] += 1
            required_paths = [
                run_dir / 'workflow_summary.json',
                run_dir / 'optimization_summary.json',
                run_dir / 'undamped_earthquake_summary.json',
            ]
            try:
                if not all(path.is_file() for path in required_paths):
                    raise ValueError('incomplete')
                workflow, optimization, baseline = [
                    json.loads(path.read_text(encoding='utf-8')) for path in required_paths
                ]
                optimization_completed = (
                    workflow.get('optimization_status') == 'completed'
                    or optimization.get('status') == 'completed'
                    or (optimization.get('review_status') or {}).get('all_verified_execution') is True
                )
                if workflow.get('baseline_status') != 'completed' or not optimization_completed:
                    raise ValueError('incomplete')
                if any(self._contains_dry_run(payload) for payload in (workflow, optimization, baseline)):
                    raise ValueError('dry_run')
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                stats['skippedRuns'] += 1
                continue

            imported_for_run = 0
            for file_path in sorted((*run_dir.glob('*.json'), *run_dir.glob('*.csv'))):
                raw = file_path.read_bytes()
                artifact_path = self._artifact_display_path(file_path)
                preview = self._real_history_preview(file_path, raw)
                kind = self._real_history_kind(file_path.name)
                mime_type = 'application/json' if file_path.suffix.lower() == '.json' else 'text/csv'
                existing = next(
                    (record for record in self.artifacts if record.artifact.path == artifact_path),
                    None,
                )
                if existing is None:
                    artifact_key = sha256(artifact_path.encode('utf-8')).hexdigest()[:16]
                    self._register_artifact(
                        artifact_id=f'art_realopt_{artifact_key}',
                        kind=kind,
                        name=file_path.name,
                        path=artifact_path,
                        mime_type=mime_type,
                        preview=preview,
                        content=raw,
                        source='REAL_OPTIMIZATION_HISTORY',
                        run_id=run_dir.name,
                        created_at=datetime.fromtimestamp(file_path.stat().st_mtime, UTC).isoformat(),
                    )
                    stats['importedArtifacts'] += 1
                    imported_for_run += 1
                    changed = True
                    continue

                digest = sha256(raw).hexdigest()
                artifact_changed = False
                if existing.artifact.source != 'REAL_OPTIMIZATION_HISTORY':
                    existing.artifact.source = 'REAL_OPTIMIZATION_HISTORY'
                    artifact_changed = True
                if existing.artifact.run_id != run_dir.name:
                    existing.artifact.run_id = run_dir.name
                    artifact_changed = True
                if existing.artifact.created_at is None:
                    existing.artifact.created_at = datetime.fromtimestamp(file_path.stat().st_mtime, UTC).isoformat()
                    artifact_changed = True
                if existing.artifact.kind != kind:
                    existing.artifact.kind = kind
                    artifact_changed = True
                if existing.artifact.mime_type != mime_type:
                    existing.artifact.mime_type = mime_type
                    artifact_changed = True
                if existing.artifact.sha256 != digest:
                    existing.artifact.sha256 = digest
                    existing.artifact.size_bytes = len(raw)
                    existing.content = raw
                    existing.preview = preview
                    artifact_changed = True
                if artifact_changed:
                    stats['updatedArtifacts'] += 1
                    changed = True
                else:
                    stats['existingArtifacts'] += 1

            if imported_for_run:
                stats['importedRuns'] += 1
            stats['runs'].append(run_dir.name)

        if changed:
            self.persist()
        return stats

    def get_artifact(self, artifact_id: str) -> ArtifactRecord:
        for record in self.artifacts:
            if record.artifact.artifact_id == artifact_id:
                return record
        raise HTTPException(status_code=404, detail={'code': 'NOT_FOUND', 'message': f'制品 {artifact_id} 不存在'})

    def find_artifact_for_run(self, run_id: str, name: str) -> ArtifactRecord | None:
        """按运行和精确名称查找已登记制品，用于幂等恢复。"""

        with self._state_lock:
            return next(
                (
                    record for record in self.artifacts
                    if record.artifact.run_id == run_id and record.artifact.name == name
                ),
                None,
            )

    def save_engineering_config(self, config: dict[str, Any]) -> dict[str, Any]:
        self.engineering_config = config
        self.persist()
        return config

    def get_engineering_config(self) -> dict[str, Any] | None:
        return self.engineering_config

    def validate_engineering_config(self, config: dict[str, Any]) -> dict[str, Any]:
        return {'moduleStatus': self._module_status(config)}

    def damper_registry(self) -> list[dict[str, Any]]:
        config = self.engineering_config or {}
        return (((config.get('damperBaseConfig') or {}).get('damperInstanceRegistry')) or self._default_registry())

    def default_result_metrics(self) -> list[dict[str, Any]]:
        config = self.engineering_config or {}
        result_config = config.get('resultExtractionConfig') or {}
        return result_config.get('structuralMetrics') or self._default_metrics()

    def templates(self) -> list[dict[str, Any]]:
        return [
            {
                'templateId': 'ansys_joint_baseline',
                'name': 'ANSYS Joint Baseline 核心工作流模板',
                'solver': 'ANSYS',
                'workflow': 'BASELINE',
                'path': 'docs/examples/templates/ansys_run_joint_baseline_workflow_template.json',
                'isLegacy': False,
            },
            {
                'templateId': 'solver_parity_baseline',
                'name': '双求解器一致性对齐测试',
                'solver': 'ANSYS',
                'workflow': 'SOLVER_PARITY',
                'path': 'docs/examples/templates/solver_parity_baseline_workflow_template.json',
                'isLegacy': False,
            },
        ]

    def preflight(self, config_path: str) -> dict[str, Any]:
        requested_path = Path(config_path)
        resolved_path = requested_path.resolve() if requested_path.is_absolute() else (REPO_ROOT / requested_path).resolve()
        try:
            raw = preflight_config(resolved_path)
        except FileNotFoundError:
            return self._failed_preflight(config_path, resolved_path, 'CONFIG_NOT_FOUND', '预检配置或其引用文件不存在')
        except json.JSONDecodeError:
            return self._failed_preflight(config_path, resolved_path, 'INVALID_CONFIG_JSON', '预检配置不是合法 JSON')
        except (KeyError, TypeError, ValueError):
            return self._failed_preflight(config_path, resolved_path, 'INVALID_CONFIG_SCHEMA', '预检配置字段或求解器配置不合法')
        except Exception:
            return self._failed_preflight(config_path, resolved_path, 'PREFLIGHT_FAILED', '预检执行失败')

        raw_checks = raw.get('path_checks') or {}
        path_checks = [
            {'name': name, **dict(check)}
            for name, check in raw_checks.items()
            if isinstance(check, dict)
        ]
        reasons = [
            {
                'code': 'REQUIRED_PATH_UNAVAILABLE',
                'message': f'预检所需路径不可用：{check["name"]}',
                'path': check.get('path'),
            }
            for check in path_checks
            if check.get('exists') is not True
        ]
        solver_capability = raw.get('solver_capability') or {}
        if isinstance(solver_capability, dict) and solver_capability.get('available') is False:
            reasons.insert(0, {
                'code': 'SOLVER_UNAVAILABLE',
                'message': f'配置声明的求解器不可用：{raw.get("solver") or "UNKNOWN"}',
            })
        return {
            'status': 'FAIL' if reasons else 'PASS',
            'configPath': config_path,
            'solver': raw.get('solver'),
            'executionMode': raw.get('execution_mode'),
            'pathChecks': path_checks,
            'reasons': reasons,
            'raw': raw,
        }

    @staticmethod
    def _failed_preflight(
        config_path: str,
        resolved_path: Path,
        code: str,
        message: str,
    ) -> dict[str, Any]:
        return {
            'status': 'FAIL',
            'configPath': config_path,
            'solver': None,
            'executionMode': None,
            'pathChecks': [{'name': 'config', 'path': str(resolved_path), 'exists': resolved_path.exists()}],
            'reasons': [{'code': code, 'message': message}],
            'raw': {},
        }

    def latest_parity_report(self) -> dict[str, Any]:
        return {
            'status': 'FAIL',
            'reportPath': 'output/run_templates/solver_parity/baseline_parity_report.json',
            'referenceSolver': 'ANSYS/MAPDL',
            'candidateSolver': 'OpenSeesPy',
            'relativeTolerance': 0.1,
            'absoluteTolerance': 0,
            'metrics': [
                {'name': 'beamEndDisplacement', 'label': '梁端位移', 'relativeError': 0.021, 'accepted': True},
                {'name': 'towerBaseShear', 'label': '塔底剪力', 'relativeError': 0.03, 'accepted': True},
                {'name': 'towerBaseMoment', 'label': '塔底弯矩', 'relativeError': 0.1835, 'accepted': False},
            ],
            'artifacts': [],
        }

    def topsis_result(self) -> dict[str, Any]:
        return {
            'candidates': [
                {
                    'id': 'c1',
                    'params': {'alpha': 0.4, 'beta': 0.6},
                    'objectives': {
                        'beamEndDisplacement': 0.0784,
                        'towerBaseShear': 5.23e7,
                        'towerBaseMoment': 2.74e9,
                        'beamEndCumulativeDisplacement': 18.4,
                        'damperCost': 720000,
                    },
                    'constraintsPassed': True,
                    'femReviewed': True,
                    'topsisScore': 0.847,
                    'rank': 1,
                }
            ],
            'entropyWeights': {
                'beamEndDisplacement': 0.28,
                'towerBaseShear': 0.23,
                'towerBaseMoment': 0.26,
                'beamEndCumulativeDisplacement': 0.23,
            },
            'recommendation': {'candidateId': 'c1', 'explanation': '本地同步 handler 推荐的示例方案。'},
        }

    def real_topsis_result(self, optimization_run_id: str) -> dict[str, Any] | None:
        """从已完成的真实优化报告回放 TOPSIS，不生成示例候选。"""

        job = next((item for item in self.jobs if item.job_id == optimization_run_id), None)
        if job is None:
            return None
        if job.status != 'SUCCEEDED':
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'OPTIMIZATION_NOT_COMPLETE',
                    'message': '真实优化尚未成功完成，不能读取 TOPSIS 结果。',
                },
            )
        job_result = job.result or {}
        if job_result.get('mode') != 'real_baseline_optimization':
            return None
        if job_result.get('finalRecommendationStatus') != 'ACCEPTED':
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'OPTIMIZATION_NOT_ACCEPTED',
                    'message': '真实优化的 FEM review 未通过，不能生成成功推荐。',
                },
            )
        artifact_id = str(job_result.get('optimizationSummaryArtifactId') or '')
        if not artifact_id:
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'OPTIMIZATION_REPORT_MISSING',
                    'message': '真实优化缺少 optimization summary 制品。',
                },
            )
        attached_artifact_ids = {
            item.artifact_id
            for item in (job.artifacts or [])
            if hasattr(item, 'artifact_id')
        }
        if artifact_id not in attached_artifact_ids:
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'OPTIMIZATION_REPORT_SCOPE_INVALID',
                    'message': 'optimization summary 制品未挂载到当前优化 Job。',
                },
            )
        try:
            record = self.get_artifact(artifact_id)
        except HTTPException as exc:
            if exc.status_code == 404:
                raise HTTPException(
                    status_code=422,
                    detail={
                        'code': 'OPTIMIZATION_REPORT_MISSING',
                        'message': '真实优化引用的 optimization summary 制品不存在。',
                    },
                ) from exc
            raise
        if record.artifact.kind not in {'JSON_SUMMARY', 'OPTIMIZATION_REPORT'}:
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'OPTIMIZATION_REPORT_INVALID',
                    'message': 'optimization summary 制品类型无效。',
                },
            )
        if sha256(record.content).hexdigest() != record.artifact.sha256:
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'OPTIMIZATION_REPORT_TAMPERED',
                    'message': 'optimization summary SHA-256 校验失败。',
                },
            )
        try:
            summary = json.loads(record.content.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'OPTIMIZATION_REPORT_INVALID',
                    'message': 'optimization summary 不是有效 JSON。',
                },
            ) from exc
        if not isinstance(summary, dict):
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'OPTIMIZATION_REPORT_INVALID',
                    'message': 'optimization summary 结构无效。',
                },
            )
        optimization = summary.get('optimization')
        topsis = optimization.get('topsis') if isinstance(optimization, dict) else None
        solutions = optimization.get('pareto_solutions') if isinstance(optimization, dict) else None
        names = optimization.get('objective_names') if isinstance(optimization, dict) else None
        if (
            not isinstance(optimization, dict)
            or not isinstance(names, list)
            or not names
            or not all(isinstance(name, str) and name for name in names)
            or len(set(names)) != len(names)
            or not isinstance(solutions, list)
            or not solutions
            or not isinstance(topsis, dict)
        ):
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'OPTIMIZATION_DECISION_EVIDENCE_MISSING',
                    'message': '真实优化缺少可重算的 Pareto/TOPSIS 证据。',
                },
            )
        try:
            best_index = int(topsis['best_index'])
            ranking = [int(index) for index in topsis['ranking']]
            scores = [float(value) for value in topsis['closeness']]
            weights = [float(value) for value in topsis['weights']]
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'OPTIMIZATION_DECISION_EVIDENCE_INVALID',
                    'message': 'TOPSIS 证据字段无效。',
                },
            ) from exc
        if (
            best_index < 0
            or best_index >= len(solutions)
            or len(ranking) != len(solutions)
            or sorted(ranking) != list(range(len(solutions)))
            or len(scores) != len(solutions)
            or len(weights) != len(names)
            or not all(math.isfinite(value) for value in (*scores, *weights))
        ):
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'OPTIMIZATION_DECISION_EVIDENCE_INVALID',
                    'message': 'TOPSIS 排名、权重或分数无法通过有限性校验。',
                },
            )
        review_status = job_result.get('reviewStatus') or summary.get('review_status') or {}
        if not (
            isinstance(review_status, dict)
            and review_status.get('all_verified_execution') is True
            and review_status.get('all_accepted') is True
        ):
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'OPTIMIZATION_FEM_REVIEW_MISSING',
                    'message': '候选缺少全部通过的真实 FEM review 证据。',
                },
            )
        review_records = summary.get('review_records')
        reviewed_indexes: set[int] = set()
        if isinstance(review_records, list):
            for review in review_records:
                if not isinstance(review, dict):
                    continue
                candidate = review.get('candidate')
                if (
                    isinstance(candidate, dict)
                    and isinstance(candidate.get('pareto_index'), int)
                    and review.get('accepted') is True
                    and review.get('verified_execution') is True
                ):
                    reviewed_indexes.add(candidate['pareto_index'])
        if best_index not in reviewed_indexes:
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'OPTIMIZATION_FEM_REVIEW_MISSING',
                    'message': 'TOPSIS 推荐候选没有对应的通过 FEM review 记录。',
                },
            )
        raw_objective_limits = summary.get('objective_limits')
        if raw_objective_limits is None:
            candidate_filter = summary.get('surrogate_candidate_filter')
            raw_objective_limits = (
                candidate_filter.get('objective_limits')
                if isinstance(candidate_filter, dict)
                else {}
            )
        try:
            objective_limits = {
                str(name): float(value)
                for name, value in dict(raw_objective_limits or {}).items()
            }
            objective_limit_tolerance = float(summary.get('objective_limit_relative_tolerance') or 0.0)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'OPTIMIZATION_CONSTRAINT_EVIDENCE_INVALID',
                    'message': '优化约束证据无法解析。',
                },
            ) from exc
        if (
            objective_limit_tolerance < 0
            or not math.isfinite(objective_limit_tolerance)
            or not all(math.isfinite(value) for value in objective_limits.values())
        ):
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'OPTIMIZATION_CONSTRAINT_EVIDENCE_INVALID',
                    'message': '优化约束证据包含非法阈值。',
                },
            )
        candidates: list[dict[str, Any]] = []
        rank_by_index = {index: rank + 1 for rank, index in enumerate(ranking)}
        for index, solution in enumerate(solutions):
            if not isinstance(solution, dict):
                raise HTTPException(
                    status_code=422,
                    detail={
                        'code': 'OPTIMIZATION_DECISION_EVIDENCE_INVALID',
                        'message': 'Pareto 候选结构无效。',
                    },
                )
            params = solution.get('design_parameters')
            objectives = solution.get('objective_values')
            if (
                not isinstance(params, dict)
                or not params
                or not isinstance(objectives, dict)
                or set(objectives) != set(names)
            ):
                raise HTTPException(
                    status_code=422,
                    detail={
                        'code': 'OPTIMIZATION_DECISION_EVIDENCE_INVALID',
                        'message': 'Pareto 候选缺少完整参数或目标值。',
                    },
                )
            try:
                normalized_params = {str(key): float(value) for key, value in params.items()}
                normalized_objectives = {str(key): float(objectives[key]) for key in names}
            except (KeyError, TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=422,
                    detail={
                        'code': 'OPTIMIZATION_DECISION_EVIDENCE_INVALID',
                        'message': 'Pareto 候选包含不可解析数值。',
                    },
                ) from exc
            if not all(math.isfinite(value) for value in (*normalized_params.values(), *normalized_objectives.values())):
                raise HTTPException(
                    status_code=422,
                    detail={
                        'code': 'OPTIMIZATION_DECISION_EVIDENCE_INVALID',
                        'message': 'Pareto 候选包含非有限数值。',
                    },
                )
            constraints_passed = True
            for name, value in normalized_objectives.items():
                _, _, objective_name = name.partition(':')
                limit = objective_limits.get(name, objective_limits.get(objective_name))
                if limit is None:
                    continue
                allowed = limit + abs(limit) * objective_limit_tolerance
                if value > allowed:
                    constraints_passed = False
                    break
            candidates.append({
                'id': f'pareto_{index}',
                'params': normalized_params,
                'objectives': normalized_objectives,
                'constraintsPassed': constraints_passed,
                'femReviewed': index in reviewed_indexes,
                'topsisScore': scores[index],
                'rank': rank_by_index[index],
                'selected': index == best_index,
            })
        selected = candidates[best_index]
        objective_weights = {
            str(name): weights[index]
            for index, name in enumerate(names)
        }
        return {
            'optimizationRunId': optimization_run_id,
            'executionMode': 'REAL',
            'simulation': False,
            'sourceJobId': job.job_id,
            'sourceArtifactId': record.artifact.artifact_id,
            'sourceSha256': record.artifact.sha256,
            'objectiveWeights': objective_weights,
            'entropyWeights': objective_weights,
            'candidates': candidates,
            'recommendation': {
                'candidateId': selected['id'],
                'explanation': '推荐来自已登记 Pareto/TOPSIS 证据和全部通过的真实 FEM review。',
            },
        }

    def register_artifact(
        self,
        *,
        kind: ArtifactKind,
        name: str,
        path: str,
        mime_type: str,
        preview: Any,
        content: bytes | None = None,
        run_id: str | None = None,
    ) -> Artifact:
        with self._state_lock:
            artifact = self._register_artifact(
                kind=kind,
                name=name,
                path=path,
                mime_type=mime_type,
                preview=preview,
                content=content,
                run_id=run_id,
            )
            self.persist()
            return artifact

    def claim_artifact_for_run(self, artifact_id: str, run_id: str) -> Artifact:
        record = self.get_artifact(artifact_id)
        owner = record.artifact.run_id
        if owner and owner != run_id:
            raise HTTPException(
                status_code=422,
                detail={'code': 'CROSS_RUN_ARTIFACT', 'message': f'制品 {artifact_id} 已属于运行 {owner}'},
            )
        if owner is None:
            record.artifact.run_id = run_id
            self.persist()
        return record.artifact

    def persist(self) -> None:
        with self._state_lock:
            update_engineering_config = self.engineering_config != self._loaded_engineering_config
            revision = self.repository.save(
                jobs=[job.model_dump(by_alias=True, mode='json') for job in self.jobs],
                artifacts=[
                    {
                        'artifact': record.artifact.model_dump(by_alias=True, mode='json'),
                        'preview': record.preview,
                        'content': record.content,
                    }
                    for record in self.artifacts
                ],
                engineering_config=self.engineering_config,
                update_engineering_config=update_engineering_config,
            )
            # 内存态与刚保存的持久态一致，同步标记避免下一次 refresh 白白重载。
            self.repository._last_loaded_revision = revision
            if update_engineering_config:
                self._loaded_engineering_config = deepcopy(self.engineering_config)

    def _register_artifact(
        self,
        *,
        kind: ArtifactKind,
        name: str,
        path: str,
        mime_type: str,
        preview: Any,
        content: bytes | None = None,
        artifact_id: str | None = None,
        source: str = 'PLATFORM_JOB',
        run_id: str | None = None,
        created_at: str | None = None,
    ) -> Artifact:
        artifact_id = artifact_id or (
            gen_id('art') if not name.startswith('baseline_') else f'art_{name.removesuffix(".json")}'
        )
        safe_preview = coerce_non_finite_floats(preview)
        raw = content if content is not None else self._content_from_preview(safe_preview)
        artifact = Artifact(
            artifactId=artifact_id,
            kind=kind,
            name=name,
            path=path,
            mimeType=mime_type,
            sizeBytes=len(raw),
            sha256=sha256(raw).hexdigest(),
            canPreview=kind != 'BINARY' and not mime_type.startswith('application/octet-stream'),
            downloadUrl=f'/api/v1/artifacts/{artifact_id}/download',
            source=source,
            runId=run_id,
            createdAt=created_at,
        )
        # 参数批量计算的工作线程会并发注册制品；不持锁 insert 与
        # persist() 的列表快照遍历交叉时，落库内容可能重复或漏项。
        with self._state_lock:
            self.artifacts.insert(0, ArtifactRecord(artifact=artifact, preview=safe_preview, content=raw))
        return artifact

    @staticmethod
    def _contains_dry_run(value: Any) -> bool:
        if isinstance(value, dict):
            for key, item in value.items():
                normalized_key = str(key).replace('-', '_').lower()
                if normalized_key == 'dry_run' and item is True:
                    return True
                if normalized_key == 'execution_mode' and str(item).lower() == 'dry_run':
                    return True
                if PlatformStore._contains_dry_run(item):
                    return True
        elif isinstance(value, list):
            return any(PlatformStore._contains_dry_run(item) for item in value)
        return False

    @classmethod
    def _is_verified_real_case_output(cls, analysis: dict[str, Any]) -> bool:
        """按真实求解摘要验证案例，兼容旧版顶层执行模式。"""
        metadata = analysis.get('metadata') or {}
        solver_design = metadata.get('solver_design') or {}
        execution_mode = solver_design.get('execution_mode') or metadata.get('execution_mode')
        return (
            analysis.get('status') == 'completed'
            and str(execution_mode).lower() == 'run'
            and metadata.get('is_verified_solver_output') is True
            and not cls._contains_dry_run(analysis)
        )

    @staticmethod
    def _artifact_content_hash_matches(record: ArtifactRecord) -> bool:
        return record.artifact.sha256 == sha256(record.content).hexdigest()

    @staticmethod
    def _manifest_entry(
        entries: dict[str, dict[str, Any]],
        *,
        root_directory: str,
        artifact: Artifact,
    ) -> dict[str, Any] | None:
        path = str(artifact.path).replace('\\', '/').lstrip('./')
        prefix = f'{root_directory}/'
        marker = f'/{prefix}'
        relative_path = path.split(marker, 1)[1] if marker in path else None
        if not relative_path:
            return None
        return entries.get(relative_path)

    def _reverification_case_sources(
        self,
        *,
        job: Job,
        case: dict[str, Any],
        manifest_entries: dict[str, dict[str, Any]],
        root_directory: str,
    ) -> list[Artifact]:
        """验证历史案例仍保存着命令流、时程和 manifest 哈希证据。"""
        case_id = str(case.get('caseId') or '')
        solver_case_id = str(case.get('solverCaseId') or '')
        if not case_id or not solver_case_id or case.get('status') != 'completed':
            raise HTTPException(status_code=422, detail={
                'code': 'SWEEP_REVERIFICATION_EVIDENCE_MISSING',
                'message': '历史案例缺少完成状态、案例编号或求解案例编号，不能回写验证结论。',
            })

        candidates = [
            artifact for artifact in job.artifacts
            if f'/{case_id}/' in str(artifact.path).replace('\\', '/')
            and f'/{solver_case_id}/' in str(artifact.path).replace('\\', '/')
        ]
        command_artifacts = [artifact for artifact in candidates if artifact.kind == 'COMMAND_STREAM']
        timeseries_artifacts = [artifact for artifact in candidates if artifact.kind == 'CSV_TIMESERIES']
        if len(command_artifacts) != 1 or len(timeseries_artifacts) != 1:
            raise HTTPException(status_code=422, detail={
                'code': 'SWEEP_REVERIFICATION_EVIDENCE_MISSING',
                'message': f'案例 {case_id} 缺少唯一的已登记命令流或时程结果，不能回写验证结论。',
            })

        sources = [*command_artifacts, *timeseries_artifacts]
        for artifact in sources:
            record = self.get_artifact(artifact.artifact_id)
            entry = self._manifest_entry(
                manifest_entries,
                root_directory=root_directory,
                artifact=artifact,
            )
            if (
                not self._artifact_content_hash_matches(record)
                or entry is None
                or entry.get('sha256') != artifact.sha256
                or entry.get('sizeBytes') != artifact.size_bytes
            ):
                raise HTTPException(status_code=422, detail={
                    'code': 'SWEEP_REVERIFICATION_HASH_MISMATCH',
                    'message': f'案例 {case_id} 的已登记输出与 output manifest 哈希不一致。',
                })

        summary_candidates = [
            entry for path, entry in manifest_entries.items()
            if path.startswith(f'{case_id}/solver_outputs/{solver_case_id}/')
            and path.endswith('/summary.json')
        ]
        objectives = case.get('objectives') or {}
        has_finite_objective = any(
            isinstance(value, (int, float)) and math.isfinite(float(value))
            for value in objectives.values()
        )
        if len(summary_candidates) != 1 or not has_finite_objective:
            raise HTTPException(status_code=422, detail={
                'code': 'SWEEP_REVERIFICATION_EVIDENCE_MISSING',
                'message': f'案例 {case_id} 缺少可审计的求解摘要或有限响应值，不能回写验证结论。',
            })
        return sources

    def reverify_real_damper_parameter_sweep(self, job_id: str) -> Job:
        """基于已登记的历史输出重建参数批量的派生证据，不触发求解器。"""
        with self.state_transaction():
            self.refresh()
            job = self.get_job(job_id)
            result = job.result or {}
            if (result.get('evidenceReverification') or {}).get('status') == 'SUCCEEDED':
                return job
            if (
                job.status != 'SUCCEEDED'
                or not self._is_real_damper_parameter_sweep_request(job.type, job.request)
                or result.get('mode') != 'real_damper_parameter_sweep'
            ):
                raise HTTPException(status_code=422, detail={
                    'code': 'SWEEP_REVERIFICATION_UNSUPPORTED',
                    'message': '只有已成功完成的真实阻尼器参数批量可以重建证据。',
                })

            manifest_artifact_id = str(result.get('outputManifestArtifactId') or '')
            if not manifest_artifact_id:
                raise HTTPException(status_code=422, detail={
                    'code': 'SWEEP_REVERIFICATION_EVIDENCE_MISSING',
                    'message': '历史批量缺少 output manifest，不能回写验证结论。',
                })
            manifest_record = self.get_artifact(manifest_artifact_id)
            if not self._artifact_content_hash_matches(manifest_record):
                raise HTTPException(status_code=422, detail={
                    'code': 'SWEEP_REVERIFICATION_HASH_MISMATCH',
                    'message': 'output manifest 的已登记内容与哈希不一致。',
                })
            try:
                manifest = json.loads(manifest_record.content.decode('utf-8-sig'))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise HTTPException(status_code=422, detail={
                    'code': 'SWEEP_REVERIFICATION_EVIDENCE_MISSING',
                    'message': 'output manifest 不是有效 JSON，不能回写验证结论。',
                }) from exc
            root_directory = str(manifest.get('rootDirectory') or '')
            raw_entries = manifest.get('files')
            if manifest.get('schemaVersion') != '1.0' or not root_directory or not isinstance(raw_entries, list):
                raise HTTPException(status_code=422, detail={
                    'code': 'SWEEP_REVERIFICATION_EVIDENCE_MISSING',
                    'message': 'output manifest 结构无效，不能回写验证结论。',
                })
            manifest_entries = {
                str(entry.get('path')): entry
                for entry in raw_entries
                if isinstance(entry, dict)
                and isinstance(entry.get('path'), str)
                and len(str(entry.get('sha256') or '')) == 64
                and isinstance(entry.get('sizeBytes'), int)
            }
            if len(manifest_entries) != len(raw_entries):
                raise HTTPException(status_code=422, detail={
                    'code': 'SWEEP_REVERIFICATION_EVIDENCE_MISSING',
                    'message': 'output manifest 包含无效文件条目，不能回写验证结论。',
                })

            case_results = list(result.get('caseResults') or [])
            if not case_results or not all(isinstance(case, dict) for case in case_results):
                raise HTTPException(status_code=422, detail={
                    'code': 'SWEEP_REVERIFICATION_EVIDENCE_MISSING',
                    'message': '历史批量缺少案例结果，不能回写验证结论。',
                })
            original_case_artifact_ids: set[str] = set()
            repaired_case_artifacts: list[Artifact] = []
            source_artifact_ids: list[str] = [manifest_artifact_id]
            repaired_cases: list[dict[str, Any]] = []
            for case in case_results:
                case_id = str(case.get('caseId') or '')
                original = next(
                    (artifact for artifact in job.artifacts if artifact.name == f'real_{case_id}_case_summary.json'),
                    None,
                )
                if original is None:
                    raise HTTPException(status_code=422, detail={
                        'code': 'SWEEP_REVERIFICATION_EVIDENCE_MISSING',
                        'message': f'案例 {case_id} 缺少原始案例摘要，不能回写验证结论。',
                    })
                sources = self._reverification_case_sources(
                    job=job,
                    case=case,
                    manifest_entries=manifest_entries,
                    root_directory=root_directory,
                )
                source_ids = [artifact.artifact_id for artifact in sources]
                source_artifact_ids.extend(source_ids)
                repaired_case = {
                    **case,
                    'isVerifiedSolverOutput': True,
                    'evidenceReverification': {
                        'mode': 'REGISTERED_OUTPUTS',
                        'manifestArtifactId': manifest_artifact_id,
                        'sourceArtifactIds': source_ids,
                    },
                }
                repaired_cases.append(repaired_case)
                original_case_artifact_ids.add(original.artifact_id)
                repaired_case_artifacts.append(self._register_artifact(
                    kind='JSON_SUMMARY',
                    name=original.name,
                    path=f'output/platform_store/evidence_reverification/{job_id}/{case_id}_case_summary.json',
                    mime_type='application/json',
                    preview=repaired_case,
                    content=strict_json_dumps(repaired_case).encode('utf-8'),
                    source='REAL_SOLVER_RESULT',
                ))

            original_summary = next(
                (artifact for artifact in job.artifacts if artifact.name == 'real_damper_parameter_sweep_summary.json'),
                None,
            )
            if original_summary is None:
                raise HTTPException(status_code=422, detail={
                    'code': 'SWEEP_REVERIFICATION_EVIDENCE_MISSING',
                    'message': '历史批量缺少汇总摘要，不能回写验证结论。',
                })
            original_summary_record = self.get_artifact(original_summary.artifact_id)
            if not self._artifact_content_hash_matches(original_summary_record):
                raise HTTPException(status_code=422, detail={
                    'code': 'SWEEP_REVERIFICATION_HASH_MISMATCH',
                    'message': '历史批量汇总摘要的已登记内容与哈希不一致。',
                })
            try:
                summary = json.loads(original_summary_record.content.decode('utf-8-sig'))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise HTTPException(status_code=422, detail={
                    'code': 'SWEEP_REVERIFICATION_EVIDENCE_MISSING',
                    'message': '历史批量汇总摘要不是有效 JSON，不能回写验证结论。',
                }) from exc
            if {str(case.get('caseId') or '') for case in summary.get('caseResults') or []} != {
                str(case.get('caseId') or '') for case in repaired_cases
            }:
                raise HTTPException(status_code=422, detail={
                    'code': 'SWEEP_REVERIFICATION_EVIDENCE_MISSING',
                    'message': '历史批量汇总摘要与案例结果不一致，不能回写验证结论。',
                })
            repaired_summary = {
                **summary,
                'caseResults': repaired_cases,
                'allVerifiedExecution': True,
                'evidenceReverification': {
                    'status': 'SUCCEEDED',
                    'mode': 'REGISTERED_OUTPUTS',
                    'manifestArtifactId': manifest_artifact_id,
                    'sourceArtifactIds': list(dict.fromkeys(source_artifact_ids)),
                    'supersededArtifactIds': [
                        original_summary.artifact_id,
                        *sorted(original_case_artifact_ids),
                    ],
                },
            }
            repaired_summary_artifact = self._register_artifact(
                kind='JSON_SUMMARY',
                name=original_summary.name,
                path=f'output/platform_store/evidence_reverification/{job_id}/real_damper_parameter_sweep_summary.json',
                mime_type='application/json',
                preview=repaired_summary,
                content=strict_json_dumps(repaired_summary).encode('utf-8'),
                source='REAL_SOLVER_RESULT',
            )
            superseded_artifact_ids = {*original_case_artifact_ids, original_summary.artifact_id}
            job.artifacts = [
                repaired_summary_artifact,
                *repaired_case_artifacts,
                *(artifact for artifact in job.artifacts if artifact.artifact_id not in superseded_artifact_ids),
            ]
            job.result = {
                **self._job_result(job.type, job.request, job.artifacts),
                'evidenceReverification': repaired_summary['evidenceReverification'],
            }
            self.persist()
            return job

    @staticmethod
    def _real_history_kind(name: str) -> ArtifactKind:
        if name in {'optimization_summary.json', *OPTIMIZATION_OVERVIEW_ARTIFACT_NAMES.values()}:
            return 'OPTIMIZATION_REPORT'
        if name.endswith('.csv'):
            return 'CSV_TABLE'
        return 'JSON_SUMMARY'

    @staticmethod
    def _real_history_preview(file_path: Path, raw: bytes) -> Any:
        text = raw.decode('utf-8-sig')
        if file_path.suffix.lower() == '.json':
            return json.loads(text)
        rows = list(csv.reader(text.splitlines()))
        return {
            'headers': rows[0] if rows else [],
            'previewRows': rows[1:51],
            'totalRows': max(len(rows) - 1, 0),
        }

    @staticmethod
    def _artifact_display_path(file_path: Path) -> str:
        try:
            return file_path.resolve().relative_to(REPO_ROOT).as_posix()
        except ValueError:
            return file_path.resolve().as_posix()

    def _generate_artifacts(
        self,
        job_type: JobType,
        params: dict[str, Any],
        *,
        job_id: str | None = None,
    ) -> list[Artifact]:
        if self._is_real_damper_comparison_request(job_type, params):
            return self._generate_real_damper_comparison_artifacts(params, job_id=job_id)
        if self._is_real_damper_parameter_sweep_request(job_type, params):
            return self._generate_real_damper_parameter_sweep_artifacts(params, job_id=job_id)
        if self._is_real_agent_analysis_request(job_type, params):
            return self._generate_real_agent_analysis_artifacts(params, job_id=job_id)
        if self._is_real_baseline_optimization_request(job_type, params):
            return self._generate_real_baseline_optimization_artifacts(params, job_id=job_id)
        if self._is_workflow_execution_request(params):
            return [
                self._register_artifact(
                    kind='JSON_SUMMARY',
                    name='workflow_execution_summary.json',
                    path='output/jobs/workflow_execution_summary.json',
                    mime_type='application/json',
                    preview=self._workflow_execution_summary(job_type, params),
                )
            ]
        if params.get('inputArtifactId') and job_type in {'LOAD_TRAFFIC_RANDOM', 'LOAD_WIND_VERTICAL', 'LOAD_EARTHQUAKE'}:
            return self._generate_uploaded_load_artifacts(job_type, params)
        if job_type == 'COMMAND_STREAM_ASSEMBLY':
            command_text = self._command_stream_text(params)
            command_content = command_text.encode('utf-8')
            command_sha = sha256(command_content).hexdigest()
            module_config = params.get('moduleConfig') or {}
            return [
                self._register_artifact(
                    kind='COMMAND_STREAM',
                    name='assembled_command_stream.mac',
                    path='output/command_streams/assembled_command_stream.mac',
                    mime_type='text/plain',
                    preview={
                        'commandText': command_text,
                        'sha256': command_sha,
                        'moduleSummary': {
                            'solver': params.get('solver', 'ANSYS'),
                            'bridgeId': params.get('bridgeId'),
                            'caseSetId': params.get('caseSetId'),
                            'modules': module_config.get('modules', []),
                            'responseTargets': module_config.get('responseTargets', []),
                            'damperMaterial': ((module_config.get('damper') or {}).get('material') or {}).get('materialType'),
                        },
                    },
                    content=command_content,
                )
            ]
        if job_type == 'OPTIMIZATION_EXPORT':
            kinds = params.get('exportKinds') or ['PARETO_FRONT']
            formats = params.get('formats') or ['CSV']
            artifacts: list[Artifact] = []
            for kind in kinds:
                for fmt in formats:
                    ext = str(fmt).lower()
                    artifacts.append(
                        self._register_artifact(
                            kind='PLOT' if ext in ('png', 'svg') else 'CSV_TABLE',
                            name=f'optimization_{str(kind).lower()}.{ext}',
                            path=f'output/optimization/exports/{str(kind).lower()}.{ext}',
                            mime_type='image/svg+xml' if ext == 'svg' else 'text/csv',
                            preview=self._csv_preview(),
                        )
                    )
            return artifacts
        if job_type == 'SURROGATE_TRAINING':
            source_artifact = self._resolve_surrogate_dataset_artifact(params)
            return [
                self._register_artifact(
                    kind='RAW_DATA',
                    name='surrogate_training_dataset.csv',
                    path='output/surrogates/training_dataset.csv',
                    mime_type='text/csv',
                    preview={
                        **self._csv_preview(),
                        'datasetSourceMode': params.get('datasetSourceMode', 'DOE_DATASET'),
                        'sourceArtifactId': source_artifact.artifact_id,
                    },
                ),
                self._register_artifact(
                    kind='SURROGATE_MODEL',
                    name='trained_surrogate_model.pkl',
                    path='output/surrogates/trained_surrogate_model.pkl',
                    mime_type='application/octet-stream',
                    preview={
                        **mock_demo_fields(),
                        'modelFamilies': params.get('modelFamilies', []),
                        'status': 'MOCK_MODEL',
                    },
                    content=b'mock-surrogate-model',
                ),
            ]
        if job_type in ('MULTI_OBJECTIVE_OPTIMIZATION', 'ENTROPY_TOPSIS_DECISION'):
            return [
                self._register_artifact(
                    kind='OPTIMIZATION_REPORT' if job_type == 'MULTI_OBJECTIVE_OPTIMIZATION' else 'DECISION_REPORT',
                    name='optimization_decision_report.json',
                    path='output/optimization/optimization_decision_report.json',
                    mime_type='application/json',
                    preview={**mock_demo_fields(), **self.topsis_result()},
                )
            ]
        if job_type == 'ACTIVE_LEARNING' and not params.get('activeLearningEnabled', True):
            return [
                self._register_artifact(
                    kind='JSON_SUMMARY',
                    name='active_learning_skipped.json',
                    path='output/active_learning/active_learning_skipped.json',
                    mime_type='application/json',
                    preview={'activeLearningEnabled': False, 'skipped': True, 'reason': 'active learning disabled by request'},
                )
            ]
        if job_type == 'EXPERIMENT_DESIGN':
            artifacts = [
                self._register_artifact(
                    kind='CSV_TABLE',
                    name='doe_design_matrix.csv',
                    path='output/doe/doe_design_matrix.csv',
                    mime_type='text/csv',
                    preview=self._doe_matrix_preview(params),
                ),
                self._register_artifact(
                    kind='JSON_SUMMARY',
                    name='doe_case_set_summary.json',
                    path='output/doe/doe_case_set_summary.json',
                    mime_type='application/json',
                    preview=self._doe_case_set_summary(params),
                ),
                self._register_artifact(
                    kind='RAW_DATA',
                    name='doe_response_training_dataset.csv',
                    path='output/doe/doe_response_training_dataset.csv',
                    mime_type='text/csv',
                    preview=self._doe_response_dataset_preview(params),
                ),
            ]
            if params.get('executionGoal') == 'OPTIMIZATION_RECOMMENDATION':
                artifacts.extend([
                    self._register_artifact(
                        kind='SURROGATE_MODEL',
                        name='doe_surrogate_seed_model.pkl',
                        path='output/doe/doe_surrogate_seed_model.pkl',
                        mime_type='application/octet-stream',
                        preview={
                            **mock_demo_fields(),
                            'source': 'EXPERIMENT_DESIGN',
                            'status': 'MOCK_MODEL',
                        },
                        content=b'mock-surrogate-model',
                    ),
                    self._register_artifact(
                        kind='OPTIMIZATION_REPORT',
                        name='doe_pareto_seed_report.json',
                        path='output/doe/doe_pareto_seed_report.json',
                        mime_type='application/json',
                        preview={**mock_demo_fields(), **self.topsis_result()},
                    ),
                    self._register_artifact(
                        kind='DECISION_REPORT',
                        name='doe_topsis_seed_recommendation.json',
                        path='output/doe/doe_topsis_seed_recommendation.json',
                        mime_type='application/json',
                        preview={**mock_demo_fields(), **self.topsis_result()},
                    ),
                ])
            return artifacts
        if job_type == 'LOAD_TRAFFIC_RANDOM':
            traffic_payload = self._traffic_payload(params)
            return [
                self._register_artifact(
                    kind='LOAD_CASE',
                    name=self._traffic_artifact_name(params, 'csv'),
                    path=f"output/load_cases/{self._traffic_artifact_name(params, 'csv')}",
                    mime_type='text/csv',
                    preview=traffic_payload['preview'],
                    content=traffic_payload['csvBytes'],
                ),
                self._register_artifact(
                    kind='JSON_SUMMARY',
                    name=self._traffic_artifact_name(params, 'json'),
                    path=f"output/load_cases/{self._traffic_artifact_name(params, 'json')}",
                    mime_type='application/json',
                    preview=traffic_payload['summary'],
                    content=traffic_payload['jsonBytes'],
                ),
            ]
        if job_type == 'LOAD_CURVE_EXPORT':
            load_kind = str(params.get('loadKind') or 'LOAD').lower()
            component = str(params.get('curveComponent') or 'component').lower()
            formats = params.get('formats') or ['PNG']
            return [
                self._register_artifact(
                    kind='PLOT',
                    name=f'load_curve_{load_kind}_{component}.{str(format_name).lower()}',
                    path=f'output/plots/load_curve_{load_kind}_{component}.{str(format_name).lower()}',
                    mime_type=self._load_curve_mime_type(str(format_name)),
                    preview={
                        **self._csv_preview(),
                        'sourceArtifactId': params.get('sourceArtifactId'),
                        'loadKind': params.get('loadKind'),
                        'format': format_name,
                    },
                )
                for format_name in formats
            ]
        if job_type in ('LOAD_WIND_VERTICAL', 'LOAD_EARTHQUAKE'):
            return [
                self._register_artifact(
                    kind='LOAD_CASE',
                    name=f'{job_type.lower()}_artifact.csv',
                    path=f'output/load_cases/{job_type.lower()}_artifact.csv',
                    mime_type='text/csv',
                    preview=self._csv_preview(),
                )
            ]
        return [
            self._register_artifact(
                kind='JSON_SUMMARY',
                name=f'{job_type.lower()}_summary.json',
                path=f'output/jobs/{job_type.lower()}_summary.json',
                mime_type='application/json',
                preview={'jobType': job_type, 'request': params},
            )
        ]

    def _validate_traffic_source(self, params: dict[str, Any]) -> Artifact | None:
        if params.get('inputArtifactId'):
            return self.get_artifact(str(params['inputArtifactId'])).artifact
        if params.get('sourceMode') != 'LOAD_EXISTING':
            return None
        source_artifact_id = str(params.get('existingVehicleDataArtifactId') or '')
        source = self.get_artifact(source_artifact_id).artifact
        if source.kind not in ('LOAD_CASE', 'CSV_TIMESERIES', 'CSV_TABLE', 'RAW_DATA'):
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'INVALID_TRAFFIC_SOURCE',
                    'message': f'制品 {source_artifact_id} 不能作为已有车流数据源',
                },
            )
        return source

    def _traffic_payload(self, params: dict[str, Any]) -> dict[str, Any]:
        source = self._validate_traffic_source(params)
        if source is None:
            return build_random_traffic_payload(params)
        record = self.get_artifact(source.artifact_id)
        source_artifact = {
            **source.model_dump(by_alias=True, mode='json'),
            'preview': record.preview,
        }
        return build_existing_traffic_payload(params, source_artifact)

    def _standardized_uploaded_load(
        self,
        job_type: JobType,
        params: dict[str, Any],
    ) -> tuple[ArtifactRecord, StandardizedLoad]:
        if job_type not in {'LOAD_TRAFFIC_RANDOM', 'LOAD_WIND_VERTICAL', 'LOAD_EARTHQUAKE'}:
            raise HTTPException(
                status_code=422,
                detail={'code': 'INVALID_LOAD_ARTIFACT_USAGE', 'message': f'{job_type} 不接受 inputArtifactId'},
            )
        artifact_id = str(params.get('inputArtifactId') or '')
        record = self.get_artifact(artifact_id)
        if record.artifact.kind != 'RAW_DATA':
            raise HTTPException(
                status_code=422,
                detail={'code': 'INVALID_LOAD_SOURCE', 'message': f'制品 {artifact_id} 不是原始上传载荷'},
            )
        request_run_id = str(params.get('agentRunId') or params.get('runId') or '')
        if record.artifact.run_id and request_run_id and record.artifact.run_id != request_run_id:
            raise HTTPException(
                status_code=422,
                detail={'code': 'CROSS_RUN_ARTIFACT', 'message': f'制品 {artifact_id} 不属于当前运行'},
            )
        mapping = params.get('loadMapping')
        if not isinstance(mapping, dict):
            raise HTTPException(
                status_code=422,
                detail={'code': 'LOAD_MAPPING_REQUIRED', 'message': '本地载荷必须显式提供列、单位和施加对象映射'},
            )
        expected_quantity = 'ACCELERATION' if job_type == 'LOAD_EARTHQUAKE' else 'FORCE'
        if mapping.get('quantity') != expected_quantity:
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'INVALID_LOAD_QUANTITY',
                    'message': f'{job_type} 的 quantity 必须为 {expected_quantity}',
                },
            )
        if job_type == 'LOAD_EARTHQUAKE' and (
            mapping.get('targetType') != 'GROUND' or mapping.get('consistentExcitation') is not True
        ):
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'INVALID_EARTHQUAKE_MAPPING',
                    'message': '地震文件必须指定 GROUND 目标并设置 consistentExcitation=true',
                },
            )
        standardized = load_import_service.standardize(
            file_name=record.artifact.name,
            content=record.content,
            mapping=mapping,
        )
        return record, standardized

    def _generate_uploaded_load_artifacts(self, job_type: JobType, params: dict[str, Any]) -> list[Artifact]:
        source, standardized = self._standardized_uploaded_load(job_type, params)
        preview = self._standardized_load_preview(standardized)
        stem = {
            'LOAD_EARTHQUAKE': 'earthquake_file',
            'LOAD_WIND_VERTICAL': 'wind_file',
            'LOAD_TRAFFIC_RANDOM': 'traffic_file',
        }[job_type]
        load_artifact = self._register_artifact(
            kind='LOAD_CASE',
            name=f'{stem}_standardized.csv',
            path=f'output/load_cases/{stem}_standardized.csv',
            mime_type='text/csv',
            preview=preview,
            content=standardized.content,
        )
        if job_type != 'LOAD_TRAFFIC_RANDOM':
            return [load_artifact]
        summary = {
            'sourceMode': 'LOAD_EXISTING',
            'inputArtifactId': source.artifact.artifact_id,
            'sourceSha256': source.artifact.sha256,
            'standardSha256': standardized.digest,
            'normalization': standardized.report,
        }
        return [
            load_artifact,
            self._register_artifact(
                kind='JSON_SUMMARY',
                name='traffic_file_standardization.json',
                path='output/load_cases/traffic_file_standardization.json',
                mime_type='application/json',
                preview=summary,
                content=json.dumps(summary, ensure_ascii=False, indent=2).encode('utf-8'),
            ),
        ]

    @staticmethod
    def _standardized_load_preview(standardized: StandardizedLoad) -> dict[str, Any]:
        rows = list(csv.reader(standardized.content.decode('utf-8').splitlines()))
        return {
            'headers': rows[0] if rows else [],
            'previewRows': rows[1:51],
            'totalRows': max(len(rows) - 1, 0),
            'normalization': standardized.report,
        }

    def _traffic_artifact_name(self, params: dict[str, Any], extension: str) -> str:
        if params.get('sourceMode') == 'LOAD_EXISTING':
            return f'traffic_existing_main_girder_bidirectional.{extension}'
        if extension == 'json':
            return 'traffic_wim_2021_summary.json'
        return 'traffic_wim_2021_main_girder_bidirectional.csv'

    def _validate_load_curve_source(self, params: dict[str, Any]) -> Artifact:
        source_artifact_id = str(params.get('sourceArtifactId') or '')
        source = self.get_artifact(source_artifact_id).artifact
        if source.kind not in ('LOAD_CASE', 'CSV_TIMESERIES', 'CSV_TABLE', 'RAW_DATA'):
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'INVALID_LOAD_CURVE_SOURCE',
                    'message': f'制品 {source_artifact_id} 不能作为荷载曲线导出源',
                },
            )
        return source

    def _validate_surrogate_training_source(self, params: dict[str, Any]) -> Artifact:
        source = self._resolve_surrogate_dataset_artifact(params)
        if source.kind not in ('RAW_DATA', 'CSV_TABLE', 'CSV_TIMESERIES'):
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'INVALID_SURROGATE_DATASET_SOURCE',
                    'message': f'制品 {source.artifact_id} 不能作为代理模型训练数据源',
                },
            )
        if params.get('datasetSourceMode') == 'USER_IMPORTED_ARTIFACT':
            schema = params.get('importedDatasetSchema') or {}
            feature_columns = schema.get('featureColumns') or []
            target_columns = schema.get('targetColumns') or {}
            if not feature_columns or not target_columns:
                raise HTTPException(
                    status_code=422,
                    detail={
                        'code': 'INVALID_IMPORTED_DATASET_SCHEMA',
                        'message': '用户导入数据必须提交 featureColumns 和 targetColumns 映射',
                    },
                )
        return source

    def _resolve_surrogate_dataset_artifact(self, params: dict[str, Any]) -> Artifact:
        source_mode = params.get('datasetSourceMode') or 'DOE_DATASET'
        if source_mode == 'USER_IMPORTED_ARTIFACT':
            artifact_id = str(params.get('importedDatasetArtifactId') or '')
            return self.get_artifact(artifact_id).artifact

        dataset_id = str(params.get('datasetId') or '')
        if not dataset_id:
            raise HTTPException(
                status_code=422,
                detail={'code': 'VALIDATION_ERROR', 'message': 'datasetId is required for DOE_DATASET'},
            )
        for record in self.artifacts:
            if record.artifact.artifact_id == dataset_id:
                return record.artifact
        for job in self.jobs:
            if job.type != 'EXPERIMENT_DESIGN' or not job.result:
                continue
            if dataset_id in (job.result.get('datasetId'), job.result.get('trainingDatasetArtifactId')):
                artifact_id = str(job.result.get('trainingDatasetArtifactId') or '')
                return self.get_artifact(artifact_id).artifact
        raise HTTPException(
            status_code=404,
            detail={'code': 'NOT_FOUND', 'message': f'DOE 训练数据集 {dataset_id} 不存在'},
        )

    def _job_result(self, job_type: JobType, params: dict[str, Any], artifacts: list[Artifact]) -> dict[str, Any]:
        result: dict[str, Any] = {'artifactCount': len(artifacts), 'mode': 'local_sync'}
        if self._is_real_damper_comparison_request(job_type, params):
            artifact_by_name = {artifact.name: artifact for artifact in artifacts}
            summary_artifact = artifact_by_name.get('real_damper_comparison_summary.json')
            catalog_artifact = artifact_by_name.get('result_catalog.json')
            manifest_artifact = artifact_by_name.get('real_output_manifest.json')
            summary = self.get_artifact(summary_artifact.artifact_id).preview if summary_artifact else {}
            return {
                **result,
                'mode': 'real_damper_comparison',
                # 求解器取执行侧摘要的实测值，回退到请求参数；不能钉死 ANSYS，
                # 否则 OpenSeesPy 比选的结果会被下游证据门按 ANSYS 口径审查
                # （review 的 USER300 门就是按这个字段分支的）。
                'solver': str(summary.get('solver') or params.get('solver') or 'ANSYS').upper(),
                'comparisonBasis': summary.get('comparisonBasis'),
                'forceCapScope': summary.get('forceCapScope'),
                'caseResults': summary.get('caseResults') or [],
                'responseComparison': summary.get('responseComparison') or {},
                'allVerifiedExecution': summary.get('allVerifiedExecution') is True,
                'executionUsage': summary.get('executionUsage'),
                'resultCatalogArtifactId': catalog_artifact.artifact_id if catalog_artifact else None,
                'solverVersionProfile': params.get('solverVersionProfile'),
                'damperCalibrationProfiles': params.get('damperCalibrationProfiles') or [],
                'inputProvenance': params.get('inputProvenance') or [],
                'comparisonSummaryArtifactId': summary_artifact.artifact_id if summary_artifact else None,
                'outputManifestArtifactId': manifest_artifact.artifact_id if manifest_artifact else None,
                'outputManifestSha256': manifest_artifact.sha256 if manifest_artifact else None,
            }
        if self._is_real_damper_parameter_sweep_request(job_type, params):
            artifact_by_name = {artifact.name: artifact for artifact in artifacts}
            summary_artifact = artifact_by_name.get('real_damper_parameter_sweep_summary.json')
            catalog_artifact = artifact_by_name.get('result_catalog.json')
            manifest_artifact = artifact_by_name.get('real_output_manifest.json')
            summary = self.get_artifact(summary_artifact.artifact_id).preview if summary_artifact else {}
            return {
                **result,
                'mode': 'real_damper_parameter_sweep',
                'solver': params.get('solver'),
                'caseResults': summary.get('caseResults') or [],
                'allVerifiedExecution': summary.get('allVerifiedExecution') is True,
                'executionUsage': summary.get('executionUsage'),
                'responseIds': params.get('responseIds') or [],
                'resultCatalogArtifactId': catalog_artifact.artifact_id if catalog_artifact else None,
                'solverVersionProfile': params.get('solverVersionProfile'),
                'inputProvenance': params.get('inputProvenance') or [],
                'outputManifestArtifactId': manifest_artifact.artifact_id if manifest_artifact else None,
                'outputManifestSha256': manifest_artifact.sha256 if manifest_artifact else None,
            }
        if self._is_real_agent_analysis_request(job_type, params):
            artifact_by_name = {artifact.name: artifact for artifact in artifacts}
            summary_artifact = artifact_by_name.get('real_analysis_summary.json')
            overview_artifact = artifact_by_name.get('real_analysis_overview.json')
            catalog_artifact = artifact_by_name.get('result_catalog.json')
            manifest_artifact = artifact_by_name.get('real_output_manifest.json')
            summary = self.get_artifact(summary_artifact.artifact_id).preview if summary_artifact else {}
            overview = self.get_artifact(overview_artifact.artifact_id).preview if overview_artifact else {}
            verified = (
                summary.get('status') == 'completed'
                and summary.get('mode') == 'real_agent_analysis'
                and not self._contains_dry_run(summary)
            )
            return {
                **result,
                'mode': 'real_agent_analysis',
                'solver': params.get('solver'),
                'isVerifiedSolverOutput': verified,
                'executionUsage': summary.get('executionUsage'),
                'resultCatalogArtifactId': catalog_artifact.artifact_id if catalog_artifact else None,
                'customLoadEvidence': summary.get('customLoadEvidence'),
                'responseIds': params.get('responseIds') or [],
                'solverVersionProfile': params.get('solverVersionProfile'),
                'inputProvenance': params.get('inputProvenance') or [],
                'analysisSummaryArtifactId': summary_artifact.artifact_id if summary_artifact else None,
                'analysisOverviewArtifactId': overview_artifact.artifact_id if overview_artifact else None,
                'outputManifestArtifactId': manifest_artifact.artifact_id if manifest_artifact else None,
                'outputManifestSha256': manifest_artifact.sha256 if manifest_artifact else None,
            }
        if self._is_real_baseline_optimization_request(job_type, params):
            artifact_by_name = {artifact.name: artifact for artifact in artifacts}
            workflow_artifact = artifact_by_name.get('real_workflow_summary.json')
            optimization_artifact = artifact_by_name.get('real_optimization_summary.json')
            baseline_artifact = artifact_by_name.get('real_baseline_summary.json')
            overview_artifact = next(
                (
                    artifact_by_name[name]
                    for name in OPTIMIZATION_OVERVIEW_ARTIFACT_NAMES.values()
                    if name in artifact_by_name
                ),
                None,
            )
            catalog_artifact = artifact_by_name.get('result_catalog.json')
            manifest_artifact = artifact_by_name.get('real_output_manifest.json')
            workflow_summary = self.get_artifact(workflow_artifact.artifact_id).preview if workflow_artifact else {}
            optimization_summary = self.get_artifact(optimization_artifact.artifact_id).preview if optimization_artifact else {}
            overview_summary = self.get_artifact(overview_artifact.artifact_id).preview if overview_artifact else {}
            return {
                **result,
                'mode': 'real_baseline_optimization',
                'workflowConfigPath': workflow_summary.get('workflowConfigPath'),
                'sourceWorkflowConfigPath': workflow_summary.get('sourceWorkflowConfigPath'),
                'baselineSummaryPath': workflow_summary.get('baseline_summary_path'),
                'optimizationSummaryPath': workflow_summary.get('optimization_summary_path'),
                'workflowSummaryPath': workflow_summary.get('workflow_summary_path'),
                'baselineStatus': workflow_summary.get('baseline_status'),
                'validationStatus': optimization_summary.get('validation_status'),
                'activeLearningStatus': optimization_summary.get('active_learning_status'),
                'reviewStatus': optimization_summary.get('review_status'),
                'executionEvidence': workflow_summary.get('executionEvidence'),
                'resultCatalogArtifactId': catalog_artifact.artifact_id if catalog_artifact else None,
                'finalRecommendationStatus': overview_summary.get('finalRecommendationStatus'),
                'customLoadEvidence': workflow_summary.get('customLoadEvidence'),
                'damperLayoutEvidence': workflow_summary.get('damperLayoutEvidence'),
                'solverVersionProfile': params.get('solverVersionProfile'),
                'inputProvenance': params.get('inputProvenance') or [],
                'workflowSummaryArtifactId': workflow_artifact.artifact_id if workflow_artifact else None,
                'optimizationSummaryArtifactId': optimization_artifact.artifact_id if optimization_artifact else None,
                'baselineSummaryArtifactId': baseline_artifact.artifact_id if baseline_artifact else None,
                'designSetArtifactId': next(
                    (artifact.artifact_id for artifact in artifacts if artifact.name == 'real_design_set.json'),
                    None,
                ),
                'trainingDatasetArtifactId': next(
                    (artifact.artifact_id for artifact in artifacts if artifact.name == 'real_training_dataset.json'),
                    None,
                ),
                'earthquakeWorkflowOverviewArtifactId': overview_artifact.artifact_id if overview_artifact else None,
                'outputManifestArtifactId': manifest_artifact.artifact_id if manifest_artifact else None,
                'outputManifestSha256': manifest_artifact.sha256 if manifest_artifact else None,
            }
        if self._is_workflow_execution_request(params):
            return {
                **result,
                **self._workflow_execution_summary(job_type, params),
                'workflowSummaryArtifactId': artifacts[0].artifact_id if artifacts else None,
            }
        if params.get('inputArtifactId') and job_type in {'LOAD_TRAFFIC_RANDOM', 'LOAD_WIND_VERTICAL', 'LOAD_EARTHQUAKE'}:
            source, standardized = self._standardized_uploaded_load(job_type, params)
            return {
                **result,
                'source': 'LOCAL_FILE',
                'inputArtifactId': source.artifact.artifact_id,
                'sourceSha256': source.artifact.sha256,
                'standardSha256': standardized.digest,
                'normalization': standardized.report,
            }
        if job_type == 'LOAD_TRAFFIC_RANDOM':
            traffic_payload = self._traffic_payload(params)
            return {**result, **traffic_payload['summary']}
        if job_type == 'LOAD_WIND_VERTICAL':
            return {**result, 'appliedComponent': params.get('appliedComponent', 'VERTICAL'), 'source': params.get('source', 'WIND_MODULE')}
        if job_type == 'LOAD_EARTHQUAKE':
            return {
                **result,
                'source': params.get('source', 'TEMPLATE'),
                'direction': params.get('direction', 'X'),
                'durationS': params.get('durationS', 40),
                'timeStepS': params.get('timeStepS', 0.02),
            }
        if job_type == 'LOAD_CURVE_EXPORT':
            return {
                **result,
                'sourceArtifactId': params.get('sourceArtifactId'),
                'loadKind': params.get('loadKind'),
                'formats': params.get('formats', []),
            }
        if job_type == 'COMMAND_STREAM_ASSEMBLY':
            module_config = params.get('moduleConfig') or {}
            return {
                **result,
                'solver': params.get('solver', 'ANSYS'),
                'caseSetId': params.get('caseSetId'),
                'commandStreamSha256': artifacts[0].sha256 if artifacts else None,
                'moduleCount': len(module_config.get('modules', [])),
                'responseTargetCount': len(module_config.get('responseTargets', [])),
            }
        if job_type == 'SOLVER_BATCH':
            return {**result, 'solver': params.get('solver', 'ANSYS'), 'caseSetId': params.get('caseSetId')}
        if job_type == 'RESULT_EXTRACTION':
            return {**result, 'solverRunId': params.get('solverRunId'), 'extractors': params.get('extractors', [])}
        if job_type == 'EXPERIMENT_DESIGN':
            training_dataset = next((artifact for artifact in artifacts if artifact.kind == 'RAW_DATA'), None)
            return {
                **result,
                'scenarioType': params.get('scenarioType'),
                'executionGoal': params.get('executionGoal'),
                'caseSetId': params.get('caseSetPrefix'),
                'datasetId': training_dataset.artifact_id if training_dataset else None,
                'trainingDatasetArtifactId': training_dataset.artifact_id if training_dataset else None,
                'sampleCount': self._estimate_doe_sample_count(params),
                'responseTargetCount': len(params.get('responseTargets', [])),
            }
        if job_type == 'SURROGATE_TRAINING':
            source_artifact = self._resolve_surrogate_dataset_artifact(params)
            return {
                **result,
                'datasetSourceMode': params.get('datasetSourceMode', 'DOE_DATASET'),
                'datasetId': params.get('datasetId'),
                'importedDatasetArtifactId': params.get('importedDatasetArtifactId'),
                'sourceArtifactId': source_artifact.artifact_id,
                'modelFamilies': params.get('modelFamilies', []),
                'targetMetricIds': params.get('targetMetricIds', []),
            }
        if job_type == 'ACTIVE_LEARNING':
            if not params.get('activeLearningEnabled', True):
                return {**result, 'mode': 'skipped', 'activeLearningEnabled': False, 'skipped': True}
            return {**result, 'activeLearningEnabled': True, 'batchSize': params.get('batchSize', 0)}
        if job_type == 'MULTI_OBJECTIVE_OPTIMIZATION':
            return {
                **result,
                'objectiveMode': params.get('objectiveMode', 'SEISMIC'),
                'objectiveCount': len(params.get('objectives', [])),
                'constraintCount': len(params.get('constraints', [])),
            }
        if job_type == 'ENTROPY_TOPSIS_DECISION':
            return {**result, 'optimizationRunId': params.get('optimizationRunId'), 'candidateCount': len(self.topsis_result()['candidates'])}
        if job_type == 'OPTIMIZATION_EXPORT':
            return {**result, 'optimizationRunId': params.get('optimizationRunId'), 'exportKinds': params.get('exportKinds', []), 'formats': params.get('formats', [])}
        return result

    def _job_title(self, job_type: JobType, params: dict[str, Any]) -> str:
        titles = {
            'PROJECT_GATE_FAST': '快速回归门槛测试',
            'LOAD_WIND_VERTICAL': '竖向风荷载时程配置',
            'LOAD_TRAFFIC_RANDOM': '随机交通流荷载配置',
            'LOAD_EARTHQUAKE': '地震动输入加速度时程配置',
            'LOAD_CURVE_EXPORT': '荷载曲线图导出',
            'COMMAND_STREAM_ASSEMBLY': f'组装 {params.get("solver", "ANSYS")} 命令流',
            'SOLVER_BATCH': f'{params.get("solver", "ANSYS")} 批处理计算',
            'RESULT_EXTRACTION': '求解器结果自动提取',
            'EXPERIMENT_DESIGN': 'USER300 阻尼器试验设计',
            'SURROGATE_TRAINING': '代理模型自适应拟合训练',
            'ACTIVE_LEARNING': '主动学习 Infill 采样',
            'MULTI_OBJECTIVE_OPTIMIZATION': '结构多目标 Pareto 优化',
            'ENTROPY_TOPSIS_DECISION': '熵权 + TOPSIS 多准则方案决策',
            'OPTIMIZATION_EXPORT': 'Pareto / 熵权 / TOPSIS 优化结果导出',
        }
        return titles.get(job_type, f'任务: {job_type}')

    def _is_workflow_execution_request(self, params: dict[str, Any]) -> bool:
        return bool(params.get('executionTarget') and params.get('requiredModules'))

    def _should_queue_job(self, job_type: JobType, params: dict[str, Any]) -> bool:
        return job_type == 'SOLVER_BATCH' or self._is_real_baseline_optimization_request(job_type, params)

    def _is_real_baseline_optimization_request(self, job_type: JobType, params: dict[str, Any]) -> bool:
        scenario = str(params.get('scenario') or '')
        solver = str(params.get('solver') or 'ANSYS').upper()
        registered_solvers = OPTIMIZATION_WORKFLOW_CONFIGS_BY_LOAD_KIND.get(scenario) or {}
        return (
            job_type == 'MULTI_OBJECTIVE_OPTIMIZATION'
            and solver in registered_solvers
            and params.get('executionTarget') == 'OPTIMIZATION_DECISION'
            and params.get('runMode') == 'REAL_BASELINE_OPTIMIZATION'
        )

    def _generate_real_baseline_optimization_artifacts(
        self,
        params: dict[str, Any],
        *,
        job_id: str | None = None,
    ) -> list[Artifact]:
        load_kind = str(params.get('loadKind') or params.get('scenario') or 'EARTHQUAKE').upper()
        source_workflow_config_path = self._baseline_optimization_workflow_config_path(params)
        prepared_workflow = self._prepare_earthquake_baseline_optimization_workflow(
            source_workflow_config_path,
            params,
            job_id=job_id,
        )
        execution_timeout_s = params.get('executionTimeoutS')
        workflow_summary = self._run_real_baseline_optimization_workflow(
            prepared_workflow.workflow_config_path,
            execution_timeout_s=None if execution_timeout_s is None else float(execution_timeout_s),
        )
        workflow_summary = {
            **workflow_summary,
            'mode': 'real_baseline_optimization',
            'runMode': 'REAL_BASELINE_OPTIMIZATION',
            'workflowConfigPath': str(prepared_workflow.workflow_config_path),
            'sourceWorkflowConfigPath': str(source_workflow_config_path),
            'preparedConfig': {
                'baselineConfigPath': str(prepared_workflow.baseline_config_path),
                'optimizationConfigPath': str(prepared_workflow.optimization_config_path),
                'outputDir': str(prepared_workflow.output_dir),
            },
            'customLoadEvidence': (
                self._load_json_config(prepared_workflow.workflow_config_path).get('metadata') or {}
            ).get('custom_load_evidence'),
            'damperLayoutEvidence': (
                self._load_json_config(prepared_workflow.workflow_config_path).get('metadata') or {}
            ).get('damper_layout_evidence'),
            'solverVersionProfile': params.get('solverVersionProfile'),
            'inputProvenance': params.get('inputProvenance') or [],
        }
        optimization_summary_path = Path(str(workflow_summary.get('optimization_summary_path') or ''))
        baseline_summary_path = Path(str(workflow_summary.get('baseline_summary_path') or ''))
        workflow_summary_path = Path(str(workflow_summary.get('workflow_summary_path') or ''))
        execution_evidence = self._optimization_execution_evidence(
            optimization_summary_path=optimization_summary_path,
            prepared_workflow=prepared_workflow,
        )
        workflow_summary['executionEvidence'] = execution_evidence
        if optimization_summary_path.is_file():
            optimization_summary_payload = self._load_json_config(optimization_summary_path)
            optimization_summary_payload['executionEvidence'] = execution_evidence
            self._write_json_config(optimization_summary_path, optimization_summary_payload)
        else:
            optimization_summary_payload = {}
        design_set_artifact, training_dataset_artifact = self._register_real_optimization_datasets(
            run_dir=prepared_workflow.output_dir,
            optimization_summary=optimization_summary_payload,
            execution_evidence=execution_evidence,
            prepared_workflow=prepared_workflow,
        )
        self._write_json_config(workflow_summary_path, workflow_summary)
        workflow_artifact = self._register_json_file_artifact(
            name='real_workflow_summary.json',
            path=workflow_summary_path,
            fallback_preview={'path': str(workflow_summary_path), 'status': 'MISSING'},
        )
        optimization_artifact = self._register_json_file_artifact(
            name='real_optimization_summary.json',
            path=optimization_summary_path,
            fallback_preview={'path': str(optimization_summary_path), 'status': 'MISSING'},
        )
        baseline_artifact = self._register_json_file_artifact(
            name='real_baseline_summary.json',
            path=baseline_summary_path,
            fallback_preview={'path': str(baseline_summary_path), 'status': 'MISSING'},
        )
        optimization_summary = self.get_artifact(optimization_artifact.artifact_id).preview
        baseline_summary = self.get_artifact(baseline_artifact.artifact_id).preview
        overview = self._earthquake_workflow_overview(
            workflow_summary=workflow_summary,
            optimization_summary=optimization_summary if isinstance(optimization_summary, dict) else {},
            baseline_summary=baseline_summary if isinstance(baseline_summary, dict) else {},
            prepared_workflow=prepared_workflow,
            load_kind=load_kind,
        )
        overview_name = OPTIMIZATION_OVERVIEW_ARTIFACT_NAMES[load_kind]
        overview_path = prepared_workflow.output_dir / overview_name
        self._write_json_config(overview_path, overview)
        overview_artifact = self._register_json_file_artifact(
            name=overview_name,
            path=overview_path,
            fallback_preview={'path': str(overview_path), 'status': 'MISSING'},
        )
        csv_artifacts = self._register_inquiry_csv_artifacts(prepared_workflow.output_dir)
        catalog_artifact = self._register_result_catalog(prepared_workflow.output_dir)
        manifest_artifact = self._register_real_output_manifest(prepared_workflow.output_dir)
        source_artifacts = [
            workflow_artifact,
            optimization_artifact,
            baseline_artifact,
            overview_artifact,
            design_set_artifact,
            training_dataset_artifact,
            *csv_artifacts,
            catalog_artifact,
            manifest_artifact,
        ]
        metric_workbooks = self.ensure_optimization_metric_workbooks(
            run_id=prepared_workflow.output_dir.name,
            artifact_ids=[artifact.artifact_id for artifact in source_artifacts],
        )
        return [
            *source_artifacts,
            *metric_workbooks,
        ]

    def _register_real_optimization_datasets(
        self,
        *,
        run_dir: Path,
        optimization_summary: dict[str, Any],
        execution_evidence: dict[str, Any],
        prepared_workflow: PreparedEarthquakeWorkflow,
    ) -> tuple[Artifact, Artifact]:
        """登记 DOE 设计集和真实求解训练集，保留可重算来源。"""

        records = [
            record
            for record in (optimization_summary.get('doe_designs') or [])
            if isinstance(record, dict)
        ]
        active_learning_records = [
            record
            for record in (optimization_summary.get('active_learning_records') or [])
            if isinstance(record, dict)
        ]
        if len(records) != prepared_workflow.requested_doe_count:
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'REAL_DOE_DATASET_INCOMPLETE',
                    'message': '真实 DOE 设计数与审批冻结的初始数量不一致。',
                },
            )
        active_learning_status = optimization_summary.get('active_learning_status') or {}
        try:
            reported_active_learning_count = int(active_learning_status.get('record_count') or 0)
        except (AttributeError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'REAL_ACTIVE_LEARNING_DATASET_INCOMPLETE',
                    'message': '主动学习补点数量证据无效。',
                },
            ) from exc
        if reported_active_learning_count != len(active_learning_records):
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'REAL_ACTIVE_LEARNING_DATASET_INCOMPLETE',
                    'message': '主动学习补点记录数与执行摘要不一致。',
                },
            )
        if any(
            not isinstance(record.get('analysis_results'), list)
            or not record.get('analysis_results')
            or any(
                not isinstance(result, dict)
                or result.get('status') != 'completed'
                or not result.get('case_id')
                for result in record.get('analysis_results') or []
            )
            for record in (*records, *active_learning_records)
        ):
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'REAL_DOE_DATASET_UNVERIFIED',
                    'message': '真实训练集包含缺失、失败或未登记 case 结果。',
                },
            )
        design_set = {
            'schemaVersion': '1.0',
            'datasetType': 'REAL_DOE_DESIGN_SET',
            'runId': run_dir.name,
            'solver': prepared_workflow.solver,
            'scenario': 'EARTHQUAKE',
            'requestedInitialDoeCount': execution_evidence.get('requestedInitialDoeCount'),
            'actualInitialDoeCount': execution_evidence.get('actualInitialDoeCount'),
            'activeLearningAddedCount': execution_evidence.get('activeLearningAddedCount'),
            'designSetSha256': execution_evidence.get('designSetSha256'),
            'designs': [record.get('design_parameters') or {} for record in records],
        }
        training_rows = _real_training_rows(records, active_learning_records)
        training_dataset = {
            'schemaVersion': '1.0',
            'datasetType': 'REAL_DOE_TRAINING',
            'runId': run_dir.name,
            'solver': prepared_workflow.solver,
            'scenario': 'EARTHQUAKE',
            'source': 'REAL_SOLVER_RESULT',
            'designSetSha256': execution_evidence.get('designSetSha256'),
            'trainingDatasetSha256': execution_evidence.get('trainingDatasetSha256'),
            'trainingSampleCount': execution_evidence.get('trainingSampleCount'),
            'rows': training_rows,
        }
        design_path = run_dir / 'real_design_set.json'
        training_path = run_dir / 'real_training_dataset.json'
        self._write_json_config(design_path, design_set)
        self._write_json_config(training_path, training_dataset)
        return (
            self._register_json_file_artifact(
                name='real_design_set.json',
                path=design_path,
                fallback_preview=design_set,
            ),
            self._register_json_file_artifact(
                name='real_training_dataset.json',
                path=training_path,
                fallback_preview=training_dataset,
            ),
        )

    def _optimization_execution_evidence(
        self,
        *,
        optimization_summary_path: Path,
        prepared_workflow: PreparedEarthquakeWorkflow,
    ) -> dict[str, Any]:
        """从真实优化摘要生成可重算的 DOE/训练数据证据。"""

        optimization_summary: dict[str, Any] = {}
        if optimization_summary_path.is_file():
            optimization_summary = self._load_json_config(optimization_summary_path)
        doe_records = [
            record
            for record in (optimization_summary.get('doe_designs') or [])
            if isinstance(record, dict)
        ]
        active_learning_records = [
            record
            for record in (optimization_summary.get('active_learning_records') or [])
            if isinstance(record, dict)
        ]
        active_learning_added = len(active_learning_records)
        training_rows = _real_training_rows(doe_records, active_learning_records)
        training_dataset_sha = (
            sha256(
                json.dumps(
                    training_rows,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(',', ':'),
                    allow_nan=False,
                ).encode('utf-8')
            ).hexdigest()
            if training_rows
            else None
        )
        actual_initial = len(doe_records)
        return {
            'requestedInitialDoeCount': prepared_workflow.requested_doe_count,
            'actualInitialDoeCount': actual_initial,
            'activeLearningAddedCount': active_learning_added,
            'realSolveCount': actual_initial + DOE_FIXED_REAL_SOLVE_OVERHEAD + active_learning_added,
            'designSetSha256': prepared_workflow.doe_design_sha256,
            'trainingDatasetSha256': training_dataset_sha,
            'trainingSampleCount': len(training_rows),
        }

    def _prepare_earthquake_baseline_optimization_workflow(
        self,
        source_workflow_config_path: Path,
        params: dict[str, Any],
        *,
        job_id: str | None = None,
    ) -> PreparedEarthquakeWorkflow:
        solver = str(params.get('solver') or 'ANSYS').upper()
        load_kind = str(params.get('loadKind') or params.get('scenario') or 'EARTHQUAKE').upper()
        run_dir = EARTHQUAKE_WORKFLOW_OUTPUT_ROOT / gen_id(f'{solver.lower()}_{load_kind.lower()}')
        run_dir.mkdir(parents=True, exist_ok=True)
        source_dir = source_workflow_config_path.parent
        source_workflow = self._load_json_config(source_workflow_config_path)
        baseline_source = self._resolve_config_path(source_workflow['baseline_config'], source_dir)
        optimization_source = self._resolve_config_path(source_workflow['optimization_config'], source_dir)
        source_optimization_config = self._load_json_config(optimization_source)
        budget = dict(params.get('budget') or {})
        requested_doe_count = int(
            budget.get('doeDesignCount')
            or params.get('doeDesignCount')
            or DOE_INITIAL_DEFAULT
        )
        if not DOE_INITIAL_MIN <= requested_doe_count <= DOE_INITIAL_MAX:
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'INVALID_DOE_DESIGN_COUNT',
                    'message': f'DOE 初始设计数必须在 {DOE_INITIAL_MIN}–{DOE_INITIAL_MAX} 之间。',
                },
            )
        baseline_config = self._earthquake_baseline_config(
            self._load_json_config(baseline_source),
            baseline_source.parent,
            run_dir,
            solver,
            load_kind,
        )
        configured_doe_designs = source_optimization_config.get('doe_designs')
        doe_designs = (
            [
                {
                    name: float(design[name])
                    for name in EARTHQUAKE_DOE_BOUNDS
                }
                for design in configured_doe_designs
            ]
            if configured_doe_designs and len(configured_doe_designs) == requested_doe_count
            else self._earthquake_doe_designs(
                int(params.get('doeSeed') or 20260705),
                requested_doe_count,
            )
        )
        doe_design_sha256 = design_set_sha256(doe_designs)
        # 单目标工况（风、车流）各有自己的配置构造器：目标集、累计位移取数节点和
        # 荷载注入方式都不同，不能共用地震的多目标构造器。
        optimization_config_builders = {
            'WIND': self._wind_optimization_config,
            'TRAFFIC': self._traffic_optimization_config,
        }
        optimization_config = optimization_config_builders.get(
            load_kind, self._earthquake_optimization_config,
        )(
            source_optimization_config,
            optimization_source.parent,
            run_dir,
            solver,
            doe_designs,
        )
        # 基线和 DOE 属于同一求解阶段，但分别维护组件计数，允许两者并行。
        progress_dir = self._begin_job_progress(job_id)
        if progress_dir is not None:
            # 基线与 DOE 共用同一个进度目录：基线是整单优化的第一段真实求解，
            # 不上报的话前端在这段时间里只能看到心跳。
            baseline_config['progress_dir'] = str(progress_dir)
            optimization_config['progress_dir'] = str(progress_dir)
        layout_evidence = self._apply_agent_damper_layout(optimization_config, params)
        if load_kind == 'WIND':
            custom_load_evidence = self._apply_agent_standard_wind_load(
                baseline_config,
                run_dir,
                params,
                solver=solver,
                optimization_config=optimization_config,
            )
        elif load_kind == 'TRAFFIC':
            # 车流的逐节点 mapping 要同时写进基线和优化配置，否则 DOE 阶段
            # 的车流会退回模板默认节点。
            custom_load_evidence = self._apply_agent_standard_traffic_load(
                baseline_config,
                run_dir,
                params,
                optimization_config=optimization_config,
            )
        else:
            custom_load_evidence = self._apply_agent_standard_earthquake_load(
                baseline_config,
                optimization_config,
                run_dir,
                params,
            )
        baseline_config_path = run_dir / baseline_source.name
        optimization_config_path = run_dir / optimization_source.name
        workflow_config_path = run_dir / source_workflow_config_path.name
        # 基线约束集按荷载类型反查。风与车流都是单目标，约束集与目标集相同；
        # 地震 joint 链两者不等（4 个 objective_specs、3 个 baseline limits），
        # 所以这里查的是"受约束的目标"这一集合，未登记工况回落地震。
        limit_targets = RESPONSE_TARGETS_BY_LOAD_KIND.get(
            load_kind, EARTHQUAKE_RESPONSE_TARGETS,
        )
        workflow_config = {
            'baseline_config': baseline_config_path.name,
            'optimization_config': optimization_config_path.name,
            'baseline_objective_limits': {
                'scenario': load_kind.lower(),
                'objectives': [target['objective'] for target in limit_targets],
            },
            'summary_path': str(run_dir / 'workflow_summary.json'),
            'metadata': {
                'source_workflow_config_path': str(source_workflow_config_path),
                'scenario': load_kind,
                'run_mode': 'REAL_BASELINE_OPTIMIZATION',
                'custom_load_evidence': custom_load_evidence,
                'damper_layout_evidence': layout_evidence,
                'requested_initial_doe_count': requested_doe_count,
                'actual_initial_doe_count': len(doe_designs),
                'doe_design_sha256': doe_design_sha256,
            },
        }
        self._write_json_config(baseline_config_path, baseline_config)
        self._write_json_config(optimization_config_path, optimization_config)
        self._write_json_config(workflow_config_path, workflow_config)
        solver_parallel = dict(optimization_config.get('parallel') or {'enabled': False, 'max_workers': 1})
        return PreparedEarthquakeWorkflow(
            workflow_config_path=workflow_config_path,
            baseline_config_path=baseline_config_path,
            optimization_config_path=optimization_config_path,
            source_workflow_config_path=source_workflow_config_path,
            output_dir=run_dir,
            doe_designs=doe_designs,
            requested_doe_count=requested_doe_count,
            doe_design_sha256=doe_design_sha256,
            solver=solver,
            solver_parallel=solver_parallel,
        )

    @staticmethod
    def _is_real_agent_analysis_request(job_type: JobType, params: dict[str, Any]) -> bool:
        return job_type == 'SOLVER_BATCH' and params.get('runMode') == 'REAL_AGENT_ANALYSIS'

    def _reject_unimplemented_standalone_capability(
        self,
        job_type: JobType,
        params: dict[str, Any],
    ) -> None:
        if job_type not in STANDALONE_DEMO_JOB_TYPES:
            return
        capability = real_execution_registry.resolve(job_type, params)
        if capability.status == 'LIVE':
            return
        if platform_execution_mode() != 'LIVE' and job_type in {
            'SOLVER_BATCH',
            'SURROGATE_TRAINING',
            'ACTIVE_LEARNING',
        }:
            # 这三个独立高风险入口无论模式都必须失败关闭。
            pass
        elif platform_execution_mode() != 'LIVE':
            return
        raise HTTPException(
            status_code=501,
            detail={
                'code': 'CAPABILITY_NOT_IMPLEMENTED',
                'message': capability.reason,
                'details': {
                    'jobType': job_type,
                    'status': capability.status,
                    'unlockRequirements': list(capability.unlock_requirements),
                },
            },
        )

    def _mark_standalone_mock_demo(
        self,
        job_type: JobType,
        params: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        capability = real_execution_registry.resolve(job_type, params)
        if capability.status == 'LIVE' or job_type not in STANDALONE_DEMO_JOB_TYPES:
            return result
        cleaned = {
            key: value
            for key, value in result.items()
            if value != 'PENDING_REPLACEMENT'
            and key not in {'realExecution', 'realSolverExecution', 'realFemExecution'}
        }
        return {**cleaned, **mock_demo_fields()}

    def _label_standalone_mock_artifacts(
        self,
        job_type: JobType,
        params: dict[str, Any],
        artifacts: list[Artifact],
    ) -> None:
        capability = real_execution_registry.resolve(job_type, params)
        if capability.status == 'LIVE' or job_type not in STANDALONE_DEMO_JOB_TYPES:
            return
        for artifact in artifacts:
            record = self.get_artifact(artifact.artifact_id)
            if isinstance(record.preview, dict):
                record.preview = {
                    **{
                        key: value
                        for key, value in record.preview.items()
                        if value != 'PENDING_REPLACEMENT'
                        and key not in {'realExecution', 'realSolverExecution', 'realFemExecution'}
                    },
                    **mock_demo_fields(),
                }
            if record.content in PLACEHOLDER_MODEL_BYTES:
                record.content = b'mock-surrogate-model'

    @staticmethod
    def _is_real_damper_comparison_request(job_type: JobType, params: dict[str, Any]) -> bool:
        cases = list(params.get('cases') or [])
        scenario = str(params.get('scenario') or '')
        solver = str(params.get('solver') or '').upper()
        # 放行面由已登记的 ANALYSIS 模板决定（下一行的成员检查），不再另钉
        # solver == 'ANSYS'：两处白名单会各自漂移，而模板表才是真实依据。
        return (
            job_type == 'SOLVER_BATCH'
            and params.get('runMode') == 'REAL_DAMPER_COMPARISON'
            and solver in (AGENT_COMPARISON_CONFIGS_BY_LOAD_KIND.get(scenario) or {})
            and len(cases) in {2, 3}
            and len({case.get('damperType') for case in cases}) == len(cases)
        )

    def _generate_real_damper_comparison_artifacts(
        self,
        params: dict[str, Any],
        *,
        job_id: str | None = None,
    ) -> list[Artifact]:
        solver = str(params.get('solver') or 'ANSYS').upper()
        load_kind = str(params.get('loadKind') or params.get('scenario') or 'EARTHQUAKE').upper()
        registered_templates = AGENT_COMPARISON_CONFIGS_BY_LOAD_KIND.get(load_kind) or {}
        if solver not in registered_templates:
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'UNSUPPORTED_REAL_COMPARISON_LOAD_KIND',
                    'message': f'{load_kind} 方案比选暂不支持求解器 {solver}',
                },
            )
        source_path = (TEMPLATE_ROOT / registered_templates[solver]).resolve()
        run_dir = EARTHQUAKE_WORKFLOW_OUTPUT_ROOT / gen_id(f'{solver.lower()}_damper_comparison')
        run_dir.mkdir(parents=True, exist_ok=True)
        timeout = params.get('executionTimeoutS')
        cases = list(params.get('cases') or [])
        # 完成计数覆盖所有串行算例；ANSYS 的单点百分比由 ansys.out 探针补充，
        # OpenSeesPy 由自己的 case_step_progress 补充。
        progress_dir = self._begin_job_progress(job_id, total_cases=len(cases))
        case_summaries = []
        artifacts: list[Artifact] = []
        for case in cases:
            case_id = str(case['caseId'])
            case_dir = run_dir / case_id
            case_dir.mkdir(parents=True, exist_ok=True)
            config = self._earthquake_baseline_config(
                self._load_json_config(source_path),
                source_path.parent,
                case_dir,
                solver,
                load_kind,
            )
            if progress_dir is not None:
                config['progress_dir'] = str(progress_dir)
            if load_kind == 'WIND':
                load_evidence = self._apply_agent_standard_wind_load(
                    config, case_dir, params, solver=solver,
                )
            elif load_kind == 'TRAFFIC':
                # 荷载按 case_dir 逐案例落盘，与风同一理由：多案例共用 run_dir
                # 下同名文件会互相覆盖。车流矩阵比风的单列文件更大，更要隔离。
                load_evidence = self._apply_agent_standard_traffic_load(
                    config, case_dir, params,
                )
            else:
                load_evidence = self._apply_agent_standard_earthquake_load(
                    config,
                    {'load_cases': [{'name': 'earthquake'}]},
                    case_dir,
                    params,
                )
            solver_kwargs = dict(config.get('solver_kwargs') or {})
            # 等峰值对比由 N 制 forceCapN 反算 C，case 参数已经是求解器单位，
            # 两个求解器都不再按界面工程单位换算，所以 damper_c_scale 固定为 1。
            solver_kwargs.update({
                'damper_module': case['solverModule'],
                'damper_c_scale': 1.0,
                'omit_dampers': False,
                'physical_count_per_tower': int((params.get('selectedLayout') or {}).get('physicalCountPerTower') or 2),
            })
            if solver == 'ANSYS':
                # nproc 只在 MAPDL 上有意义；solver_kwargs 直接展开进求解器构造函数，
                # 传给 OpenSeesPy 会 TypeError。
                solver_kwargs['nproc'] = 1
            else:
                # 进度文件按业务 caseId 命名，否则多个算例写同一个求解器自建 id。
                solver_kwargs['progress_case_id'] = case_id
            config['solver_kwargs'] = solver_kwargs
            config['damper_params'] = self._comparison_solver_params(case)
            config['summary_path'] = str(case_dir / 'case_summary.json')
            config_path = case_dir / source_path.name
            self._write_json_config(config_path, config)
            analysis = self._run_real_damper_comparison_case(
                config_path,
                execution_timeout_s=None if timeout is None else float(timeout),
            )
            verified = self._is_verified_real_case_output(analysis)
            case_summary = {
                'caseId': case_id,
                'damperType': case['damperType'],
                'solverModule': case['solverModule'],
                'parameters': case['parameters'],
                'theoreticalPeakForceN': case['theoreticalPeakForceN'],
                'designVelocityMps': case['designVelocityMps'],
                'parameterSource': case['parameterSource'],
                'status': analysis.get('status'),
                'isVerifiedSolverOutput': verified,
                'objectives': analysis.get('objectives') or {},
                'solverCaseId': analysis.get('case_id'),
                'executionUsage': analysis.get('executionUsage'),
                'customLoadEvidence': load_evidence,
            }
            case_summary_path = case_dir / 'real_case_summary.json'
            self._write_json_config(case_summary_path, case_summary)
            case_summaries.append(case_summary)
            artifacts.append(self._register_json_file_artifact(
                name=f'real_{case_id}_case_summary.json',
                path=case_summary_path,
                fallback_preview={'status': 'MISSING', 'path': str(case_summary_path)},
            ))
            command_path_value = ((analysis.get('metadata') or {}).get('command_stream') or {}).get('path')
            if command_path_value:
                command_path = Path(str(command_path_value)).resolve()
                if command_path.is_file() and command_path.is_relative_to(run_dir.resolve()):
                    command_content = command_path.read_bytes()
                    # 后缀取实际落盘文件：ANSYS 是 .apdl，OpenSeesPy 是 .py。
                    # 写死 .apdl 会让 OpenSeesPy 的制品名与内容语言不符。
                    artifacts.append(self._register_artifact(
                        kind='COMMAND_STREAM',
                        # ANSYS 落 APDL，OpenSeesPy 落 Python 命令流，扩展名不能写死。
                        name=f'{case_id}_executed_command_stream{COMPARISON_COMMAND_STREAM_SUFFIX[solver]}',
                        path=self._repo_display_path(command_path),
                        mime_type='text/plain; charset=utf-8',
                        preview={
                            'phase': 'EXECUTED',
                            'damperType': case['damperType'],
                            'solverModule': case['solverModule'],
                            'path': str(command_path),
                        },
                        content=command_content,
                    ))
            artifacts.extend(self._register_inquiry_csv_artifacts(case_dir))
            if progress_dir is not None:
                write_batch_progress(progress_dir, completed=len(case_summaries), total=len(cases))
        comparison = self._compare_case_objectives(case_summaries)
        summary = {
            'mode': 'real_damper_comparison',
            'runMode': 'REAL_DAMPER_COMPARISON',
            'solver': solver,
            'loadKind': load_kind,
            'comparisonBasis': params.get('comparisonBasis'),
            'forceCapN': params.get('forceCapN'),
            'forceCapScope': params.get('forceCapScope'),
            'designVelocityMps': params.get('designVelocityMps'),
            'selectedLayoutId': params.get('selectedLayoutId'),
            'selectedLayout': params.get('selectedLayout'),
            'responseIds': params.get('responseIds') or [],
            'caseResults': case_summaries,
            'responseComparison': comparison,
            'allVerifiedExecution': all(case['isVerifiedSolverOutput'] for case in case_summaries),
            'solverVersionProfile': params.get('solverVersionProfile'),
            'damperCalibrationProfiles': params.get('damperCalibrationProfiles') or [],
            'inputProvenance': params.get('inputProvenance') or [],
            'executionUsage': {
                'realSolveCount': len(case_summaries),
                'cases': [case.get('executionUsage') for case in case_summaries],
            },
        }
        summary_path = run_dir / 'real_damper_comparison_summary.json'
        self._write_json_config(summary_path, summary)
        artifacts.insert(0, self._register_json_file_artifact(
            name='real_damper_comparison_summary.json',
            path=summary_path,
            fallback_preview={'status': 'MISSING', 'path': str(summary_path)},
        ))
        artifacts.append(self._register_result_catalog(run_dir))
        artifacts.append(self._register_real_output_manifest(run_dir))
        return artifacts

    @staticmethod
    def _validate_real_damper_comparison_params(params: dict[str, Any]) -> None:
        # 模块名按求解器取：OpenSeesPy 的黏滞模块是 damper_viscous，写死 ANSYS
        # 的 damper_user300_viscous 会让每个 OpenSeesPy 比选都 422。
        solver = str(params.get('solver') or 'ANSYS').upper()
        expected_modules = {
            damper_type: (spec['solverModules'] or {}).get(solver)
            for damper_type, spec in DAMPER_TYPES.items()
        }
        if params.get('comparisonBasis') != 'EQUAL_PEAK_FORCE':
            raise HTTPException(
                status_code=422,
                detail={'code': 'INVALID_COMPARISON_BASIS', 'message': '只支持 EQUAL_PEAK_FORCE 对比'},
            )
        if params.get('forceCapScope') != 'PER_PHYSICAL_DAMPER':
            raise HTTPException(
                status_code=422,
                detail={'code': 'INVALID_FORCE_CAP_SCOPE', 'message': '最大出力必须按每个物理阻尼器冻结'},
            )
        force_cap = float(params.get('forceCapN') or 0)
        design_velocity = float(params.get('designVelocityMps') or 0)
        if force_cap <= 0 or design_velocity <= 0:
            raise HTTPException(
                status_code=422,
                detail={'code': 'INVALID_COMPARISON_PROFILE', 'message': '最大出力和设计速度必须为正数'},
            )
        for case in params.get('cases') or []:
            damper_type = str(case.get('damperType') or '')
            expected_module = (DAMPER_SOLVER_MODULES.get(damper_type) or {}).get(solver)
            if expected_module is None or expected_module != case.get('solverModule'):
                raise HTTPException(
                    status_code=422,
                    detail={
                        'code': 'INVALID_DAMPER_MODULE',
                        'message': f'{damper_type} 的 {solver} 阻尼器模块不匹配',
                    },
                )
            if (
                float(case.get('theoreticalPeakForceN') or 0) != force_cap
                or float(case.get('designVelocityMps') or 0) != design_velocity
                or case.get('parameterSource') != 'VERIFIED_TEMPLATE'
            ):
                raise HTTPException(
                    status_code=422,
                    detail={'code': 'UNFROZEN_COMPARISON_CASE', 'message': f'{damper_type} 参数未绑定审批剖面'},
                )
        expected_layout = {
            'nodePairs': [[36, 517], [36, 518], [107, 520], [107, 521]],
            'direction': 'X',
            'physicalCountPerTower': 2,
        }
        if params.get('selectedLayoutId') != 'TWO_PER_TOWER' or params.get('selectedLayout') != expected_layout:
            raise HTTPException(
                status_code=422,
                detail={'code': 'UNREGISTERED_DAMPER_LAYOUT', 'message': '双工况对比仅支持登记的 TWO_PER_TOWER 布置'},
            )

    @staticmethod
    def _comparison_solver_params(case: dict[str, Any]) -> dict[str, float]:
        parameters = case['parameters']
        if case['damperType'] == 'VISCOUS':
            return {
                'c': float(parameters['c']),
                'alpha': float(parameters['alpha']),
                'regularization_velocity': float(parameters['vfloor']),
            }
        if case['damperType'] == 'EDDY_CURRENT':
            return {'c': float(parameters['fmax']), 'alpha': float(parameters['vcr'])}
        if case['damperType'] == 'FRICTION':
            return {'c': float(parameters['fc']), 'alpha': float(parameters['vs'])}
        raise HTTPException(
            status_code=422,
            detail={'code': 'UNSUPPORTED_DAMPER_TYPE', 'message': str(case.get('damperType'))},
        )

    @staticmethod
    def _compare_case_objectives(case_summaries: list[dict[str, Any]]) -> dict[str, Any]:
        """对比全部工况共有的数值指标。

        两工况时保持既有字段（secondMinusFirst/relativeToFirst），
        三工况在同一结构上追加 pairwise 两两配对，历史消费方
        （relativeToFirst 百分比投影、追问目录）无需感知工况数。
        """
        if len(case_summaries) < 2:
            return {}
        values_by_case: list[tuple[str, dict[str, float]]] = []
        common: set[str] | None = None
        for case in case_summaries:
            numeric = {
                str(name): float(value)
                for name, value in (case.get('objectives') or {}).items()
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            }
            values_by_case.append((str(case['caseId']), numeric))
            common = set(numeric) if common is None else common & set(numeric)
        first_id, first_values = values_by_case[0]
        second_id, second_values = values_by_case[1]
        metrics: dict[str, dict[str, Any]] = {}
        for metric in sorted(common or set()):
            entry: dict[str, Any] = {
                case_id: values[metric] for case_id, values in values_by_case
            }
            difference = second_values[metric] - first_values[metric]
            entry['secondMinusFirst'] = difference
            entry['relativeToFirst'] = (
                None if first_values[metric] == 0 else difference / abs(first_values[metric])
            )
            entry['pairwise'] = [
                {
                    'baseCaseId': base_id,
                    'otherCaseId': other_id,
                    'difference': other_values[metric] - base_values[metric],
                    'relativeChange': (
                        None
                        if base_values[metric] == 0
                        else (other_values[metric] - base_values[metric]) / abs(base_values[metric])
                    ),
                }
                for position, (base_id, base_values) in enumerate(values_by_case)
                for other_id, other_values in values_by_case[position + 1:]
            ]
            metrics[metric] = entry
        return {
            'firstCaseId': first_id,
            'secondCaseId': second_id,
            'caseIds': [case_id for case_id, _ in values_by_case],
            'metrics': metrics,
        }

    def _generate_real_agent_analysis_artifacts(
        self,
        params: dict[str, Any],
        *,
        job_id: str | None = None,
    ) -> list[Artifact]:
        solver = str(params.get('solver') or 'ANSYS').upper()
        load_kind = str(params.get('loadKind') or 'EARTHQUAKE').upper()
        registered_templates = AGENT_ANALYSIS_CONFIGS_BY_LOAD_KIND.get(load_kind)
        if registered_templates is None:
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'UNSUPPORTED_REAL_ANALYSIS_LOAD_KIND',
                    'message': f'真实分析暂不支持荷载类型 {load_kind}',
                },
            )
        try:
            template_name = registered_templates[solver]
        except KeyError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'UNSUPPORTED_REAL_ANALYSIS_SOLVER',
                    'message': f'{load_kind} 真实分析暂不支持求解器 {solver}',
                },
            ) from exc
        source_path = (TEMPLATE_ROOT / template_name).resolve()
        run_dir = EARTHQUAKE_WORKFLOW_OUTPUT_ROOT / gen_id(f'{solver.lower()}_analysis')
        run_dir.mkdir(parents=True, exist_ok=True)
        config = self._earthquake_baseline_config(
            self._load_json_config(source_path),
            source_path.parent,
            run_dir,
            solver,
            load_kind,
        )
        progress_dir = self._begin_job_progress(job_id, total_cases=1)
        if progress_dir is not None:
            config['progress_dir'] = str(progress_dir)
        if load_kind == 'WIND':
            load_evidence = self._apply_agent_standard_wind_load(config, run_dir, params, solver)
        elif load_kind == 'TRAFFIC':
            load_evidence = self._apply_agent_standard_traffic_load(config, run_dir, params)
        else:
            optimization_stub = {'load_cases': [{'name': 'earthquake'}]}
            load_evidence = self._apply_agent_standard_earthquake_load(
                config, optimization_stub, run_dir, params,
            )
        model_evidence = self._apply_agent_custom_fem_model(config, run_dir, params, solver)
        response_evidence = self._apply_agent_response_outputs(
            config,
            params,
            solver,
            custom_model=model_evidence is not None,
        )
        config['summary_path'] = str(run_dir / 'analysis_summary.json')
        config_path = run_dir / source_path.name
        self._write_json_config(config_path, config)
        timeout = ((params.get('resources') or {}).get('executionTimeoutS'))
        analysis = self._run_real_agent_analysis(
            config_path,
            execution_timeout_s=None if timeout is None else float(timeout),
        )
        if progress_dir is not None:
            write_batch_progress(progress_dir, completed=1, total=1)
        summary = {
            **analysis,
            'mode': 'real_agent_analysis',
            'runMode': 'REAL_AGENT_ANALYSIS',
            'solver': solver,
            'sourceConfigPath': str(source_path),
            'preparedConfigPath': str(config_path),
            'customLoadEvidence': load_evidence,
            'customModelEvidence': model_evidence,
            'responseOutputEvidence': response_evidence,
            'responseIds': params.get('responseIds') or [],
            'solverVersionProfile': params.get('solverVersionProfile'),
            'inputProvenance': params.get('inputProvenance') or [],
        }
        summary_path = Path(config['summary_path'])
        self._write_json_config(summary_path, summary)
        summary_artifact = self._register_json_file_artifact(
            name='real_analysis_summary.json',
            path=summary_path,
            fallback_preview={'status': 'MISSING', 'path': str(summary_path)},
        )
        overview = {
            'mode': 'real_agent_analysis',
            'solver': solver,
            'status': summary.get('status'),
            'customLoadEvidence': load_evidence,
            'customModelEvidence': model_evidence,
            'responseOutputEvidence': response_evidence,
            'responseIds': params.get('responseIds') or [],
            'solverVersionProfile': params.get('solverVersionProfile'),
            'inputProvenance': params.get('inputProvenance') or [],
            'executionUsage': summary.get('executionUsage'),
            'summaryArtifactId': summary_artifact.artifact_id,
            'summarySha256': summary_artifact.sha256,
        }
        overview_path = run_dir / 'real_analysis_overview.json'
        self._write_json_config(overview_path, overview)
        overview_artifact = self._register_json_file_artifact(
            name='real_analysis_overview.json',
            path=overview_path,
            fallback_preview={'status': 'MISSING', 'path': str(overview_path)},
        )
        csv_artifacts = self._register_inquiry_csv_artifacts(run_dir)
        catalog_artifact = self._register_result_catalog(run_dir)
        manifest_artifact = self._register_real_output_manifest(run_dir)
        return [summary_artifact, overview_artifact, *csv_artifacts, catalog_artifact, manifest_artifact]

    @staticmethod
    def _apply_agent_damper_layout(
        optimization_config: dict[str, Any],
        params: dict[str, Any],
    ) -> dict[str, Any] | None:
        layout_id = params.get('selectedLayoutId')
        if not layout_id:
            return None
        layout = dict(params.get('selectedLayout') or {})
        expected = {
            'ONE_PER_TOWER': {'nodePairs': [[36, 518], [107, 521]], 'direction': 'X', 'physicalCountPerTower': 1},
            'TWO_PER_TOWER': {
                'nodePairs': [[36, 517], [36, 518], [107, 520], [107, 521]],
                'direction': 'X',
                'physicalCountPerTower': 2,
            },
        }
        if layout_id not in expected or layout != expected[layout_id]:
            raise HTTPException(
                status_code=422,
                detail={'code': 'UNREGISTERED_DAMPER_LAYOUT', 'message': '阻尼器布置不在 STbridge 受控候选表中'},
            )
        solver_config = optimization_config.get('solver')
        if isinstance(solver_config, dict):
            solver_config['physical_count_per_tower'] = layout['physicalCountPerTower']
        else:
            solver_kwargs = dict(optimization_config.get('solver_kwargs') or {})
            solver_kwargs['physical_count_per_tower'] = layout['physicalCountPerTower']
            optimization_config['solver_kwargs'] = solver_kwargs
        return {'layoutId': layout_id, **layout}

    def _apply_agent_standard_earthquake_load(
        self,
        baseline_config: dict[str, Any],
        optimization_config: dict[str, Any],
        run_dir: Path,
        params: dict[str, Any],
    ) -> dict[str, Any] | None:
        artifact_id = params.get('loadDatasetArtifactId')
        if not artifact_id:
            return None
        record = self.get_artifact(str(artifact_id))
        expected_sha256 = str(params.get('loadDatasetSha256') or '')
        if not expected_sha256 or record.artifact.sha256 != expected_sha256:
            raise HTTPException(
                status_code=409,
                detail={'code': 'LOAD_ARTIFACT_HASH_MISMATCH', 'message': '标准荷载 Artifact 的 SHA256 与审批冻结值不一致'},
            )
        rows = list(csv.DictReader(record.content.decode('utf-8-sig').splitlines()))
        if not rows:
            raise HTTPException(
                status_code=422,
                detail={'code': 'EMPTY_STANDARD_LOAD', 'message': '标准荷载 Artifact 不包含数据行'},
            )
        channel_ids = {row.get('channel_id') for row in rows}
        contract_valid = (
            len(channel_ids) == 1
            and all(row.get('load_kind') == 'EARTHQUAKE' for row in rows)
            and all(row.get('application_type') == 'UNIFORM_EXCITATION' for row in rows)
            and all(row.get('quantity') == 'ACCELERATION' for row in rows)
            and all(row.get('unit') == 'm/s2' for row in rows)
        )
        if not contract_valid:
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'UNSUPPORTED_STANDARD_EARTHQUAKE_LOAD',
                    'message': '真实地震优化首期仅接受一个 m/s2 UNIFORM_EXCITATION 加速度通道',
                },
            )
        try:
            times = [float(row['time_s']) for row in rows]
            values = [float(row['value']) for row in rows]
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=422,
                detail={'code': 'INVALID_STANDARD_LOAD', 'message': '标准荷载时间或数值字段无效'},
            ) from exc
        if len(times) < 2 or any(current <= previous for previous, current in zip(times, times[1:])):
            raise HTTPException(
                status_code=422,
                detail={'code': 'INVALID_STANDARD_LOAD_TIME', 'message': '标准荷载至少需要两个严格递增的时间点'},
            )
        dt = times[1] - times[0]
        tolerance = max(abs(dt), 1.0) * 1.0e-9
        if any(abs((current - previous) - dt) > tolerance for previous, current in zip(times, times[1:])):
            raise HTTPException(
                status_code=422,
                detail={'code': 'NON_UNIFORM_STANDARD_LOAD_TIME', 'message': '真实地震优化要求等时间步标准荷载'},
            )
        solver_load_path = run_dir / 'agent_earthquake_acceleration_mps2.txt'
        solver_load_path.write_text('\n'.join(format(value, '.15g') for value in values) + '\n', encoding='utf-8')
        evidence = {
            'artifactId': record.artifact.artifact_id,
            'sha256': record.artifact.sha256,
            'solverInputPath': str(solver_load_path),
            'solverInputSha256': sha256(solver_load_path.read_bytes()).hexdigest(),
            'sampleCount': len(values),
            'timeStepS': dt,
            'durationS': times[-1] - times[0],
            'component': rows[0].get('component'),
            'unit': 'm/s2',
        }
        load_case = {
            'name': 'earthquake',
            'load_type': 'earthquake',
            'path': str(solver_load_path),
            'scale': 1.0,
            'dt': dt,
            'duration': evidence['durationS'],
            'metadata': {'agent_standard_load': evidence},
        }
        baseline_config['load_case'] = dict(load_case)
        optimization_config['load_cases'] = [
            dict(load_case) if item.get('name') == 'earthquake' else item
            for item in optimization_config.get('load_cases') or []
        ]
        return evidence

    def _apply_agent_standard_wind_load(
        self,
        baseline_config: dict[str, Any],
        run_dir: Path,
        params: dict[str, Any],
        solver: str,
        optimization_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """把审批冻结的标准风荷载制品绑定到求解配置和登记目标节点集。

        风荷载没有项目内置默认记录：缺少登记制品或施加对象时必须失败关闭，
        不允许回退到模板路径或求解器默认节点。
        """
        artifact_id = params.get('loadDatasetArtifactId')
        if not artifact_id:
            raise HTTPException(status_code=422, detail={
                'code': 'WIND_LOAD_ARTIFACT_REQUIRED',
                'message': '风荷载分析必须引用审批冻结的标准荷载 Artifact',
            })
        target_set_id = str(params.get('loadTargetSetId') or '')
        target_nodes = AGENT_LOAD_TARGET_SETS.get(target_set_id)
        if not target_nodes:
            raise HTTPException(status_code=422, detail={
                'code': 'WIND_LOAD_TARGET_REQUIRED',
                'message': '风荷载分析必须冻结已登记的施加目标节点集',
            })
        record = self.get_artifact(str(artifact_id))
        expected_sha256 = str(params.get('loadDatasetSha256') or '')
        if not expected_sha256 or record.artifact.sha256 != expected_sha256:
            raise HTTPException(status_code=409, detail={
                'code': 'LOAD_ARTIFACT_HASH_MISMATCH',
                'message': '标准荷载 Artifact 的 SHA256 与审批冻结值不一致',
            })
        rows = list(csv.DictReader(record.content.decode('utf-8-sig').splitlines()))
        if not rows:
            raise HTTPException(status_code=422, detail={
                'code': 'EMPTY_STANDARD_LOAD',
                'message': '标准荷载 Artifact 不包含数据行',
            })
        channel_ids = list(dict.fromkeys(row.get('channel_id') for row in rows))
        # 两种放行形态：单通道＝目标集总力按等权分配到各节点；通道数等于目标节点数
        # ＝逐节点独立力时程，第 i 个通道对应目标集第 i 个节点。其余通道数无法判定
        # 通道与节点的对应关系，必须失败关闭，否则就是"求解成功但荷载装错节点"。
        expected_channel_ids = [f'channel_{index}' for index in range(1, len(target_nodes) + 1)]
        per_node_channels = len(channel_ids) > 1 and channel_ids == expected_channel_ids
        if not (
            (len(channel_ids) == 1 or per_node_channels)
            and all(row.get('load_kind') == 'WIND' for row in rows)
            and all(row.get('application_type') == 'NODAL_FORCE' for row in rows)
            and all(row.get('quantity') == 'FORCE' for row in rows)
            and all(row.get('unit') == 'N' for row in rows)
            and all(row.get('component') == AGENT_WIND_FORCE_COMPONENT for row in rows)
        ):
            raise HTTPException(status_code=422, detail={
                'code': 'UNSUPPORTED_STANDARD_WIND_LOAD',
                'message': (
                    f'真实风荷载分析接受 N 单位、{AGENT_WIND_FORCE_COMPONENT} 方向的 '
                    'NODAL_FORCE 力通道：一个目标集总力通道，或与目标节点数 '
                    f'{len(target_nodes)} 相等且按 channel_1..channel_'
                    f'{len(target_nodes)} 命名的逐节点通道'
                ),
            })
        if not all(
            row.get('target_type') == 'NODE_GROUP' and row.get('target_id') == target_set_id
            for row in rows
        ):
            raise HTTPException(status_code=422, detail={
                'code': 'WIND_LOAD_TARGET_MISMATCH',
                'message': f'标准荷载的施加对象与冻结目标集 {target_set_id} 不一致',
            })
        try:
            # 标准制品按通道分段排列（同一通道的全部时刻连续），逐通道取值后
            # 时间轴取第一个通道的时刻序列。
            channel_values: dict[str, list[float]] = {channel_id: [] for channel_id in channel_ids}
            channel_times: dict[str, list[float]] = {channel_id: [] for channel_id in channel_ids}
            for row in rows:
                channel_values[row['channel_id']].append(float(row['value']))
                channel_times[row['channel_id']].append(float(row['time_s']))
            times = channel_times[channel_ids[0]]
            values = channel_values[channel_ids[0]]
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail={
                'code': 'INVALID_STANDARD_LOAD',
                'message': '标准荷载时间或数值字段无效',
            }) from exc
        if any(channel_times[channel_id] != times for channel_id in channel_ids):
            raise HTTPException(status_code=422, detail={
                'code': 'INVALID_STANDARD_LOAD',
                'message': '逐节点风荷载各通道必须共用同一条时间轴',
            })
        if len(times) < 2 or any(current <= previous for previous, current in zip(times, times[1:])):
            raise HTTPException(status_code=422, detail={
                'code': 'INVALID_STANDARD_LOAD_TIME',
                'message': '标准荷载至少需要两个严格递增的时间点',
            })
        dt = times[1] - times[0]
        tolerance = max(abs(dt), 1.0) * 1.0e-9
        if any(abs((current - previous) - dt) > tolerance for previous, current in zip(times, times[1:])):
            raise HTTPException(status_code=422, detail={
                'code': 'NON_UNIFORM_STANDARD_LOAD_TIME',
                'message': '真实风荷载分析要求等时间步标准荷载',
            })
        solver_load_path = run_dir / 'agent_wind_nodal_force_n.txt'
        if per_node_channels:
            # 逐节点形态写成"时间列 + 每节点一列"的矩阵：两侧求解器都按
            # 首两行时间递增识别并剥掉时间列，因此 source_column 是力列的 1 基序号。
            solver_load_path.write_text(
                '\n'.join(
                    ','.join(
                        format(cell, '.15g')
                        for cell in (
                            time_value,
                            *(channel_values[channel_id][index] for channel_id in channel_ids),
                        )
                    )
                    for index, time_value in enumerate(times)
                )
                + '\n',
                encoding='utf-8',
            )
        else:
            solver_load_path.write_text(
                '\n'.join(format(value, '.15g') for value in values) + '\n',
                encoding='utf-8',
            )
        evidence = {
            'artifactId': record.artifact.artifact_id,
            'sha256': record.artifact.sha256,
            'solverInputPath': str(solver_load_path),
            'solverInputSha256': sha256(solver_load_path.read_bytes()).hexdigest(),
            'sampleCount': len(values),
            'timeStepS': dt,
            'durationS': times[-1] - times[0],
            'component': AGENT_WIND_FORCE_COMPONENT,
            'unit': 'N',
            'targetSetId': target_set_id,
            'targetNodes': list(target_nodes),
            'distribution': (
                # 逐节点形态每列整量施加到对应节点，没有权重分配可言；
                # 单通道形态才是"目标集总力按等权分配"。
                'PER_NODE_INDEPENDENT_FORCE_COLUMNS'
                if per_node_channels
                else 'EQUAL_WEIGHT_OVER_TARGET_NODES'
            ),
        }
        if per_node_channels:
            evidence['channelCount'] = len(channel_ids)
        elif str(solver).upper() == 'OPENSEESPY_INPROC':
            # 逐节点权重只对 OpenSees 的等权分配有意义，且要写进 evidence 供审查核对。
            evidence['nodeWeight'] = 1.0 / len(target_nodes)
        wind_load_case = {
            'name': 'wind',
            'load_type': 'wind',
            'path': str(solver_load_path),
            'scale': 1.0,
            'dt': dt,
            'duration': evidence['durationS'],
            'metadata': {'agent_standard_load': evidence},
        }
        baseline_config['bridge_model'] = self._wind_bridge_model_with_target_nodes(
            baseline_config.get('bridge_model'),
            target_nodes,
            solver,
            per_node_channels,
        )
        baseline_config['load_case'] = {
            **dict(baseline_config.get('load_case') or {}),
            **wind_load_case,
        }
        if optimization_config is not None:
            # 优化配置用 load_cases 列表 + active_load_cases，与基线的单工况字段不同；
            # 目标节点集同样要在这里恢复，否则 DOE 阶段的风荷载会落到求解器默认节点。
            optimization_config['bridge_model'] = self._wind_bridge_model_with_target_nodes(
                optimization_config.get('bridge_model'),
                target_nodes,
                solver,
                per_node_channels,
            )
            existing_wind_case = next(
                (
                    dict(item)
                    for item in optimization_config.get('load_cases') or []
                    if item.get('name') == 'wind'
                ),
                {},
            )
            optimization_config['load_cases'] = [{**existing_wind_case, **wind_load_case}]
            optimization_config['active_load_cases'] = ['wind']
        return evidence

    @staticmethod
    def _wind_bridge_model_with_target_nodes(
        raw_config: Any,
        target_nodes: list[int],
        solver: str = 'ANSYS',
        per_node_columns: bool = False,
    ) -> dict[str, Any]:
        """把审批冻结的风荷载目标节点集写回 bridge_model metadata。

        `_earthquake_baseline_config` 会过滤掉所有含 wind 字样的 metadata 键，
        风工况必须在荷载绑定阶段重新写入，否则 ANSYS 会退回求解器默认节点。

        `per_node_columns` 为真时荷载文件是逐节点力矩阵，第 i 个目标节点取第 i 列
        整量施加；为假时荷载文件只有一列总力，按等权重分配到各目标节点。
        """
        bridge_model = dict(raw_config or {})
        metadata = dict(bridge_model.get('metadata') or {})
        metadata['wind_girder_load_nodes'] = list(target_nodes)
        # 命名 component 会让 ANSYS 渲染跳过冻结的目标节点集，必须移除。
        metadata.pop('wind_load_component', None)
        if per_node_columns:
            # 逐节点列必须给两个求解器都写 mapping：ANSYS 的 _wind_load_points 只在
            # 读到 wind_load_mappings 时才走逐列渲染，否则回落到 _equal_weight_points
            # ——每个节点取第 1 列的 1/N，即整体欠载 N 倍，且没有任何字段能看出来。
            metadata['wind_load_nodes'] = list(target_nodes)
            metadata['wind_load_mappings'] = [
                {
                    'fem_node_id': int(node),
                    'dof': OPENSEES_WIND_FORCE_DOF,
                    'scale': 1.0,
                    'source_column': index,
                    'group': 'girder',
                    'label': f'girder_{index}',
                }
                for index, node in enumerate(target_nodes, start=1)
            ]
        elif str(solver).upper() == 'OPENSEESPY_INPROC':
            # OpenSees 风模块不读 wind_girder_load_nodes，也不做等权分配：没有逐节点
            # mapping 时它会退化成对每个目标节点施加整个目标集的总力（wind.pyfrag
            # 的 else 分支），得到的是 len(target_nodes) 倍超载，而且量纲上看不出错。
            # 这里显式写出每节点权重，与 ANSYS 的 _equal_weight_points 口径一致。
            weight = 1.0 / len(target_nodes)
            metadata['wind_load_nodes'] = list(target_nodes)
            metadata['wind_load_mappings'] = [
                {
                    'fem_node_id': int(node),
                    'dof': OPENSEES_WIND_FORCE_DOF,
                    'scale': weight,
                    # 求解侧荷载文件只有一列总力，全部 mapping 共用该列。
                    'source_column': 1,
                    'group': 'girder',
                    'label': f'girder_{index}',
                }
                for index, node in enumerate(target_nodes, start=1)
            ]
        bridge_model['metadata'] = metadata
        return bridge_model

    def _apply_agent_standard_traffic_load(
        self,
        baseline_config: dict[str, Any],
        run_dir: Path,
        params: dict[str, Any],
        optimization_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """把审批冻结的标准交通荷载制品绑定到求解配置和登记目标节点集。

        交通荷载没有项目内置默认记录：缺少登记制品或施加对象时必须失败关闭，
        不允许回退到模板路径或求解器默认节点。
        """
        artifact_id = params.get('loadDatasetArtifactId')
        if not artifact_id:
            raise HTTPException(status_code=422, detail={
                'code': 'TRAFFIC_LOAD_ARTIFACT_REQUIRED',
                'message': '交通荷载分析必须引用审批冻结的标准荷载 Artifact',
            })
        target_set_id = str(params.get('loadTargetSetId') or '')
        target_nodes = AGENT_LOAD_TARGET_SETS.get(target_set_id)
        if not target_nodes:
            raise HTTPException(status_code=422, detail={
                'code': 'TRAFFIC_LOAD_TARGET_REQUIRED',
                'message': '交通荷载分析必须冻结已登记的施加目标节点集',
            })
        record = self.get_artifact(str(artifact_id))
        expected_sha256 = str(params.get('loadDatasetSha256') or '')
        if not expected_sha256 or record.artifact.sha256 != expected_sha256:
            raise HTTPException(status_code=409, detail={
                'code': 'LOAD_ARTIFACT_HASH_MISMATCH',
                'message': '标准荷载 Artifact 的 SHA256 与审批冻结值不一致',
            })
        mapping_artifact_id = params.get('loadPointMappingArtifactId')
        if not mapping_artifact_id:
            raise HTTPException(status_code=422, detail={
                'code': 'TRAFFIC_LOAD_POINT_MAPPING_REQUIRED',
                'message': '车流荷载分析必须引用审批冻结的逐节点 mapping 制品',
            })
        mapping_record = self.get_artifact(str(mapping_artifact_id))
        expected_mapping_sha = str(params.get('loadPointMappingSha256') or '')
        if not expected_mapping_sha or mapping_record.artifact.sha256 != expected_mapping_sha:
            raise HTTPException(status_code=409, detail={
                'code': 'LOAD_POINT_MAPPING_HASH_MISMATCH',
                'message': '逐节点 mapping 制品的 SHA256 与审批冻结值不一致',
            })
        try:
            node_mappings = json.loads(mapping_record.content.decode('utf-8-sig'))
        except (UnicodeDecodeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail={
                'code': 'INVALID_TRAFFIC_POINT_MAPPING',
                'message': '逐节点 mapping 制品不是合法 JSON',
            }) from exc
        if not isinstance(node_mappings, list) or not node_mappings:
            raise HTTPException(status_code=422, detail={
                'code': 'INVALID_TRAFFIC_POINT_MAPPING',
                'message': '逐节点 mapping 制品必须是非空数组',
            })
        # 稠密矩阵：第一列是 time_s，其余每列是一个节点的独立力时程。
        reader = csv.reader(record.content.decode('utf-8-sig').splitlines())
        matrix_rows = [row for row in reader if row and any(cell.strip() for cell in row)]
        if len(matrix_rows) < 2:
            raise HTTPException(status_code=422, detail={
                'code': 'EMPTY_STANDARD_LOAD',
                'message': '标准荷载 Artifact 不包含数据行',
            })
        header = [cell.strip() for cell in matrix_rows[0]]
        if header[0] != 'time_s' or len(header) < 2:
            raise HTTPException(status_code=422, detail={
                'code': 'UNSUPPORTED_STANDARD_TRAFFIC_LOAD',
                'message': '车流标准荷载首列必须是 time_s，且至少有一个节点力列',
            })
        node_columns = header[1:]
        # mapping 与矩阵列必须严格双射：列错位在量纲上完全看不出来，
        # 只能在绑定阶段钉死，不允许按列号顺序假定。
        if len(node_mappings) != len(node_columns):
            raise HTTPException(status_code=422, detail={
                'code': 'TRAFFIC_LOAD_POINT_MAPPING_MISMATCH',
                'message': 'mapping 条目数与矩阵节点列数不一致',
            })
        try:
            mapped_nodes = [int(item['fem_node_id']) for item in node_mappings]
            mapped_columns = [int(item['source_column']) for item in node_mappings]
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail={
                'code': 'INVALID_TRAFFIC_POINT_MAPPING',
                'message': 'mapping 条目缺少 fem_node_id 或 source_column',
            }) from exc
        if mapped_columns != list(range(1, len(node_columns) + 1)):
            raise HTTPException(status_code=422, detail={
                'code': 'TRAFFIC_LOAD_POINT_MAPPING_MISMATCH',
                'message': 'mapping 的 source_column 必须与矩阵列号一一对应',
            })
        if any(
            column != f'node_{node}_fy_N'
            for node, column in zip(mapped_nodes, node_columns)
        ):
            raise HTTPException(status_code=422, detail={
                'code': 'TRAFFIC_LOAD_POINT_MAPPING_MISMATCH',
                'message': 'mapping 的节点顺序与矩阵列名不一致',
            })
        if sorted(mapped_nodes) != sorted(int(node) for node in target_nodes):
            raise HTTPException(status_code=422, detail={
                'code': 'TRAFFIC_LOAD_TARGET_MISMATCH',
                'message': f'mapping 的节点集合与冻结目标集 {target_set_id} 不一致',
            })
        try:
            times = [float(row[0]) for row in matrix_rows[1:]]
        except (IndexError, ValueError) as exc:
            raise HTTPException(status_code=422, detail={
                'code': 'INVALID_STANDARD_LOAD',
                'message': '标准荷载时间字段无效',
            }) from exc
        if len(times) < 2 or any(current <= previous for previous, current in zip(times, times[1:])):
            raise HTTPException(status_code=422, detail={
                'code': 'INVALID_STANDARD_LOAD_TIME',
                'message': '标准荷载至少需要两个严格递增的时间点',
            })
        dt = times[1] - times[0]
        tolerance = max(abs(dt), 1.0) * 1.0e-9
        if any(abs((current - previous) - dt) > tolerance for previous, current in zip(times, times[1:])):
            raise HTTPException(status_code=422, detail={
                'code': 'NON_UNIFORM_STANDARD_LOAD_TIME',
                'message': '真实车流荷载分析要求等时间步标准荷载',
            })
        for row_index, row in enumerate(matrix_rows[1:], start=2):
            if len(row) != len(header):
                raise HTTPException(status_code=422, detail={
                    'code': 'INVALID_STANDARD_LOAD',
                    'message': f'标准荷载第 {row_index} 行列数与表头不一致',
                })
        try:
            value_rows = [[float(cell) for cell in row[1:]] for row in matrix_rows[1:]]
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={
                'code': 'INVALID_STANDARD_LOAD',
                'message': '标准荷载包含非数值节点力',
            }) from exc
        # 求解侧输入保持矩阵形态：ANSYS 的 *VREAD 按列取数，OpenSees 的逐节点
        # mapping 也按 source_column 取列。写成单列会丢掉空间分布。
        solver_load_path = run_dir / 'agent_traffic_nodal_force_matrix_n.csv'
        with solver_load_path.open('w', encoding='utf-8', newline='') as handle:
            writer = csv.writer(handle, lineterminator='\n')
            writer.writerow(header)
            for time_value, values in zip(times, value_rows):
                writer.writerow([
                    format(time_value, '.15g'),
                    *(format(value, '.15g') for value in values),
                ])
        peak_total_force_n = max(
            (abs(sum(values)) for values in value_rows),
            default=0.0,
        )
        evidence = {
            'artifactId': record.artifact.artifact_id,
            'sha256': record.artifact.sha256,
            'pointMappingArtifactId': mapping_record.artifact.artifact_id,
            'pointMappingSha256': mapping_record.artifact.sha256,
            'solverInputPath': str(solver_load_path),
            'solverInputSha256': sha256(solver_load_path.read_bytes()).hexdigest(),
            'sampleCount': len(times),
            'timeStepS': dt,
            'durationS': times[-1] - times[0],
            'component': AGENT_TRAFFIC_FORCE_COMPONENT,
            'unit': 'N',
            'targetSetId': target_set_id,
            'targetNodes': list(mapped_nodes),
            'nodeCount': len(mapped_nodes),
            'peakTotalForceN': peak_total_force_n,
            # 逐节点独立时程，求解侧不得再做等权分配或求和。
            'distribution': 'PER_NODE_INDEPENDENT_TIME_HISTORY',
        }
        traffic_load_case = {
            'name': 'traffic',
            'load_type': 'traffic',
            'path': str(solver_load_path),
            'scale': 1.0,
            'dt': dt,
            'duration': evidence['durationS'],
            'metadata': {'agent_standard_load': evidence},
        }
        baseline_config['bridge_model'] = self._traffic_bridge_model_with_mappings(
            baseline_config.get('bridge_model'),
            mapped_nodes,
            mapped_columns,
        )
        baseline_config['load_case'] = {
            **dict(baseline_config.get('load_case') or {}),
            **traffic_load_case,
        }
        if optimization_config is not None:
            # 优化配置用 load_cases 列表 + active_load_cases，与基线的单工况字段不同；
            # 逐节点 mapping 同样要在这里写入，否则 DOE 阶段的车流会退回模板默认节点。
            optimization_config['bridge_model'] = self._traffic_bridge_model_with_mappings(
                optimization_config.get('bridge_model'),
                mapped_nodes,
                mapped_columns,
            )
            existing_traffic_case = next(
                (
                    dict(item)
                    for item in optimization_config.get('load_cases') or []
                    if item.get('name') == 'traffic'
                ),
                {},
            )
            optimization_config['load_cases'] = [{**existing_traffic_case, **traffic_load_case}]
            optimization_config['active_load_cases'] = ['traffic']
        return evidence

    @staticmethod
    def _traffic_bridge_model_with_mappings(
        raw_config: Any,
        mapped_nodes: list[int],
        mapped_columns: list[int],
    ) -> dict[str, Any]:
        """把审批冻结的车流节点集与逐节点列映射写回 bridge_model metadata。

        ANSYS 侧 `_mapped_load_points` 读 `traffic_load_mappings` 的 `source_column`，
        OpenSees 侧 `traffic.pyfrag` 读同一个键，因此两个求解器共用这份声明，
        不像风那样需要按求解器分叉。
        """
        bridge_model = dict(raw_config or {})
        metadata = dict(bridge_model.get('metadata') or {})
        metadata['traffic_load_nodes'] = list(mapped_nodes)
        # 命名 component 会让 ANSYS 渲染只生成一列 TABLE 并丢弃逐节点 mapping
        # （ansys_load_rendering 的 component 分支先 return），必须移除。
        metadata.pop('traffic_load_component', None)
        # scale 固定 1.0：每列已经是该节点的实际力，不是需要再分摊的总力。
        metadata['traffic_load_mappings'] = [
            {
                'fem_node_id': int(node),
                'dof': OPENSEES_TRAFFIC_FORCE_DOF,
                'scale': 1.0,
                'source_column': int(column),
                'group': 'traffic',
                'label': f'traffic_{index}',
            }
            for index, (node, column) in enumerate(zip(mapped_nodes, mapped_columns), start=1)
        ]
        bridge_model['metadata'] = metadata
        return bridge_model

    def _apply_agent_custom_fem_model(
        self,
        baseline_config: dict[str, Any],
        run_dir: Path,
        params: dict[str, Any],
        solver: str,
    ) -> dict[str, Any] | None:
        """把审批冻结的用户 FEM 模型制品落盘并替换 bridge_model 引用。

        OpenSeesPy 路径使用 Python 模型构建器而非 APDL 文本，
        自定义模型首期仅放行 ANSYS 求解器。
        """
        artifact_id = params.get('modelArtifactId')
        if not artifact_id:
            return None
        if solver != 'ANSYS':
            raise HTTPException(status_code=422, detail={
                'code': 'CUSTOM_MODEL_SOLVER_UNSUPPORTED',
                'message': '用户上传的 FEM 模型首期仅支持 ANSYS 求解器',
            })
        record = self.get_artifact(str(artifact_id))
        if record.artifact.kind != 'FEM_MODEL':
            raise HTTPException(status_code=422, detail={
                'code': 'NOT_A_FEM_MODEL',
                'message': f'制品 {artifact_id} 不是已登记的 FEM 模型',
            })
        expected_sha256 = str(params.get('modelSha256') or '')
        if not expected_sha256 or record.artifact.sha256 != expected_sha256:
            raise HTTPException(status_code=409, detail={
                'code': 'MODEL_ARTIFACT_HASH_MISMATCH',
                'message': 'FEM 模型 Artifact 的 SHA256 与审批冻结值不一致',
            })
        model_path = run_dir / 'agent_user_fem_model.txt'
        model_path.write_bytes(record.content)
        bridge_model = dict(baseline_config.get('bridge_model') or {})
        bridge_model['name'] = f'user_fem_{re.sub(r"[^A-Za-z0-9_]", "_", str(artifact_id))[:40]}'
        bridge_model['source_path'] = str(model_path)
        baseline_config['bridge_model'] = bridge_model
        return {
            'artifactId': record.artifact.artifact_id,
            'sha256': record.artifact.sha256,
            'fileName': record.artifact.name,
            'solverInputPath': str(model_path),
            'solverInputSha256': sha256(model_path.read_bytes()).hexdigest(),
        }

    # 用户可点名输出的节点/单元数量上限：每个通道都会进 timeseries.csv，
    # 过多通道会拖垮后处理与追问链。
    _RESPONSE_OUTPUT_MAX_TARGETS = 16

    def _apply_agent_response_outputs(
        self,
        baseline_config: dict[str, Any],
        params: dict[str, Any],
        solver: str,
        *,
        custom_model: bool,
    ) -> dict[str, Any] | None:
        """按审批冻结的节点/单元清单覆盖响应提取配置。

        默认 STbridge 模型 + 仅节点输出时保留完整 ansys-dpf-rst 通道
        （塔底内力、阻尼器力等），只覆盖响应节点；自定义模型或需要
        单元输出时切换到模型无关的 ansys-dpf-nodes 轻量后处理。
        """
        nodes = [int(node) for node in params.get('responseNodes') or []]
        elements = [int(element) for element in params.get('responseElementIds') or []]
        component = int(params.get('responseComponent') or 0)
        if not nodes and not elements and not custom_model:
            return None
        if custom_model and not nodes:
            raise HTTPException(status_code=422, detail={
                'code': 'RESPONSE_NODES_REQUIRED',
                'message': '自定义模型分析必须显式指定响应节点',
            })
        for name, values in (('responseNodes', nodes), ('responseElementIds', elements)):
            if len(values) > self._RESPONSE_OUTPUT_MAX_TARGETS:
                raise HTTPException(status_code=422, detail={
                    'code': 'TOO_MANY_RESPONSE_TARGETS',
                    'message': f'{name} 最多支持 {self._RESPONSE_OUTPUT_MAX_TARGETS} 个',
                })
            if len(set(values)) != len(values) or any(value <= 0 for value in values):
                raise HTTPException(status_code=422, detail={
                    'code': 'INVALID_RESPONSE_TARGETS',
                    'message': f'{name} 必须是不重复的正整数',
                })
        if component not in {0, 1, 2}:
            raise HTTPException(status_code=422, detail={
                'code': 'INVALID_RESPONSE_COMPONENT',
                'message': 'responseComponent 必须是 0(X)/1(Y)/2(Z)',
            })
        if solver == 'ANSYS':
            solver_kwargs = dict(baseline_config.get('solver_kwargs') or {})
            postprocessor = dict(solver_kwargs.get('postprocessor') or {})
            if custom_model or elements:
                postprocessor = {
                    'mode': 'ansys-dpf-nodes',
                    'response_nodes': nodes,
                    'response_component': component,
                    'response_elements': elements,
                    **({'ansys_path': postprocessor['ansys_path']} if postprocessor.get('ansys_path') else {}),
                }
                mode = 'ansys-dpf-nodes'
            else:
                postprocessor['response_nodes'] = nodes
                postprocessor['response_component'] = component
                # 累计位移通道跟随用户点名的首个节点，避免默认节点缺失时报错。
                postprocessor['cumulative_displacement_node'] = nodes[0]
                mode = str(postprocessor.get('mode') or 'ansys-dpf-rst')
            solver_kwargs['postprocessor'] = postprocessor
            baseline_config['solver_kwargs'] = solver_kwargs
        else:
            solver_config = dict(baseline_config.get('solver') or {})
            if nodes:
                solver_config['response_nodes'] = nodes
                # OpenSees 的 DOF 从 1 开始编号。
                solver_config['response_dof'] = component + 1
            if elements:
                solver_config['response_elements'] = elements
                # 单元内力取全局分量下标（0/1/2），与 DOF 编号不同，不加一。
                solver_config['response_element_component'] = component
            baseline_config['solver'] = solver_config
            mode = (
                'openseespy_inproc_nodes_elements' if elements else 'openseespy_inproc_nodes'
            )
        return {
            'responseNodes': nodes,
            'responseElementIds': elements,
            'responseComponent': component,
            'postprocessorMode': mode,
        }

    def _earthquake_baseline_config(
        self,
        config: dict[str, Any],
        config_dir: Path,
        output_dir: Path,
        solver: str,
        load_kind: str = 'EARTHQUAKE',
    ) -> dict[str, Any]:
        updated = dict(config)
        updated['bridge_model'] = self._earthquake_bridge_model_config(updated.get('bridge_model'), config_dir)
        load_case = dict(updated.get('load_case') or {})
        updated['load_case'] = self._config_with_resolved_paths(load_case, config_dir, ('path',))
        # 无控基线摘要按荷载类型命名，与 OPTIMIZATION_OVERVIEW_ARTIFACT_NAMES 同一口径：
        # 风工况的基线结论不应写进名为 earthquake 的文件里，否则下游读取方和证据
        # 审查会把风的结果当成地震的。读取方都按 baseline_summary_path 动态取值；
        # 唯一按字面名扫描的 sync_real_optimization_history 只扫 *_earthquake_* 目录。
        updated['summary_path'] = str(
            output_dir / UNDAMPED_BASELINE_SUMMARY_NAMES.get(
                str(load_kind or 'EARTHQUAKE').upper(),
                UNDAMPED_BASELINE_SUMMARY_NAMES['EARTHQUAKE'],
            )
        )
        if solver == 'ANSYS':
            solver_kwargs = dict(updated.get('solver_kwargs') or {})
            solver_kwargs['output_dir'] = str(output_dir / 'solver_outputs')
            solver_kwargs['damper_module'] = 'damper_user300_viscous'
            solver_kwargs['nproc'] = 1
            updated['solver_kwargs'] = solver_kwargs
        else:
            solver_config = dict(updated.get('solver') or {})
            solver_config['type'] = 'openseespy_inproc'
            solver_config['output_dir'] = str(output_dir / 'solver_outputs')
            if solver_config.get('model_path'):
                solver_config['model_path'] = str(self._resolve_config_path(solver_config['model_path'], config_dir))
            updated['solver'] = solver_config
        return updated

    def _earthquake_optimization_config(
        self,
        config: dict[str, Any],
        config_dir: Path,
        output_dir: Path,
        solver: str,
        doe_designs: list[dict[str, float]],
    ) -> dict[str, Any]:
        updated = dict(config)
        include_operation = solver == 'ANSYS'
        updated['bridge_model'] = (
            self._joint_bridge_model_config(updated.get('bridge_model'), config_dir)
            if include_operation
            else self._earthquake_bridge_model_config(updated.get('bridge_model'), config_dir)
        )
        earthquake_case = self._earthquake_load_case(updated, config_dir)
        updated['load_cases'] = (
            [earthquake_case, self._operation_load_case(updated, config_dir)]
            if include_operation
            else [earthquake_case]
        )
        updated.pop('load_combinations', None)
        updated['active_load_cases'] = ['earthquake', 'operation'] if include_operation else ['earthquake']
        updated['objective_specs'] = [
            {'scenario': 'earthquake', 'objective': target['objective'], 'weight': 1.0}
            for target in EARTHQUAKE_RESPONSE_TARGETS
        ]
        if include_operation:
            for item in updated['objective_specs']:
                item['weight'] = 0.65
            updated['objective_specs'].append(
                {'scenario': 'operation', 'objective': 'cumulative_displacement', 'weight': 0.35}
            )
        # 基线指标约束属于联合工作流的优化后处理，不进入 DOE 配置。
        updated.pop('baseline_objective_limits', None)
        updated['bounds'] = {name: list(values) for name, values in EARTHQUAKE_DOE_BOUNDS.items()}
        updated['steps'] = dict(EARTHQUAKE_OPTIMIZATION_STEPS)
        updated['n_doe_samples'] = len(doe_designs)
        updated['doe_designs'] = doe_designs
        # DOE 与无控基线并行，各自写入独立进度组件；读取方再聚合总量。
        updated['progress_completed_offset'] = 0
        updated['progress_total_cases'] = len(doe_designs)
        updated['progress_component'] = 'doe'
        updated['n_candidates'] = EARTHQUAKE_CANDIDATE_COUNT
        updated['surrogate_cv'] = EARTHQUAKE_SURROGATE_CV
        updated.pop('max_normalized_doe_distance', None)
        # 独立验证点用于检查代理模型与真实 FEM 的偏差，OpenSeesPy 同样支持。
        # 工况差异只影响 operation 载荷，不应关闭地震验证流程。
        updated['run_validation'] = True
        updated.pop('min_validation_r2', None)
        updated['n_validation_points'] = 2
        updated['max_validation_designs'] = 2
        updated['max_validation_peak_relative_error'] = EARTHQUAKE_REVIEW_RELATIVE_ERROR_LIMIT
        updated.pop('min_surrogate_r2', None)
        updated['max_active_learning_iterations'] = EARTHQUAKE_MAX_ACTIVE_LEARNING_ITERATIONS
        updated['run_review'] = True
        updated['review_reuse_doe_results'] = False
        updated['review_relative_error_limit'] = EARTHQUAKE_REVIEW_RELATIVE_ERROR_LIMIT
        updated['max_review_iterations'] = EARTHQUAKE_MAX_REVIEW_ITERATIONS
        updated['output_dir'] = str(output_dir)
        updated['summary_path'] = str(output_dir / 'optimization_summary.json')
        if solver == 'ANSYS':
            updated['solver'] = 'ansys'
            solver_kwargs = dict(updated.get('solver_kwargs') or {})
            damper_calibration = dict(solver_kwargs.get('damper_calibration') or {})
            if damper_calibration.get('artifact_path'):
                damper_calibration['artifact_path'] = str(
                    self._resolve_config_path(damper_calibration['artifact_path'], config_dir)
                )
                solver_kwargs['damper_calibration'] = damper_calibration
            solver_kwargs['damper_module'] = 'damper_user300_viscous'
            solver_kwargs['nproc'] = 1
            solver_kwargs['mapdl_memory_mb'] = min(int(solver_kwargs.get('mapdl_memory_mb') or 64), 64)
            solver_kwargs['mapdl_args'] = self._ansys_parallel_mapdl_args(solver_kwargs.get('mapdl_args'))
            postprocessor = dict(solver_kwargs.get('postprocessor') or {})
            postprocessor['cumulative_displacement_node'] = 107
            solver_kwargs['postprocessor'] = postprocessor
            updated['solver_kwargs'] = solver_kwargs
            updated['parallel'] = {'enabled': True, 'max_workers': 4, 'license_limit': 4, 'mode': 'thread'}
        else:
            solver_config = dict(updated.get('solver') or {})
            solver_config['type'] = 'openseespy_inproc'
            damper_calibration = dict(solver_config.get('damper_calibration') or {})
            if damper_calibration.get('artifact_path'):
                damper_calibration['artifact_path'] = str(
                    self._resolve_config_path(damper_calibration['artifact_path'], config_dir)
                )
                solver_config['damper_calibration'] = damper_calibration
            if solver_config.get('model_path'):
                solver_config['model_path'] = str(self._resolve_config_path(solver_config['model_path'], config_dir))
            updated['solver'] = solver_config
            updated['parallel'] = {'enabled': True, 'max_workers': 4, 'license_limit': 4, 'mode': 'process'}
        return updated

    def _wind_optimization_config(
        self,
        config: dict[str, Any],
        config_dir: Path,
        output_dir: Path,
        solver: str,
        doe_designs: list[dict[str, float]],
    ) -> dict[str, Any]:
        """构造风工况单目标优化配置：唯一目标是梁端 X 向累计位移最小。

        风荷载工况路径由审批冻结制品在 `_apply_agent_standard_wind_load`
        里注入，这里只固化目标、设计空间和求解器参数。

        放行的求解器由 OPTIMIZATION_WORKFLOW_CONFIGS_BY_LOAD_KIND['WIND'] 的登记
        模板决定，不在这里另立白名单：两处不一致会让已登记模板在装配阶段被拒。
        """
        registered_solvers = OPTIMIZATION_WORKFLOW_CONFIGS_BY_LOAD_KIND.get('WIND') or {}
        if solver not in registered_solvers:
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'UNSUPPORTED_REAL_WORKFLOW_SOLVER',
                    'message': f'WIND 真实 baseline-first optimization 暂不支持求解器 {solver}',
                },
            )
        updated = dict(config)
        # 风工况的目标节点集属于 wind_* metadata，不能过 _earthquake_bridge_model_config
        # 的过滤器；这里保留原样，由荷载绑定阶段写入冻结节点。
        updated['bridge_model'] = self._joint_bridge_model_config(updated.get('bridge_model'), config_dir)
        updated['load_cases'] = [self._wind_load_case(updated, config_dir)]
        updated.pop('load_combinations', None)
        updated['active_load_cases'] = ['wind']
        updated['objective_specs'] = [
            {'scenario': 'wind', 'objective': target['objective'], 'weight': 1.0}
            for target in WIND_RESPONSE_TARGETS
        ]
        # 基线指标约束属于工作流的优化后处理，不进入 DOE 配置。
        updated.pop('baseline_objective_limits', None)
        updated['bounds'] = {name: list(values) for name, values in EARTHQUAKE_DOE_BOUNDS.items()}
        updated['steps'] = dict(EARTHQUAKE_OPTIMIZATION_STEPS)
        updated['n_doe_samples'] = len(doe_designs)
        updated['doe_designs'] = doe_designs
        updated['progress_completed_offset'] = 0
        updated['progress_total_cases'] = len(doe_designs)
        updated['progress_component'] = 'doe'
        updated['n_candidates'] = EARTHQUAKE_CANDIDATE_COUNT
        updated['surrogate_cv'] = EARTHQUAKE_SURROGATE_CV
        updated.pop('max_normalized_doe_distance', None)
        updated['run_validation'] = True
        updated.pop('min_validation_r2', None)
        updated['n_validation_points'] = 2
        updated['max_validation_designs'] = 2
        updated['max_validation_peak_relative_error'] = EARTHQUAKE_REVIEW_RELATIVE_ERROR_LIMIT
        updated.pop('min_surrogate_r2', None)
        updated['max_active_learning_iterations'] = EARTHQUAKE_MAX_ACTIVE_LEARNING_ITERATIONS
        updated['run_review'] = True
        updated['review_reuse_doe_results'] = False
        updated['review_relative_error_limit'] = EARTHQUAKE_REVIEW_RELATIVE_ERROR_LIMIT
        updated['max_review_iterations'] = EARTHQUAKE_MAX_REVIEW_ITERATIONS
        updated['output_dir'] = str(output_dir)
        updated['summary_path'] = str(output_dir / 'optimization_summary.json')
        if solver == 'ANSYS':
            updated['solver'] = 'ansys'
            solver_kwargs = dict(updated.get('solver_kwargs') or {})
            damper_calibration = dict(solver_kwargs.get('damper_calibration') or {})
            if damper_calibration.get('artifact_path'):
                damper_calibration['artifact_path'] = str(
                    self._resolve_config_path(damper_calibration['artifact_path'], config_dir)
                )
                solver_kwargs['damper_calibration'] = damper_calibration
            solver_kwargs['damper_module'] = 'damper_user300_viscous'
            # 优化要求真实阻尼器参与求解；风基线模板的 omit_dampers 只适用于无控基线。
            solver_kwargs['omit_dampers'] = False
            solver_kwargs['nproc'] = 1
            solver_kwargs['mapdl_memory_mb'] = min(int(solver_kwargs.get('mapdl_memory_mb') or 64), 64)
            solver_kwargs['mapdl_args'] = self._ansys_parallel_mapdl_args(solver_kwargs.get('mapdl_args'))
            postprocessor = dict(solver_kwargs.get('postprocessor') or {})
            # 累计位移固定取梁端节点的 X 向行程；风荷载竖向施加，这个组合不随工况改变。
            postprocessor['cumulative_displacement_node'] = WIND_CUMULATIVE_DISPLACEMENT_NODE
            postprocessor['response_component'] = 0
            solver_kwargs['postprocessor'] = postprocessor
            updated['solver_kwargs'] = solver_kwargs
            updated['parallel'] = {'enabled': True, 'max_workers': 4, 'license_limit': 4, 'mode': 'thread'}
        else:
            solver_config = dict(updated.get('solver') or {})
            solver_config['type'] = 'openseespy_inproc'
            damper_calibration = dict(solver_config.get('damper_calibration') or {})
            if damper_calibration.get('artifact_path'):
                damper_calibration['artifact_path'] = str(
                    self._resolve_config_path(damper_calibration['artifact_path'], config_dir)
                )
                solver_config['damper_calibration'] = damper_calibration
            if solver_config.get('model_path'):
                solver_config['model_path'] = str(
                    self._resolve_config_path(solver_config['model_path'], config_dir)
                )
            # 优化要求真实阻尼器参与求解；风基线模板的 omit_dampers 只适用于无控基线。
            solver_config['omit_dampers'] = False
            # OpenSees 的 opensees-csv 后处理没有 cumulative_displacement_node 选择器：
            # displacement 列是 response_nodes 的逐步 max-abs 包络。要与 ANSYS 固定取
            # 107 号节点行程的定义一致，这里必须把响应节点收窄为单节点。
            solver_config['response_nodes'] = [WIND_CUMULATIVE_DISPLACEMENT_NODE]
            updated['solver'] = solver_config
            # openseespy_inproc 不支持线程并行（config_runner 会直接 raise）。
            updated['parallel'] = {'enabled': True, 'max_workers': 4, 'license_limit': 4, 'mode': 'process'}
        return updated

    def _wind_load_case(self, config: dict[str, Any], config_dir: Path) -> dict[str, Any]:
        for item in config.get('load_cases') or []:
            if item.get('name') == 'wind' or item.get('load_type') == 'wind':
                return self._config_with_resolved_paths(dict(item), config_dir, ('path',))
        load_case = dict(config.get('load_case') or {})
        if load_case:
            return self._config_with_resolved_paths(load_case, config_dir, ('path',))
        raise HTTPException(
            status_code=422,
            detail={'code': 'INVALID_WORKFLOW_CONFIG_PATH', 'message': '风优化模板缺少 wind load case'},
        )

    def _traffic_optimization_config(
        self,
        config: dict[str, Any],
        config_dir: Path,
        output_dir: Path,
        solver: str,
        doe_designs: list[dict[str, float]],
    ) -> dict[str, Any]:
        """构造车流工况单目标优化配置：唯一目标是梁端 X 向累计位移最小。

        目标目录刻意只含累计位移：塔底剪力与弯矩不进车流优化目标，
        与 OPTIMIZATION_RESPONSE_CATALOG_BY_LOAD_KIND['TRAFFIC'] 同一口径。
        车流荷载矩阵与逐节点列映射由 `_apply_agent_standard_traffic_load`
        注入，这里只固化目标、设计空间和求解器参数。

        放行的求解器由 OPTIMIZATION_WORKFLOW_CONFIGS_BY_LOAD_KIND['TRAFFIC'] 的登记
        模板决定，不在这里另立白名单：两处不一致会让已登记模板在装配阶段被拒。
        """
        registered_solvers = OPTIMIZATION_WORKFLOW_CONFIGS_BY_LOAD_KIND.get('TRAFFIC') or {}
        if solver not in registered_solvers:
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'UNSUPPORTED_REAL_WORKFLOW_SOLVER',
                    'message': f'TRAFFIC 真实 baseline-first optimization 暂不支持求解器 {solver}',
                },
            )
        updated = dict(config)
        # 车流的目标节点集与逐节点 mapping 属于 traffic_* metadata，同样不能过
        # _earthquake_bridge_model_config 的过滤器；由荷载绑定阶段写入冻结值。
        updated['bridge_model'] = self._joint_bridge_model_config(updated.get('bridge_model'), config_dir)
        updated['load_cases'] = [self._traffic_load_case(updated, config_dir)]
        updated.pop('load_combinations', None)
        updated['active_load_cases'] = ['traffic']
        updated['objective_specs'] = [
            {'scenario': 'traffic', 'objective': target['objective'], 'weight': 1.0}
            for target in TRAFFIC_RESPONSE_TARGETS
        ]
        updated.pop('baseline_objective_limits', None)
        updated['bounds'] = {name: list(values) for name, values in EARTHQUAKE_DOE_BOUNDS.items()}
        updated['steps'] = dict(EARTHQUAKE_OPTIMIZATION_STEPS)
        updated['n_doe_samples'] = len(doe_designs)
        updated['doe_designs'] = doe_designs
        updated['progress_completed_offset'] = 0
        updated['progress_total_cases'] = len(doe_designs)
        updated['progress_component'] = 'doe'
        updated['n_candidates'] = EARTHQUAKE_CANDIDATE_COUNT
        updated['surrogate_cv'] = EARTHQUAKE_SURROGATE_CV
        updated.pop('max_normalized_doe_distance', None)
        updated['run_validation'] = True
        updated.pop('min_validation_r2', None)
        updated['n_validation_points'] = 2
        updated['max_validation_designs'] = 2
        updated['max_validation_peak_relative_error'] = EARTHQUAKE_REVIEW_RELATIVE_ERROR_LIMIT
        updated.pop('min_surrogate_r2', None)
        updated['max_active_learning_iterations'] = EARTHQUAKE_MAX_ACTIVE_LEARNING_ITERATIONS
        updated['run_review'] = True
        updated['review_reuse_doe_results'] = False
        updated['review_relative_error_limit'] = EARTHQUAKE_REVIEW_RELATIVE_ERROR_LIMIT
        updated['max_review_iterations'] = EARTHQUAKE_MAX_REVIEW_ITERATIONS
        updated['output_dir'] = str(output_dir)
        updated['summary_path'] = str(output_dir / 'optimization_summary.json')
        if solver == 'ANSYS':
            updated['solver'] = 'ansys'
            solver_kwargs = dict(updated.get('solver_kwargs') or {})
            damper_calibration = dict(solver_kwargs.get('damper_calibration') or {})
            if damper_calibration.get('artifact_path'):
                damper_calibration['artifact_path'] = str(
                    self._resolve_config_path(damper_calibration['artifact_path'], config_dir)
                )
                solver_kwargs['damper_calibration'] = damper_calibration
            solver_kwargs['damper_module'] = 'damper_user300_viscous'
            # 优化要求真实阻尼器参与求解；车流基线模板的 omit_dampers 只适用于无控基线。
            solver_kwargs['omit_dampers'] = False
            solver_kwargs['nproc'] = 1
            solver_kwargs['mapdl_memory_mb'] = min(int(solver_kwargs.get('mapdl_memory_mb') or 64), 64)
            solver_kwargs['mapdl_args'] = self._ansys_parallel_mapdl_args(solver_kwargs.get('mapdl_args'))
            postprocessor = dict(solver_kwargs.get('postprocessor') or {})
            # 累计位移固定取梁端节点 1 的 X 向行程：节点 1 的累计行程（1.2479 m）大于
            # 节点 72（1.2419 m），而优化目标就是累计位移，取更大的一侧。
            postprocessor['cumulative_displacement_node'] = TRAFFIC_CUMULATIVE_DISPLACEMENT_NODE
            postprocessor['response_component'] = 0
            solver_kwargs['postprocessor'] = postprocessor
            updated['solver_kwargs'] = solver_kwargs
            updated['parallel'] = {'enabled': True, 'max_workers': 4, 'license_limit': 4, 'mode': 'thread'}
        else:
            solver_config = dict(updated.get('solver') or {})
            solver_config['type'] = 'openseespy_inproc'
            damper_calibration = dict(solver_config.get('damper_calibration') or {})
            if damper_calibration.get('artifact_path'):
                damper_calibration['artifact_path'] = str(
                    self._resolve_config_path(damper_calibration['artifact_path'], config_dir)
                )
                solver_config['damper_calibration'] = damper_calibration
            if solver_config.get('model_path'):
                solver_config['model_path'] = str(
                    self._resolve_config_path(solver_config['model_path'], config_dir)
                )
            # 优化要求真实阻尼器参与求解；车流基线模板的 omit_dampers 只适用于无控基线。
            solver_config['omit_dampers'] = False
            # OpenSees 的 opensees-csv 后处理没有 cumulative_displacement_node 选择器：
            # displacement 列是 response_nodes 的逐步 max-abs 包络。要与 ANSYS 固定取
            # 节点 1 行程的定义一致，这里必须把响应节点收窄为单节点。
            solver_config['response_nodes'] = [TRAFFIC_CUMULATIVE_DISPLACEMENT_NODE]
            updated['solver'] = solver_config
            # openseespy_inproc 不支持线程并行（config_runner 会直接 raise）。
            updated['parallel'] = {'enabled': True, 'max_workers': 4, 'license_limit': 4, 'mode': 'process'}
        return updated

    def _traffic_load_case(self, config: dict[str, Any], config_dir: Path) -> dict[str, Any]:
        for item in config.get('load_cases') or []:
            if item.get('name') == 'traffic' or item.get('load_type') == 'traffic':
                return self._config_with_resolved_paths(dict(item), config_dir, ('path',))
        load_case = dict(config.get('load_case') or {})
        if load_case:
            return self._config_with_resolved_paths(load_case, config_dir, ('path',))
        raise HTTPException(
            status_code=422,
            detail={'code': 'INVALID_WORKFLOW_CONFIG_PATH', 'message': '车流优化模板缺少 traffic load case'},
        )

    def _earthquake_load_case(self, config: dict[str, Any], config_dir: Path) -> dict[str, Any]:
        for item in config.get('load_cases') or []:
            if item.get('name') == 'earthquake' or item.get('load_type') == 'earthquake':
                return self._config_with_resolved_paths(dict(item), config_dir, ('path',))
        load_case = dict(config.get('load_case') or {})
        if load_case:
            return self._config_with_resolved_paths(load_case, config_dir, ('path',))
        raise HTTPException(
            status_code=422,
            detail={'code': 'INVALID_WORKFLOW_CONFIG_PATH', 'message': 'baseline-first workflow 模板缺少 earthquake load case'},
        )

    def _operation_load_case(self, config: dict[str, Any], config_dir: Path) -> dict[str, Any]:
        for item in config.get('load_cases') or []:
            if item.get('name') != 'operation':
                continue
            updated = self._config_with_resolved_paths(dict(item), config_dir, ('path',))
            metadata = dict(updated.get('metadata') or {})
            calibration = dict(metadata.get('load_calibration') or {})
            if calibration.get('artifact_path'):
                calibration['artifact_path'] = str(
                    self._resolve_config_path(calibration['artifact_path'], config_dir)
                )
                metadata['load_calibration'] = calibration
            updated['metadata'] = metadata
            return updated
        raise HTTPException(
            status_code=422,
            detail={'code': 'INVALID_WORKFLOW_CONFIG_PATH', 'message': '联合优化模板缺少 operation load case'},
        )

    def _earthquake_bridge_model_config(self, raw_config: Any, config_dir: Path) -> dict[str, Any]:
        bridge_model = self._config_with_resolved_paths(dict(raw_config or {}), config_dir, ('source_path',))
        metadata = dict(bridge_model.get('metadata') or {})
        blocked_tokens = ('operation', 'wind', 'traffic')
        bridge_model['metadata'] = {
            key: value
            for key, value in metadata.items()
            if not any(token in str(key).lower() for token in blocked_tokens)
            and not (isinstance(value, str) and any(token in value.lower() for token in blocked_tokens))
        }
        return bridge_model

    def _joint_bridge_model_config(self, raw_config: Any, config_dir: Path) -> dict[str, Any]:
        bridge_model = self._config_with_resolved_paths(dict(raw_config or {}), config_dir, ('source_path',))
        metadata = dict(bridge_model.get('metadata') or {})
        for key in ('wind_load_mappings_path', 'traffic_load_mappings_path'):
            if metadata.get(key):
                metadata[key] = str(self._resolve_config_path(metadata[key], config_dir))
        bridge_model['metadata'] = metadata
        return bridge_model

    def _earthquake_doe_designs(
        self,
        seed: int,
        count: int = DOE_INITIAL_DEFAULT,
    ) -> list[dict[str, float]]:
        return generate_two_factor_doe(
            EARTHQUAKE_DOE_BOUNDS,
            count=count,
            seed=seed,
            minimum=DOE_INITIAL_MIN,
            maximum=DOE_INITIAL_MAX,
        )

    def _earthquake_workflow_overview(
        self,
        *,
        workflow_summary: dict[str, Any],
        optimization_summary: dict[str, Any],
        baseline_summary: dict[str, Any],
        prepared_workflow: PreparedEarthquakeWorkflow,
        load_kind: str = 'EARTHQUAKE',
    ) -> dict[str, Any]:
        # 风与车流都只有单一累计位移目标，也都没有 operation 联合工况；概览的响应
        # 目录和基线键前缀必须跟随荷载类型，否则会把地震的三个目标列成空值。
        is_single_objective = load_kind in {'WIND', 'TRAFFIC'}
        scenario_prefix = load_kind.lower()
        overview_targets = (
            RESPONSE_TARGETS_BY_LOAD_KIND[load_kind]
            if is_single_objective
            else (
                JOINT_RESPONSE_TARGETS
                if prepared_workflow.solver == 'ANSYS'
                else EARTHQUAKE_RESPONSE_TARGETS
            )
        )
        # 限值表只列真正受约束的目标：按全目标集渲染会多出永远为 "-" 的假限值，
        # 让审查以为存在一个并不存在的约束。
        limit_targets = RESPONSE_TARGETS_BY_LOAD_KIND.get(
            load_kind, EARTHQUAKE_RESPONSE_TARGETS,
        )
        validation_status = dict(optimization_summary.get('validation_status') or {})
        review_status = dict(optimization_summary.get('review_status') or {})
        active_learning_status = dict(optimization_summary.get('active_learning_status') or {})
        active_learning_additions = int(active_learning_status.get('record_count') or 0)
        validation_required = bool(
            validation_status.get('has_validation_records')
            or validation_status.get('has_validation_reports')
        )
        accepted = (
            (not validation_required or self._status_is_verified_and_accepted(validation_status))
            and self._status_is_verified_and_accepted(review_status)
        )
        objective_limits = optimization_summary.get('objective_limits') or workflow_summary.get('objective_limits') or {}
        optimization = optimization_summary.get('optimization') or {}
        objective_names = list(optimization.get('objective_names') or [])
        best_objectives = list(optimization.get('best_objectives') or [])
        recommended_objectives = {
            str(name).split(':', 1)[-1]: value
            for name, value in zip(objective_names, best_objectives)
        }
        baseline_objectives = {
            key: (baseline_summary.get('objectives') or {}).get(
                key,
                (baseline_summary.get('objectives') or {}).get(f'{scenario_prefix}:{key}'),
            )
            for key in recommended_objectives
            if key in (baseline_summary.get('objectives') or {})
            or f'{scenario_prefix}:{key}' in (baseline_summary.get('objectives') or {})
        }
        recommended_parameters = dict(zip(
            optimization.get('parameter_names') or [],
            optimization.get('best_design') or [],
        ))
        return {
            'mode': 'real_baseline_optimization',
            'scenario': load_kind,
            'operationIncluded': not is_single_objective and prepared_workflow.solver == 'ANSYS',
            'solver': prepared_workflow.solver,
            'workflowConfigPath': str(prepared_workflow.workflow_config_path),
            'sourceWorkflowConfigPath': str(prepared_workflow.source_workflow_config_path),
            'customLoadEvidence': workflow_summary.get('customLoadEvidence'),
            'damperLayoutEvidence': workflow_summary.get('damperLayoutEvidence'),
            'solverVersionProfile': workflow_summary.get('solverVersionProfile'),
            'inputProvenance': workflow_summary.get('inputProvenance') or [],
            'baselineSummaryPath': workflow_summary.get('baseline_summary_path'),
            'optimizationSummaryPath': workflow_summary.get('optimization_summary_path'),
            'workflowSummaryPath': workflow_summary.get('workflow_summary_path'),
            'doeContract': {
                'dampingCoefficientRange': list(EARTHQUAKE_DOE_BOUNDS['c']),
                'velocityExponentRange': list(EARTHQUAKE_DOE_BOUNDS['alpha']),
                'centerSamples': 1,
                'cornerSamples': 4,
                'lhsSamples': max(len(prepared_workflow.doe_designs) - 5, 0),
                'requestedInitialDoeCount': prepared_workflow.requested_doe_count,
                'actualInitialDoeCount': len(prepared_workflow.doe_designs),
                'activeLearningAddedCount': active_learning_additions,
                'controlledSamples': len(prepared_workflow.doe_designs),
                'uncontrolledBaselineSamples': 1,
                'totalSamples': len(prepared_workflow.doe_designs) + 1,
                'realSolveCount': (
                    len(prepared_workflow.doe_designs)
                    + DOE_FIXED_REAL_SOLVE_OVERHEAD
                    + active_learning_additions
                ),
                'designSetSha256': prepared_workflow.doe_design_sha256,
                'designs': prepared_workflow.doe_designs,
            },
            'optimizationSteps': dict(EARTHQUAKE_OPTIMIZATION_STEPS),
            'solverParallel': prepared_workflow.solver_parallel,
            'surrogateCandidates': list(EARTHQUAKE_SURROGATE_MODEL_NAMES),
            'surrogateAccuracyGate': {
                'selectionMetric': 'cross_validation_max_relative_error',
                'acceptanceMetric': 'final_fem_max_relative_error',
                'maxFinalReviewRelativeError': EARTHQUAKE_REVIEW_RELATIVE_ERROR_LIMIT,
                'r2UsedForAcceptance': False,
                'maxActiveLearningIterations': EARTHQUAKE_MAX_ACTIVE_LEARNING_ITERATIONS,
                'activeLearningBatchSize': DOE_ACTIVE_LEARNING_BATCH,
            },
            'responseTargets': [
                {key: value for key, value in target.items() if key != 'displayScale'}
                for target in overview_targets
            ],
            'objectiveLimits': self._display_objective_limits(objective_limits, limit_targets),
            'sampleResponses': self._earthquake_sample_responses(
                baseline_summary,
                optimization_summary,
                overview_targets,
            ),
            'baselineObjectives': baseline_objectives,
            'recommendedObjectives': recommended_objectives,
            'recommendedParameters': recommended_parameters,
            'surrogateMetrics': optimization_summary.get('surrogate_selections') or [],
            'surrogateCandidateMetrics': optimization_summary.get('surrogate_candidate_metrics') or {},
            'topsis': ((optimization_summary.get('optimization') or {}).get('topsis')),
            'surrogateCandidateFilter': optimization_summary.get('surrogate_candidate_filter') or {},
            'realDoeFallback': optimization_summary.get('real_doe_fallback') or {},
            'failure': {
                'stage': optimization_summary.get('failure_stage'),
                'reason': optimization_summary.get('failure_reason'),
            },
            'validationStatus': validation_status,
            'activeLearningStatus': active_learning_status,
            'reviewStatus': review_status,
            'finalRecommendationStatus': 'ACCEPTED' if accepted else 'DIAGNOSTIC_NOT_ACCEPTED',
        }

    def _earthquake_sample_responses(
        self,
        baseline_summary: dict[str, Any],
        optimization_summary: dict[str, Any],
        targets: tuple[dict[str, Any], ...] = JOINT_RESPONSE_TARGETS,
        bare_key_targets: tuple[dict[str, Any], ...] = EARTHQUAKE_RESPONSE_TARGETS,
    ) -> list[dict[str, Any]]:
        rows = []
        if baseline_summary:
            rows.append(
                {
                    'sampleType': 'UNCONTROLLED_BASELINE',
                    'caseId': baseline_summary.get('case_id'),
                    'solver': baseline_summary.get('solver'),
                    'status': baseline_summary.get('status'),
                    'design': {'c': None, 'alpha': None},
                    'responses': self._display_response_values(
                        baseline_summary.get('objectives') or {},
                        targets,
                        bare_key_targets,
                    ),
                }
            )
        for index, design in enumerate(optimization_summary.get('doe_designs') or [], start=1):
            results = list(design.get('analysis_results') or [])
            result = next(iter(results), {})
            rows.append(
                {
                    'sampleType': 'CONTROLLED_DOE',
                    'sampleIndex': index,
                    'caseId': result.get('case_id'),
                    'solver': result.get('solver'),
                    'status': result.get('status'),
                    'design': design.get('design_parameters') or {'c': None, 'alpha': None},
                    'responses': self._display_response_values(
                        self._combined_scenario_objectives(results),
                        targets,
                        bare_key_targets,
                    ),
                }
            )
        return rows

    def _display_response_values(
        self,
        objectives: dict[str, Any],
        targets: tuple[dict[str, Any], ...] = JOINT_RESPONSE_TARGETS,
        bare_key_targets: tuple[dict[str, Any], ...] = EARTHQUAKE_RESPONSE_TARGETS,
    ) -> dict[str, dict[str, Any]]:
        """把目标值按展示单位换算。

        `bare_key_targets` 限定哪些目标可以回退到不带工况前缀的键：无控基线摘要
        写的是裸键（如 cumulative_displacement），而联合工况下裸键属于地震，
        不能让 operation 目标误读它。
        """
        values = {}
        for target in targets:
            raw_value = objectives.get(target['fullObjective'])
            if raw_value is None and target in bare_key_targets:
                raw_value = objectives.get(target['objective'])
            value = self._optional_float(raw_value)
            values[target['targetId']] = {
                'label': target['label'],
                'rawValue': value,
                'sourceUnit': target['sourceUnit'],
                'displayValue': None if value is None else value * float(target['displayScale']),
                'displayUnit': target['displayUnit'],
            }
        return values

    def _combined_scenario_objectives(self, results: list[dict[str, Any]]) -> dict[str, Any]:
        objectives: dict[str, Any] = {}
        for result in results:
            scenario = str((result.get('load_case') or {}).get('name') or '')
            for name, value in (result.get('objectives') or {}).items():
                if scenario:
                    objectives[f'{scenario}:{name}'] = value
        return objectives

    def _display_objective_limits(
        self,
        limits: dict[str, Any],
        targets: tuple[dict[str, Any], ...] = EARTHQUAKE_RESPONSE_TARGETS,
    ) -> list[dict[str, Any]]:
        rows = []
        for target in targets:
            raw_value = limits.get(target['fullObjective'], limits.get(target['objective']))
            value = self._optional_float(raw_value)
            rows.append(
                {
                    'targetId': target['targetId'],
                    'label': target['label'],
                    'objective': target['fullObjective'],
                    'rawValue': value,
                    'sourceUnit': target['sourceUnit'],
                    'displayValue': None if value is None else value * float(target['displayScale']),
                    'displayUnit': target['displayUnit'],
                }
            )
        return rows

    def _status_is_verified_and_accepted(self, status: dict[str, Any]) -> bool:
        return bool(status.get('all_verified_execution')) and bool(status.get('all_accepted'))

    def _optional_float(self, value: Any) -> float | None:
        try:
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None

    def _ansys_parallel_mapdl_args(self, raw_args: Any) -> list[str]:
        args = [str(item) for item in (raw_args or [])]
        if '-smp' not in args:
            args.append('-smp')
        if '-db' not in args:
            args.extend(['-db', '32'])
        return args

    def _config_with_resolved_paths(self, config: dict[str, Any], config_dir: Path, keys: tuple[str, ...]) -> dict[str, Any]:
        updated = dict(config)
        for key in keys:
            value = updated.get(key)
            if value:
                updated[key] = str(self._resolve_config_path(value, config_dir))
        return updated

    def _resolve_config_path(self, value: Any, config_dir: Path) -> Path:
        path = Path(str(value))
        return path if path.is_absolute() else (config_dir / path).resolve()

    def _load_json_config(self, path: Path) -> dict[str, Any]:
        with path.open('r', encoding='utf-8') as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError(f'JSON config must be an object: {path}')
        return payload

    def _write_json_config(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(strict_json_dumps(payload, indent=2), encoding='utf-8')

    def _baseline_optimization_workflow_config_path(self, params: dict[str, Any]) -> Path:
        configured = params.get('workflowConfigPath')
        load_kind = str(params.get('loadKind') or params.get('scenario') or 'EARTHQUAKE').upper()
        registered = OPTIMIZATION_WORKFLOW_CONFIGS_BY_LOAD_KIND.get(load_kind) or {}
        if configured:
            candidate = (REPO_ROOT / str(configured)).resolve()
        else:
            solver = str(params.get('solver') or 'ANSYS')
            try:
                candidate = (TEMPLATE_ROOT / registered[solver]).resolve()
            except KeyError as exc:
                raise HTTPException(
                    status_code=422,
                    detail={
                        'code': 'UNSUPPORTED_REAL_WORKFLOW_SOLVER',
                        'message': f'{load_kind} 真实 baseline-first optimization 暂不支持求解器 {solver}',
                    },
                ) from exc
        template_root = TEMPLATE_ROOT.resolve()
        # 只放行当前荷载类型登记的模板，避免风工况请求引用地震模板（或反向）。
        if candidate.parent != template_root or candidate.name not in set(registered.values()) or not candidate.is_file():
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'INVALID_WORKFLOW_CONFIG_PATH',
                    'message': f'workflowConfigPath 不是已登记的 baseline-first workflow 模板: {candidate}',
                },
            )
        return candidate

    def _run_real_baseline_optimization_workflow(self, workflow_config_path: Path, *, execution_timeout_s: float | None) -> dict[str, Any]:
        import sys

        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        from pyansys_bridge.optimization import run_undamped_baseline_optimization_workflow_config

        execution = self._real_executor.execute_config(
            ConfigExecutionRequest(config_path=workflow_config_path, timeout_s=execution_timeout_s),
            runner=run_undamped_baseline_optimization_workflow_config,
        )
        return {**execution.payload, 'executionUsage': execution.usage}

    def _run_real_agent_analysis(self, config_path: Path, *, execution_timeout_s: float | None) -> dict[str, Any]:
        import sys

        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        from pyansys_bridge.optimization import run_undamped_baseline_config

        def runner(path: Path, *, execution_timeout_s: float | None) -> Any:
            return run_undamped_baseline_config(
                path,
                execution_timeout_s=execution_timeout_s,
                use_cache=False,
            )

        execution = self._real_executor.execute_config(
            ConfigExecutionRequest(config_path=config_path, timeout_s=execution_timeout_s),
            runner=runner,
        )
        return {**execution.payload, 'executionUsage': execution.usage}

    def _run_real_damper_comparison_case(
        self,
        config_path: Path,
        *,
        execution_timeout_s: float | None,
    ) -> dict[str, Any]:
        import sys

        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        from pyansys_bridge.optimization import run_solver_acceptance_case_config

        execution = self._real_executor.execute_config(
            ConfigExecutionRequest(config_path=config_path, timeout_s=execution_timeout_s),
            runner=run_solver_acceptance_case_config,
        )
        return {**execution.payload, 'executionUsage': execution.usage}

    def _register_json_file_artifact(self, *, name: str, path: Path, fallback_preview: dict[str, Any]) -> Artifact:
        preview: Any = fallback_preview
        content: bytes | None = None
        if path.is_file():
            content = path.read_bytes()
            try:
                preview = json.loads(content.decode('utf-8'))
            except json.JSONDecodeError:
                preview = {'path': str(path), 'status': 'UNREADABLE_JSON'}
        return self._register_artifact(
            kind='JSON_SUMMARY',
            name=name,
            path=self._repo_display_path(path),
            mime_type='application/json',
            preview=preview,
            content=content,
        )

    def _register_inquiry_csv_artifacts(self, run_dir: Path) -> list[Artifact]:
        """登记真实求解输出中的可追问 CSV；不把荷载输入或配置文件暴露给追问链。"""
        artifacts: list[Artifact] = []
        for path in sorted(run_dir.rglob('*.csv')):
            if not is_inquiry_result_csv(path):
                continue
            artifact_path = self._repo_display_path(path)
            existing = next(
                (record for record in self.artifacts if record.artifact.path == artifact_path),
                None,
            )
            if existing is not None:
                artifacts.append(existing.artifact)
                continue
            content = path.read_bytes()
            artifacts.append(self._register_artifact(
                kind='CSV_TIMESERIES' if path.name == 'timeseries.csv' else 'CSV_TABLE',
                name=path.name,
                path=artifact_path,
                mime_type='text/csv; charset=utf-8',
                preview=self._real_history_preview(path, content),
                content=content,
                source='REAL_SOLVER_RESULT',
                run_id=run_dir.name,
                created_at=datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
            ))
        return artifacts

    def ensure_optimization_metric_workbooks(
        self,
        *,
        run_id: str,
        artifact_ids: list[str],
    ) -> list[Artifact]:
        """按指标生成可下载的 Excel 工作簿，每个参数工况占一个工作表。

        优化会为多个 DOE/主动学习参数工况登记同名 CSV。对话卡片不应把这些
        原始文件逐个暴露给用户，因此这里把同一指标的时程合并到一个工作簿中。
        生成结果以 run_id 和中文文件名做幂等键；重复读取结果时只复用已有制品。
        """

        existing = [
            record.artifact
            for record in self.artifacts
            if record.artifact.run_id == run_id
            and record.artifact.kind == 'BINARY'
            and record.artifact.name.startswith('优化结果_')
            and record.artifact.name.endswith('.xlsx')
        ]
        if existing:
            return existing

        import io

        from openpyxl import Workbook
        from openpyxl.styles import Font

        metric_specs = (
            ('max_girder_end_displacement', '梁端位移', 'm', ('displacement', 'girder_displacement', 'absolute_displacement')),
            ('max_acceleration', '加速度响应', 'm/s²', ('acceleration',)),
            ('max_tower_base_shear', '塔底剪力', 'N', ('tower_base_shear',)),
            ('max_tower_base_moment', '塔底弯矩', 'N·m', ('tower_base_moment',)),
            ('max_damper_force', '阻尼器力', 'N', ('damper_force',)),
            ('max_damper_stroke', '阻尼器行程', 'm', ('damper_stroke',)),
            ('cumulative_displacement', '累计位移', 'm', ('cumulative_displacement',)),
        )
        records_by_id = {record.artifact.artifact_id: record for record in self.artifacts}
        case_labels = self._optimization_case_labels(records_by_id, artifact_ids)
        grouped: dict[str, list[dict[str, Any]]] = {item[0]: [] for item in metric_specs}

        for artifact_id in dict.fromkeys(artifact_ids):
            record = records_by_id.get(str(artifact_id))
            if record is None or record.artifact.kind not in {'CSV_TIMESERIES', 'CSV_TABLE'}:
                continue
            try:
                text = record.content.decode('utf-8-sig')
                rows = list(csv.reader(text.splitlines()))
            except (UnicodeDecodeError, csv.Error):
                continue
            if not rows or not rows[0]:
                continue
            headers = [str(value).strip() for value in rows[0]]
            normalized_headers = {header.lower(): index for index, header in enumerate(headers)}
            time_index = next(
                (index for name, index in normalized_headers.items() if name == 'time' or name.endswith('_time')),
                None,
            )
            if time_index is None:
                continue
            source_path = str(record.artifact.path or '').replace('\\', '/')
            case_id = Path(source_path).parent.name
            case_label = case_labels.get(case_id) or case_id or Path(source_path).stem
            for metric_id, label, unit, aliases in metric_specs:
                value_index = next(
                    (normalized_headers[alias] for alias in aliases if alias in normalized_headers),
                    None,
                )
                if value_index is None:
                    continue
                values = [
                    [row[time_index] if time_index < len(row) else '', row[value_index] if value_index < len(row) else '']
                    for row in rows[1:]
                    if time_index < len(row) or value_index < len(row)
                ]
                if values and not any(item['caseLabel'] == case_label for item in grouped[metric_id]):
                    grouped[metric_id].append({
                        'label': label,
                        'unit': unit,
                        'caseLabel': case_label,
                        'source': source_path,
                        'values': values,
                    })

        generated: list[Artifact] = []
        for metric_id, label, unit, _aliases in metric_specs:
            sources = grouped[metric_id]
            if not sources:
                continue
            workbook = Workbook()
            workbook.remove(workbook.active)
            used_titles: set[str] = set()
            for index, source in enumerate(sources, start=1):
                title = re.sub(r'[\\/*?:\[\]]', '_', str(source['caseLabel'] or '工况'))[:31] or f'工况{index}'
                base_title = title
                suffix = 2
                while title in used_titles:
                    tail = f'_{suffix}'
                    title = f'{base_title[:31 - len(tail)]}{tail}'
                    suffix += 1
                used_titles.add(title)
                sheet = workbook.create_sheet(title=title)
                sheet.append([f'{label}（{unit}）'])
                sheet['A1'].font = Font(bold=True, size=14)
                sheet.append(['参数工况', source['caseLabel']])
                sheet.append(['来源文件', source['source']])
                sheet.append(['时间（s）', f'{label}（{unit}）'])
                for cell in sheet[4]:
                    cell.font = Font(bold=True)
                for row in source['values']:
                    sheet.append(row)
                sheet.column_dimensions['A'].width = 18
                sheet.column_dimensions['B'].width = 24
            output = io.BytesIO()
            workbook.save(output)
            content = output.getvalue()
            generated.append(self._register_artifact(
                kind='BINARY',
                name=f'优化结果_{label}.xlsx',
                path=f'output/platform_store/agent_runs/{run_id}/优化结果_{label}.xlsx',
                mime_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                preview={
                    'type': 'OPTIMIZATION_METRIC_WORKBOOK',
                    'metricId': metric_id,
                    'label': label,
                    'unit': unit,
                    'sheetCount': len(sources),
                    'sheets': [source['caseLabel'] for source in sources],
                },
                content=content,
                source='PLATFORM_JOB',
                run_id=run_id,
            ))
        if generated:
            self.persist()
        return generated

    @staticmethod
    def _is_real_damper_parameter_sweep_request(job_type: JobType, params: dict[str, Any]) -> bool:
        cases = list(params.get('cases') or [])
        scenario = str(params.get('scenario') or '')
        solver = str(params.get('solver') or '').upper()
        # 放行口径直接取自模板登记表：地震、风、车流三种工况 × 两个求解器都有已登记
        # 的批量模板，新增组合只需登记模板，不用再改这里的白名单。
        return (
            job_type == 'SOLVER_BATCH'
            and params.get('runMode') == 'REAL_DAMPER_PARAMETER_SWEEP'
            and scenario in AGENT_SWEEP_CONFIGS_BY_LOAD_KIND
            and solver in AGENT_SWEEP_CONFIGS_BY_LOAD_KIND[scenario]
            and 1 <= len(cases) <= 64
            and len({case.get('caseId') for case in cases}) == len(cases)
        )

    @staticmethod
    def _validate_real_damper_parameter_sweep_params(params: dict[str, Any]) -> None:
        solver = str(params.get('solver') or '').upper()
        for case in params.get('cases') or []:
            damper_type = str(case.get('damperType') or '')
            # 用 .get 逐级取值而不是下标：未登记的求解器名要以 422 失败关闭，
            # 下标会抛 KeyError 变成 500。
            expected_module = (DAMPER_SOLVER_MODULES.get(damper_type) or {}).get(solver)
            if expected_module is None or case.get('solverModule') != expected_module:
                raise HTTPException(status_code=422, detail={
                    'code': 'INVALID_DAMPER_MODULE',
                    'message': f'{damper_type} 的 {solver} 模块不匹配',
                })
            parameters = case.get('parameters') or {}
            required = {
                'VISCOUS': {'c', 'alpha', 'vfloor'},
                'FRICTION': {'fc', 'vs'},
                'EDDY_CURRENT': {'fmax', 'vcr'},
            }[damper_type]
            if set(parameters) != required or any(float(value) <= 0 for value in parameters.values()):
                raise HTTPException(status_code=422, detail={
                    'code': 'INVALID_DAMPER_PARAMETERS',
                    'message': f'{damper_type} 参数必须完整且为正数',
                })
        layout = params.get('selectedLayout') or {}
        if params.get('selectedLayoutId') not in {'ONE_PER_TOWER', 'TWO_PER_TOWER'} or not layout.get('nodePairs'):
            raise HTTPException(status_code=422, detail={
                'code': 'UNREGISTERED_DAMPER_LAYOUT',
                'message': '参数批量计算仅支持登记的阻尼器布置',
            })

    @staticmethod
    def _ansys_license_concurrency_cap() -> int | None:
        """运维声明的 ANSYS 许可证座位数上限；未配置或非法时不限制。"""
        raw = os.getenv('MOMO_ANSYS_MAX_CONCURRENT', '').strip()
        if not raw:
            return None
        try:
            cap = int(raw)
        except ValueError:
            return None
        return cap if cap > 0 else None

    # FlexNet/ANSYS 许可证故障在求解日志与异常消息里的稳定特征。
    _ANSYS_LICENSE_ERROR_MARKERS = (
        'license',
        'ansyslmd',
        'flexnet',
        'flexlm',
        'lmgrd',
    )

    @classmethod
    def _is_ansys_license_error(cls, exc: BaseException) -> bool:
        text = str(exc).lower()
        return any(marker in text for marker in cls._ANSYS_LICENSE_ERROR_MARKERS)

    def _generate_real_damper_parameter_sweep_artifacts(
        self,
        params: dict[str, Any],
        *,
        job_id: str | None = None,
    ) -> list[Artifact]:
        solver = str(params.get('solver') or 'ANSYS').upper()
        load_kind = str(params.get('loadKind') or params.get('scenario') or 'EARTHQUAKE').upper()
        # 批量复用对应工况的 ANALYSIS 基线模板。组合虽已由
        # _is_real_damper_parameter_sweep_request 收敛，这里仍显式失败关闭：
        # 该方法也可被同步执行路径直接调用，缺表时要给 422 而不是 KeyError。
        registered_templates = AGENT_SWEEP_CONFIGS_BY_LOAD_KIND.get(load_kind)
        if registered_templates is None or solver not in registered_templates:
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'UNSUPPORTED_REAL_PARAMETER_SWEEP_REQUEST',
                    'message': f'{load_kind} 参数批量计算暂不支持求解器 {solver}',
                },
            )
        source_path = (TEMPLATE_ROOT / registered_templates[solver]).resolve()
        run_dir = EARTHQUAKE_WORKFLOW_OUTPUT_ROOT / gen_id('damper_parameter_sweep')
        run_dir.mkdir(parents=True, exist_ok=True)
        cases = list(params.get('cases') or [])
        damper_c_scale = ENGINEERING_VISCOUS_C_SCALE_BY_SOLVER[solver]
        progress_dir = self._begin_job_progress(job_id, total_cases=len(cases))
        case_summaries: list[dict[str, Any]] = []
        artifacts: list[Artifact] = []

        requested_max_workers = int((params.get('budget') or {}).get('maxConcurrentCases') or params.get('maxConcurrentCases') or 4)
        requested_max_workers = max(1, min(requested_max_workers, len(cases)))
        max_workers = requested_max_workers
        # ANSYS 并发受许可证座位数约束；运维通过环境变量声明可用座位数，
        # 超过座位数的并发请求在这里收敛而不是让 MAPDL 启动后拿不到 license 失败。
        license_cap = self._ansys_license_concurrency_cap() if solver == 'ANSYS' else None
        if license_cap is not None:
            max_workers = max(1, min(max_workers, license_cap))

        def prepare_case(case: dict[str, Any]) -> dict[str, Any]:
            case_id = str(case['caseId'])
            case_dir = run_dir / case_id
            case_dir.mkdir(parents=True, exist_ok=True)
            config = self._earthquake_baseline_config(
                self._load_json_config(source_path),
                source_path.parent,
                case_dir,
                solver,
                load_kind,
            )
            # OpenSeesPy 每个 case 都运行在独立子进程，并以业务 caseId 写各自
            # 的进度文件；ANSYS 保持原有单 case 透传行为，避免改变其输出探针语义。
            if progress_dir is not None and (solver == 'OPENSEESPY_INPROC' or max_workers == 1):
                config['progress_dir'] = str(progress_dir)
            # 荷载文件按 case_dir 逐案例落盘：OpenSees 的案例跑在真并发子进程里，
            # 共用一份 run_dir 下的节点力文件会互相覆盖。
            if load_kind == 'WIND':
                load_evidence = self._apply_agent_standard_wind_load(
                    config,
                    case_dir,
                    params,
                    solver=solver,
                )
            elif load_kind == 'TRAFFIC':
                # 车流矩阵（163 列）比风的单列文件大得多，逐案例隔离更必要。
                load_evidence = self._apply_agent_standard_traffic_load(
                    config,
                    case_dir,
                    params,
                )
            else:
                load_evidence = self._apply_agent_standard_earthquake_load(
                    config,
                    {'load_cases': [{'name': 'earthquake'}]},
                    case_dir,
                    params,
                )
            solver_kwargs = dict(config.get('solver_kwargs') or {})
            solver_kwargs.update({
                'damper_module': case['solverModule'],
                'damper_c_scale': damper_c_scale,
                'omit_dampers': False,
                'physical_count_per_tower': int((params.get('selectedLayout') or {}).get('physicalCountPerTower') or 1),
            })
            if solver == 'OPENSEESPY_INPROC':
                solver_kwargs['progress_case_id'] = case_id
            config['solver_kwargs'] = solver_kwargs
            config['damper_params'] = self._comparison_solver_params(case)
            config['summary_path'] = str(case_dir / 'case_summary.json')
            config_path = case_dir / source_path.name
            self._write_json_config(config_path, config)
            return {
                'case': case,
                'case_dir': case_dir,
                'config_path': config_path,
                'load_evidence': load_evidence,
            }

        def complete_case(prepared: dict[str, Any], analysis: dict[str, Any]) -> tuple[dict[str, Any], list[Artifact]]:
            case = prepared['case']
            case_id = str(case['caseId'])
            case_dir = Path(prepared['case_dir'])
            verified = self._is_verified_real_case_output(analysis)
            case_summary = {
                'caseId': case_id,
                'damperType': case['damperType'],
                'solverModule': case['solverModule'],
                'parameters': case['parameters'],
                'status': analysis.get('status'),
                'isVerifiedSolverOutput': verified,
                'objectives': analysis.get('objectives') or {},
                'solverCaseId': analysis.get('case_id'),
                'executionUsage': analysis.get('executionUsage'),
                'customLoadEvidence': prepared['load_evidence'],
            }
            case_summary_path = case_dir / 'real_case_summary.json'
            self._write_json_config(case_summary_path, case_summary)
            case_artifacts = [self._register_json_file_artifact(
                name=f'real_{case_id}_case_summary.json',
                path=case_summary_path,
                fallback_preview={'status': 'MISSING', 'path': str(case_summary_path)},
            )]
            command_path_value = ((analysis.get('metadata') or {}).get('command_stream') or {}).get('path')
            if command_path_value:
                command_path = Path(str(command_path_value)).resolve()
                if command_path.is_file() and command_path.is_relative_to(run_dir.resolve()):
                    case_artifacts.append(self._register_artifact(
                        kind='COMMAND_STREAM',
                        name=f'{case_id}_executed_command_stream.txt',
                        path=self._repo_display_path(command_path),
                        mime_type='text/plain; charset=utf-8',
                        preview={'phase': 'EXECUTED', 'damperType': case['damperType'], 'solverModule': case['solverModule']},
                        content=command_path.read_bytes(),
                    ))
            case_artifacts.extend(self._register_inquiry_csv_artifacts(case_dir))
            return case_summary, case_artifacts

        def execute_case(case: dict[str, Any]) -> tuple[dict[str, Any], list[Artifact]]:
            prepared = prepare_case(case)
            analysis = self._run_real_damper_comparison_case(
                prepared['config_path'],
                execution_timeout_s=None if params.get('executionTimeoutS') is None else float(params['executionTimeoutS']),
            )
            return complete_case(prepared, analysis)

        case_order = {str(case['caseId']): position for position, case in enumerate(cases)}

        def accept_case(case_summary: dict[str, Any], case_artifacts: list[Artifact]) -> None:
            case_summaries.append(case_summary)
            artifacts.extend(case_artifacts)
            case_summaries.sort(key=lambda item: case_order[str(item['caseId'])])
            artifacts.sort(key=lambda artifact: artifact.name)
            # 每完成一个 case 就推进完成计数，避免长批量在结束前进度一直为 0。
            if progress_dir is not None:
                write_batch_progress(progress_dir, completed=len(case_summaries), total=len(cases))

        # 许可证不足属于环境资源竞争而非算例本身的错误：并发阶段把这类
        # 失败排队，等线程池释放全部座位后串行重试，而不是让整批失败。
        license_retry_cases: list[dict[str, Any]] = []
        if solver == 'OPENSEESPY_INPROC':
            prepared_cases = [prepare_case(case) for case in cases]
            execution_timeout_s = (
                None if params.get('executionTimeoutS') is None else float(params['executionTimeoutS'])
            )
            with ProcessPoolExecutor(max_workers=max_workers) as executor:
                future_map = {
                    executor.submit(
                        _execute_damper_parameter_sweep_case,
                        prepared['config_path'],
                        execution_timeout_s,
                    ): prepared
                    for prepared in prepared_cases
                }
                for future in as_completed(future_map):
                    accept_case(*complete_case(future_map[future], future.result()))
        else:
            with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix='damper-sweep') as executor:
                future_map = {executor.submit(execute_case, case): case for case in cases}
                for future in as_completed(future_map):
                    try:
                        case_summary, case_artifacts = future.result()
                    except Exception as exc:
                        if max_workers > 1 and self._is_ansys_license_error(exc):
                            license_retry_cases.append(future_map[future])
                            continue
                        raise
                    accept_case(case_summary, case_artifacts)
        license_retry_cases.sort(key=lambda case: case_order[str(case['caseId'])])
        for case in license_retry_cases:
            accept_case(*execute_case(case))

        summary = {
            'mode': 'real_damper_parameter_sweep',
            'runMode': 'REAL_DAMPER_PARAMETER_SWEEP',
            'solver': solver,
            'loadKind': load_kind,
            'selectedLayoutId': params.get('selectedLayoutId'),
            'selectedLayout': params.get('selectedLayout'),
            'responseIds': params.get('responseIds') or [],
            'parallelism': {
                'requestedMaxConcurrentCases': requested_max_workers,
                'effectiveMaxConcurrentCases': max_workers,
                'mode': 'process_pool' if solver == 'OPENSEESPY_INPROC' else 'thread_pool',
                'ansysLicenseConcurrencyCap': license_cap,
                'licenseRetriedCaseIds': [str(case['caseId']) for case in license_retry_cases],
            },
            'caseResults': case_summaries,
            'allVerifiedExecution': bool(case_summaries) and all(case['isVerifiedSolverOutput'] for case in case_summaries),
            'solverVersionProfile': params.get('solverVersionProfile'),
            'inputProvenance': params.get('inputProvenance') or [],
            'executionUsage': {
                'realSolveCount': len(case_summaries),
                'cases': [case.get('executionUsage') for case in case_summaries],
            },
        }
        summary_path = run_dir / 'real_damper_parameter_sweep_summary.json'
        self._write_json_config(summary_path, summary)
        artifacts.insert(0, self._register_json_file_artifact(
            name='real_damper_parameter_sweep_summary.json',
            path=summary_path,
            fallback_preview={'status': 'MISSING', 'path': str(summary_path)},
        ))
        artifacts.append(self._register_result_catalog(run_dir))
        artifacts.append(self._register_real_output_manifest(run_dir))
        return artifacts

    def _optimization_case_labels(
        self,
        records_by_id: dict[str, ArtifactRecord],
        artifact_ids: list[str],
    ) -> dict[str, str]:
        """从优化摘要把 case_id 映射到可读的参数标签。"""

        labels: dict[str, str] = {}
        for artifact_id in artifact_ids:
            record = records_by_id.get(str(artifact_id))
            if record is None or record.artifact.name not in {'real_optimization_summary.json', 'optimization_summary.json'}:
                continue
            preview = record.preview if isinstance(record.preview, dict) else {}
            for key in ('doe_designs', 'active_learning_records'):
                for item in preview.get(key) or []:
                    if not isinstance(item, dict):
                        continue
                    design = item.get('design_parameters') or item.get('designParameters') or {}
                    label = '，'.join(f'{key}={value:g}' if isinstance(value, (int, float)) else f'{key}={value}' for key, value in design.items()) or '未记录参数'
                    for result in item.get('analysis_results') or []:
                        if isinstance(result, dict) and result.get('case_id'):
                            labels[str(result['case_id'])] = label
        return labels

    def _register_result_catalog(self, run_dir: Path) -> Artifact:
        """登记统一结果目录，供只读追问和报告回放绑定 CSV 来源。"""

        entries: list[dict[str, Any]] = []
        for path in sorted(run_dir.rglob('*.csv'), key=lambda item: item.as_posix()):
            if not is_inquiry_result_csv(path):
                continue
            raw = path.read_bytes()
            preview = self._real_history_preview(path, raw)
            columns = [str(column) for column in preview.get('headers', [])]
            if not columns:
                continue
            entries.append({
                'artifactPath': self._repo_display_path(path),
                'columns': columns,
                'units': self._result_catalog_units(columns),
                'sha256': sha256(raw).hexdigest(),
                'verified': True,
            })
        catalog = {
            'schemaVersion': '1.0',
            'runId': run_dir.name,
            'entryCount': len(entries),
            'entries': entries,
            'verified': bool(entries),
        }
        path = run_dir / 'result_catalog.json'
        self._write_json_config(path, catalog)
        return self._register_artifact(
            kind='JSON_SUMMARY',
            name='result_catalog.json',
            path=self._repo_display_path(path),
            mime_type='application/json',
            preview=catalog,
            content=path.read_bytes(),
            source='REAL_SOLVER_RESULT',
            run_id=run_dir.name,
        )

    @staticmethod
    def _result_catalog_units(columns: list[str]) -> dict[str, str]:
        units: dict[str, str] = {}
        for column in columns:
            normalized = column.lower()
            if normalized == 'time' or normalized.endswith('_time'):
                units[column] = 's'
            elif 'accel' in normalized:
                units[column] = 'm/s²'
            elif 'disp' in normalized or 'drift' in normalized or 'stroke' in normalized:
                units[column] = 'm'
            elif 'moment' in normalized:
                units[column] = 'N*m'
            elif 'shear' in normalized or 'force' in normalized:
                units[column] = 'N'
            else:
                units[column] = '1'
        return units

    def _register_real_output_manifest(self, run_dir: Path) -> Artifact:
        manifest = build_output_manifest(
            run_dir,
            allowed_root=EARTHQUAKE_WORKFLOW_OUTPUT_ROOT,
        )
        manifest_path = run_dir / 'real_output_manifest.json'
        self._write_json_config(manifest_path, manifest)
        return self._register_json_file_artifact(
            name='real_output_manifest.json',
            path=manifest_path,
            fallback_preview={'status': 'MISSING', 'path': str(manifest_path)},
        )

    def _repo_display_path(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(REPO_ROOT).as_posix()
        except ValueError:
            return str(path)

    def _workflow_execution_summary(self, job_type: JobType, params: dict[str, Any]) -> dict[str, Any]:
        required_modules = [str(module_id) for module_id in params.get('requiredModules', [])]
        return {
            'jobType': job_type,
            'projectName': params.get('projectName'),
            'modelFileName': params.get('modelFileName'),
            'solver': params.get('solver', 'ANSYS'),
            'scenario': params.get('scenario'),
            'executionTarget': params.get('executionTarget'),
            'requiredModules': required_modules,
            'requiredModuleCount': len(required_modules),
            **mock_demo_fields(),
            'mode': 'workflow_summary',
        }

    def _module_status(self, config: dict[str, Any]) -> list[dict[str, Any]]:
        catalog = [
            ('DAMPER_BASE', '阻尼器基准配置', '/damper-base'),
            ('DOE', '试验设计', '/experiment-design'),
            ('LOADS', '荷载配置', '/loads'),
            ('SOLVER_BATCH', '求解器批处理设置', '/solver'),
            ('RESULT_EXTRACTION', '结果提取配置', '/results'),
            ('SURROGATE_LEARNING', '代理模型与主动学习', '/surrogate'),
            ('OPTIMIZATION_DECISION', '多目标优化与决策配置', '/optimization'),
        ]
        required = self._required_engineering_modules(config)
        evaluators = {
            'DAMPER_BASE': self._damper_base_status,
            'DOE': self._doe_status,
            'LOADS': self._loads_status,
            'SOLVER_BATCH': self._solver_batch_status,
            'RESULT_EXTRACTION': self._result_extraction_status,
            'SURROGATE_LEARNING': self._surrogate_learning_status,
            'OPTIMIZATION_DECISION': self._optimization_decision_status,
        }
        return [
            {
                'moduleId': module_id,
                'label': label,
                **evaluators[module_id](config),
                'required': module_id in required,
            }
            for module_id, label, route in catalog
        ]

    def _required_engineering_modules(self, config: dict[str, Any]) -> set[str]:
        global_config = config.get('globalTaskConfig') or {}
        scenario = global_config.get('scenario')
        target = global_config.get('executionTarget')
        required = {'DAMPER_BASE', 'DOE'}
        if target != 'DESIGN_SAMPLES':
            required.add('LOADS')
        if target in ('BATCH_SOLVE', 'RESULT_EXTRACTION', 'SURROGATE_TRAINING', 'OPTIMIZATION_DECISION'):
            required.update({'SOLVER_BATCH', 'RESULT_EXTRACTION'})
        if target in ('SURROGATE_TRAINING', 'OPTIMIZATION_DECISION'):
            required.add('SURROGATE_LEARNING')
        if target == 'OPTIMIZATION_DECISION':
            required.add('OPTIMIZATION_DECISION')
        if scenario and target == 'COMMAND_STREAM':
            required.add('LOADS')
        return required

    def _damper_base_status(self, config: dict[str, Any]) -> dict[str, str]:
        model_file = (config.get('projectConfig') or {}).get('modelFile') or {}
        damper_config = config.get('damperBaseConfig') or {}
        connection_node_pairs = damper_config.get('connectionNodePairs') or []
        registry = damper_config.get('damperInstanceRegistry') or []
        if model_file.get('parseStatus') != 'PARSED':
            return {'status': 'INCOMPLETE', 'message': '模型文件尚未解析，无法生成阻尼器注册表', 'route': '/damper-base'}
        if not connection_node_pairs:
            return {'status': 'UNCONFIGURED', 'message': '未配置自定义阻尼器连接节点', 'route': '/damper-base'}
        if any(
            not isinstance(pair, dict)
            or not isinstance(pair.get('nodeI'), int)
            or not isinstance(pair.get('nodeJ'), int)
            or pair['nodeI'] <= 0
            or pair['nodeJ'] <= 0
            or pair['nodeI'] == pair['nodeJ']
            for pair in connection_node_pairs
        ):
            return {'status': 'INCOMPLETE', 'message': '自定义连接节点必须为不同的正整数节点', 'route': '/damper-base'}
        if not registry:
            return {'status': 'UNCONFIGURED', 'message': '未生成阻尼器实例注册表', 'route': '/damper-base'}
        status = 'VALIDATED' if damper_config.get('placementValidated') else 'CONFIGURED'
        return {'status': status, 'message': f'已按自定义节点注册 {len(registry)} 个 USER300 阻尼器', 'route': '/damper-base'}

    def _doe_status(self, config: dict[str, Any]) -> dict[str, str]:
        doe_config = config.get('doeConfig') or {}
        variables = doe_config.get('variables') or []
        enabled_count = sum(1 for variable in variables if variable.get('enabled') is True)
        if enabled_count == 0 or int(doe_config.get('sampleCount') or 0) <= 0:
            return {'status': 'UNCONFIGURED', 'message': '未选择 DOE 设计变量', 'route': '/experiment-design'}
        status = 'VALIDATED' if doe_config.get('validated') else 'CONFIGURED'
        return {'status': status, 'message': f'已选择 {enabled_count} 个 DOE 变量', 'route': '/experiment-design'}

    def _loads_status(self, config: dict[str, Any]) -> dict[str, str]:
        scenario = (config.get('globalTaskConfig') or {}).get('scenario')
        load_config = config.get('loadConfig') or {}
        if scenario == 'EARTHQUAKE':
            return self._single_load_status(load_config.get('earthquake') or {}, '地震荷载', '/loads?tab=earthquake')
        if scenario == 'WIND':
            return self._single_load_status(load_config.get('wind') or {}, '风荷载', '/loads?tab=wind')
        if scenario == 'TRAFFIC':
            return self._single_load_status(load_config.get('traffic') or {}, '车流荷载', '/loads?tab=traffic')

        wind = load_config.get('wind') or {}
        traffic = load_config.get('traffic') or {}
        if not wind.get('configured') and not traffic.get('configured'):
            return {'status': 'UNCONFIGURED', 'message': '风荷载与车流荷载均未配置', 'route': '/loads?tab=wind'}
        if not wind.get('configured'):
            return {'status': 'INCOMPLETE', 'message': '风-车组合缺少风荷载配置', 'route': '/loads?tab=wind'}
        if not traffic.get('configured'):
            return {'status': 'INCOMPLETE', 'message': '风-车组合缺少车流荷载配置', 'route': '/loads?tab=traffic'}
        status = 'VALIDATED' if wind.get('validated') and traffic.get('validated') else 'CONFIGURED'
        return {'status': status, 'message': '风荷载和车流荷载已配置', 'route': '/loads?tab=wind'}

    def _single_load_status(self, load_config: dict[str, Any], label: str, route: str) -> dict[str, str]:
        if not load_config.get('configured'):
            return {'status': 'UNCONFIGURED', 'message': f'{label}未配置，点击前往配置', 'route': route}
        status = 'VALIDATED' if load_config.get('validated') else 'CONFIGURED'
        return {'status': status, 'message': f'{label}已配置', 'route': route}

    def _solver_batch_status(self, config: dict[str, Any]) -> dict[str, str]:
        solver_config = config.get('solverBatchConfig') or {}
        process_count = int(solver_config.get('processCount') or 0)
        cores_per_process = int(solver_config.get('coresPerProcess') or 0)
        if process_count <= 0 or cores_per_process <= 0:
            return {'status': 'INCOMPLETE', 'message': '求解资源进程数或核心数无效', 'route': '/solver'}
        status = 'VALIDATED' if solver_config.get('validated') else 'CONFIGURED'
        return {'status': status, 'message': f'求解资源：{process_count} 进程，每进程 {cores_per_process} 核', 'route': '/solver'}

    def _result_extraction_status(self, config: dict[str, Any]) -> dict[str, str]:
        result_config = config.get('resultExtractionConfig') or {}
        structural = result_config.get('structuralMetrics') or []
        damper = result_config.get('autoDamperMetrics') or []
        enabled_structural = sum(1 for item in structural if item.get('enabled') is True)
        enabled_damper = sum(1 for item in damper if item.get('enabled') is True)
        if enabled_structural == 0 and enabled_damper == 0:
            return {'status': 'UNCONFIGURED', 'message': '尚未启用任何结果提取指标', 'route': '/results'}
        status = 'VALIDATED' if result_config.get('validated') else 'CONFIGURED'
        return {'status': status, 'message': f'结构指标 {enabled_structural} 项，阻尼器自动指标 {enabled_damper} 项', 'route': '/results'}

    def _surrogate_learning_status(self, config: dict[str, Any]) -> dict[str, str]:
        surrogate_config = config.get('surrogateLearningConfig') or {}
        model_families = surrogate_config.get('modelFamilies') or []
        output_response_ids = surrogate_config.get('outputResponseIds') or []
        if not model_families or not output_response_ids:
            return {'status': 'UNCONFIGURED', 'message': '代理模型族或输出响应目标未配置', 'route': '/surrogate'}
        status = 'VALIDATED' if surrogate_config.get('validated') else 'CONFIGURED'
        return {'status': status, 'message': f"代理模型：{', '.join(map(str, model_families))}", 'route': '/surrogate'}

    def _optimization_decision_status(self, config: dict[str, Any]) -> dict[str, str]:
        optimization_config = config.get('optimizationDecisionConfig') or {}
        objectives = optimization_config.get('objectives') or []
        decision_methods = optimization_config.get('decisionMethods') or []
        if not objectives:
            return {'status': 'UNCONFIGURED', 'message': '尚未配置优化目标', 'route': '/optimization'}
        status = 'VALIDATED' if optimization_config.get('validated') else 'CONFIGURED'
        return {'status': status, 'message': f'目标 {len(objectives)} 项，决策方法 {len(decision_methods)} 项', 'route': '/optimization'}

    def _default_registry(self) -> list[dict[str, Any]]:
        return [
            {
                'damperId': 'DMP-S-001',
                'materialType': 'VISCOUS',
                'placementLabel': '南塔梁端',
                'elementType': 'USER300',
                'elementId': 930001,
                'nodeI': 36,
                'nodeJ': 481,
                'direction': 'UX',
                'source': 'AUTO_GENERATED',
            }
        ]

    def _default_metrics(self) -> list[dict[str, Any]]:
        return [
            {
                'id': 'metric_beam_end_ux_peak',
                'name': '梁端纵向位移',
                'objectType': 'NODE',
                'objectId': '梁端代表节点 481、518',
                'component': 'UX',
                'responseType': 'DISPLACEMENT',
                'statistic': 'ABS_PEAK',
                'enabled': True,
                'autoGenerated': False,
                'sourceDescription': '主梁两端代表节点 UX 峰值',
            }
        ]

    def _csv_preview(self) -> dict[str, Any]:
        return {
            'headers': ['Time', 'Beam_End_Disp', 'Tower_Base_Shear', 'Tower_Base_Moment'],
            'previewRows': [['0.0', '0.0000', '52300000', '2740000000'], ['0.1', '0.0142', '52180000', '2732000000']],
            'totalRows': 6000,
        }

    def _command_stream_text(self, params: dict[str, Any]) -> str:
        module_config = params.get('moduleConfig') or {}
        modules = [str(module) for module in module_config.get('modules', [])]
        response_targets = [str(target) for target in module_config.get('responseTargets', [])]
        damper = module_config.get('damper') or {}
        material = damper.get('material') or {}
        placement = damper.get('placement') or {}
        lines = [
            '! MOMO assembled command stream',
            f"! solver={params.get('solver', 'ANSYS')} bridge={params.get('bridgeId', 'stbridge')} caseSet={params.get('caseSetId', 'case_set_local')}",
            '/PREP7',
        ]
        lines.extend(f'! MODULE {module}' for module in modules)
        if 'DAMPER' in modules:
            connection_node_pairs = placement.get('connectionNodePairs') or []
            node_summary = ','.join(
                f"{pair.get('nodeI')}-{pair.get('nodeJ')}"
                for pair in connection_node_pairs
                if isinstance(pair, dict)
            )
            lines.append(
                '! DAMPER '
                f"element={damper.get('elementType', 'USER300')} "
                f"material={material.get('materialType', 'UNKNOWN')} "
                f"layout={placement.get('layoutId', 'UNSPECIFIED')} "
                f"southCount={placement.get('southTowerCount', 0)} "
                f"northCount={placement.get('northTowerCount', 0)} "
                f"nodes={node_summary or ','.join(map(str, placement.get('connectionNodePairIds', [])))}"
            )
        lines.extend([
            f"! POSTPROCESS targets={','.join(response_targets)}",
            '! WRITE summary.json',
            '! WRITE timeseries.csv',
            '! WRITE objectives.csv',
            '! WRITE command_stream.sha256',
            'FINISH',
        ])
        return '\n'.join(lines) + '\n'

    def _load_curve_mime_type(self, format_name: str) -> str:
        normalized = format_name.upper()
        if normalized == 'SVG':
            return 'image/svg+xml'
        if normalized == 'PNG':
            return 'image/png'
        if normalized in ('TIFF', 'TIF'):
            return 'image/tiff'
        return 'application/octet-stream'

    def _estimate_doe_sample_count(self, params: dict[str, Any]) -> int:
        variables = [variable for variable in params.get('variables', []) if variable.get('enabled', True)]
        sampling = params.get('sampling') or {}
        base_count = int(sampling.get('lhsSamples') or 0)
        corner_count = 2 ** len(variables) if sampling.get('includeCorners') and variables else 0
        center_count = 1 if sampling.get('includeCenter') else 0
        return base_count + corner_count + center_count

    def _doe_matrix_preview(self, params: dict[str, Any]) -> dict[str, Any]:
        variables = [variable for variable in params.get('variables', []) if variable.get('enabled', True)]
        headers = ['caseId'] + [str(variable.get('id')) for variable in variables]
        rows: list[list[Any]] = []
        for index in range(1, min(self._estimate_doe_sample_count(params), 8) + 1):
            row: list[Any] = [f"{params.get('caseSetPrefix', 'doe_case')}_{index:03d}"]
            for variable_index, variable in enumerate(variables):
                lower = float(variable.get('min', 0))
                upper = float(variable.get('max', lower))
                ratio = ((index * (variable_index + 2)) % 9) / 8
                row.append(round(lower + (upper - lower) * ratio, 4))
            rows.append(row)
        return {'headers': headers, 'previewRows': rows, 'totalRows': self._estimate_doe_sample_count(params)}

    def _doe_response_dataset_preview(self, params: dict[str, Any]) -> dict[str, Any]:
        headers = ['caseId'] + list(params.get('responseTargets', []))
        rows: list[list[Any]] = []
        for index in range(1, 3):
            values: list[Any] = [f"{params.get('caseSetPrefix', 'doe_case')}_{index:03d}"]
            values.extend(round(0.05 + 0.01 * index + 0.005 * target_index, 4) for target_index in range(len(headers) - 1))
            rows.append(values)
        return {'headers': headers, 'previewRows': rows, 'totalRows': self._estimate_doe_sample_count(params)}

    def _doe_case_set_summary(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            'caseSetId': params.get('caseSetPrefix'),
            'designName': params.get('designName'),
            'solver': params.get('solver'),
            'scenarioType': params.get('scenarioType'),
            'executionGoal': params.get('executionGoal'),
            'vehicleLibraryId': params.get('vehicleLibraryId'),
            'sampleCount': self._estimate_doe_sample_count(params),
            'enabledVariableIds': [
                variable.get('id')
                for variable in params.get('variables', [])
                if variable.get('enabled', True)
            ],
            'responseTargets': params.get('responseTargets', []),
            **mock_demo_fields(),
        }

    def _content_from_preview(self, preview: Any) -> bytes:
        if isinstance(preview, str):
            return preview.encode('utf-8')
        if isinstance(preview, dict) and 'headers' in preview:
            rows = [','.join(preview['headers'])]
            rows.extend(','.join(map(str, row)) for row in preview.get('previewRows', []))
            return ('\n'.join(rows) + '\n').encode('utf-8-sig')
        return strict_json_dumps(preview, indent=2).encode('utf-8')

    def _load_state(self) -> bool:
        # 版本戳先于状态读取：加载期间的并发写只会让标记偏旧，
        # 最坏多做一次重载，绝不会漏掉更新。
        revision_before = self.repository.state_revision()
        payload = self.repository.load()
        if payload is None:
            return False
        self.repository._last_loaded_revision = revision_before
        artifacts = [
            ArtifactRecord(
                artifact=Artifact.model_validate(item['artifact']),
                preview=item.get('preview'),
                content=item['content'],
            )
            for item in payload.get('artifacts', [])
        ]
        jobs = [Job.model_validate(item) for item in payload.get('jobs', [])]
        engineering_config = payload.get('engineeringConfig')
        self.artifacts, self.jobs, self.engineering_config = artifacts, jobs, engineering_config
        self._loaded_engineering_config = deepcopy(engineering_config)
        return bool(self.jobs or self.artifacts or self.engineering_config)

    def _load_legacy_state(self) -> bool:
        if not LEGACY_STATE_PATH.exists():
            return False
        try:
            payload = json.loads(LEGACY_STATE_PATH.read_text(encoding='utf-8'))
            self.artifacts = [
                ArtifactRecord(
                    artifact=Artifact.model_validate(item['artifact']),
                    preview=item.get('preview'),
                    content=base64.b64decode(item.get('contentBase64', '')),
                )
                for item in payload.get('artifacts', [])
            ]
            self.jobs = [Job.model_validate(item) for item in payload.get('jobs', [])]
            self.engineering_config = payload.get('engineeringConfig')
            self.persist()
            self._loaded_engineering_config = deepcopy(self.engineering_config)
        except (OSError, ValueError, KeyError):
            self.jobs = []
            self.artifacts = []
            self.engineering_config = None
            return False
        return bool(self.jobs or self.artifacts or self.engineering_config)

    def _fail_orphaned_running_jobs(self) -> None:
        recovered = False
        for job in self.jobs:
            if job.status != 'RUNNING':
                continue
            if job.worker is not None and process_exists(job.worker.pid):
                logger.info(
                    '服务恢复时保留仍存活的 worker 任务',
                    extra={
                        'event': 'worker_recovered',
                        'job_id': job.job_id,
                        'worker_pid': job.worker.pid,
                    },
                )
                continue
            job.status = 'FAILED'
            job.finished_at = utc_now()
            job.progress = JobProgress(phase='恢复失败', message='服务重启时检测到遗留运行任务', percent=None)
            job.error = JobError(
                code='WORKER_RESTARTED',
                message='服务重启导致工作进程状态丢失；任务未自动重跑',
            )
            recovered = True
        if recovered:
            self.persist()

    def _paginate(self, items: list[Any], page: int, page_size: int) -> dict[str, Any]:
        safe_page = max(page, 1)
        safe_page_size = max(page_size, 1)
        start = (safe_page - 1) * safe_page_size
        data = items[start:start + safe_page_size]
        total = len(items)
        total_pages = (total + safe_page_size - 1) // safe_page_size
        return {
            'data': data,
            'pagination': {'page': safe_page, 'pageSize': safe_page_size, 'totalItems': total, 'totalPages': total_pages},
        }


platform_store = (
    None
    if os.environ.get('MOMO_PLATFORM_WORKER') == '1'
    else PlatformStore()
)
