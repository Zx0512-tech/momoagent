"""风工况阻尼器参数优化的模板、审批门、配置装配与证据口径测试。

风工况优化三个目标：梁端 X 向累计位移、塔底剪力、塔底弯矩，与地震侧同数量、
同单位口径。风荷载竖向（UY）施加在登记目标节点集上，位移与阻尼器同为 X 向 ——
这个方向组合是项目规定，测试固化它以防回归。

其中塔底弯矩只作目标、不作"不得劣于无控基线"的硬约束（依据见
test_wind_baseline_limits_are_a_strict_subset_of_wind_objectives 的实测注释）。
ANSYS 与 OPENSEESPY_INPROC 两个求解器都有已登记模板。
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.agents.analysis import ANALYSIS_WIND_TARGET_SET_ID
from app.agents.damper_optimization import (
    DamperOptimizationAgent,
    OPTIMIZATION_RESPONSE_CATALOG_BY_LOAD_KIND,
    WIND_OPTIMIZATION_WORKFLOW_PATHS,
    required_real_optimization_artifacts,
)
from app.services import platform_store as platform_store_module
from app.services.agent_engineering import build_engineering_contract
from app.services.platform_store import (
    OPTIMIZATION_OVERVIEW_ARTIFACT_NAMES,
    WIND_BASELINE_LIMIT_TARGETS,
    WIND_RESPONSE_TARGETS,
    PlatformStore,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
WIND_WORKFLOW_TEMPLATE = WIND_OPTIMIZATION_WORKFLOW_PATHS['ANSYS']


def _store(tmp_path: Path) -> PlatformStore:
    """隔离状态库。省略 state_path 会落到真实生产 sqlite，测试不得触碰它。"""
    return PlatformStore(
        state_path=tmp_path / 'state' / 'platform.sqlite3',
        recover_orphans=False,
    )
STANDARD_HEADER = (
    'time_s,load_kind,channel_id,application_type,target_type,target_id,'
    'component,quantity,value,unit'
)
# 累计位移固定取梁端节点 107 的 X 向行程。
WIND_CUMULATIVE_NODE = 107
WIND_RESPONSE_COMPONENT_X = 0


# ---------------------------------------------------------------------------
# 已登记模板：真实 preflight，不做桩
# ---------------------------------------------------------------------------

def test_wind_optimization_template_is_registered_and_single_objective() -> None:
    from pyansys_bridge.optimization.config_runner import preflight_config

    report = preflight_config(REPO_ROOT / WIND_WORKFLOW_TEMPLATE)

    assert report['kind'] == 'baseline_optimization_workflow'
    optimization = report['optimization']
    # 风工况按项目规定只优化梁端累计位移。塔底内力提取得到（真实求解已确认是
    # 非零物理量），但不作优化目标 —— 加进去会让"不得劣于无控基线"的约束集与
    # 目标集打架（实测竖向风下阻尼器抬高塔底弯矩），凭空多一层耦合。
    assert optimization['objective_names'] == ['wind:cumulative_displacement']
    assert optimization['active_load_cases'] == ['wind']
    assert optimization['load_case_types'] == {'wind': 'wind'}
    assert optimization['solver'] == 'ansys'
    # 基线半边仍是无控风工况。
    assert report['baseline']['load_type'] == 'wind'
    assert report['baseline']['solver'] == 'ansys'
    for section in ('baseline', 'optimization'):
        checks = report[section]['path_checks']
        assert checks
        assert all(check['exists'] is True for check in checks.values())


def test_wind_optimization_template_freezes_x_direction_cumulative_node() -> None:
    """位移取 X 向、累计位移取梁端节点，是规定而非默认值，模板必须显式写死。"""
    import json

    optimization_template = (
        REPO_ROOT / 'docs/examples/templates/ansys_run_wind_optimization_template.json'
    )
    config = json.loads(optimization_template.read_text(encoding='utf-8'))
    postprocessor = config['solver_kwargs']['postprocessor']

    assert postprocessor['cumulative_displacement_node'] == WIND_CUMULATIVE_NODE
    assert postprocessor['response_component'] == WIND_RESPONSE_COMPONENT_X
    assert WIND_CUMULATIVE_NODE in postprocessor['response_nodes']
    # 优化阶段必须保留真实阻尼器；omit_dampers 只属于无控基线。
    assert config['solver_kwargs']['omit_dampers'] is False
    assert config['solver_kwargs']['damper_module'] == 'damper_user300_viscous'


def test_wind_optimization_template_carries_verified_user300_calibration() -> None:
    from app.services.agent_evidence import build_solver_version_profile
    from app.agents.evidence_gates import solver_version_profile_passed

    profile = build_solver_version_profile(
        REPO_ROOT / WIND_WORKFLOW_TEMPLATE,
        solver='ANSYS',
    )

    assert profile['userElement']['name'] == 'USER300'
    assert profile['userElement']['calibrationHashVerified'] is True
    assert solver_version_profile_passed(profile, require_user300=True) is True


# ---------------------------------------------------------------------------
# 审批门
# ---------------------------------------------------------------------------

def _agent(*, readiness_status: str = 'READY') -> DamperOptimizationAgent:
    from pyansys_bridge.optimization.config_runner import preflight_config

    return DamperOptimizationAgent(
        planner=SimpleNamespace(),
        store=SimpleNamespace(),
        dispatcher=SimpleNamespace(),
        readiness_builder=lambda *_args, **_kwargs: {'status': readiness_status},
        preflight_runner=preflight_config,
        solver_profile_builder=lambda *_args, **_kwargs: {
            'schemaVersion': '1.0',
            'solver': {'name': 'ANSYS_MAPDL', 'version': '2024 R2'},
            'responseContract': {'id': 'ANSYS_RESPONSE_R1'},
            'userElement': {'name': 'USER300', 'calibrationHashVerified': True},
        },
    )


def _wind_mapping() -> dict:
    return {
        'version': 2,
        'loadKind': 'WIND',
        'time': {'column': 'time', 'unit': 's'},
        'channels': [{
            'valueColumn': 'wind_fy',
            'applicationType': 'NODAL_FORCE',
            'targetType': 'NODE_GROUP',
            'targetId': ANALYSIS_WIND_TARGET_SET_ID,
            'component': 'UY',
            'quantity': 'FORCE',
            'sourceUnit': 'N',
            'scale': 1.0,
        }],
    }


def _wind_run(
    *,
    solver: str = 'ANSYS',
    response_ids: list[str] | None = None,
) -> dict:
    return {
        'runId': 'run-wind-opt-1',
        'goal': '风荷载下优化阻尼器参数，使梁端累计位移最小',
        'taskType': 'DAMPER_OPTIMIZATION',
        'intent': {'loadKind': 'WIND'},
        'workflowContract': build_engineering_contract(
            task_type='DAMPER_OPTIMIZATION',
            solver=solver,
            damper_type='VISCOUS',
            response_ids=['cumulative_displacement'] if response_ids is None else response_ids,
            load_kind='WIND',
        ),
    }


def _prepare(agent, run, mapping, *, artifact_id='load-wind-1', sha='c' * 64):
    return agent.prepare_approval(
        run,
        mapping=mapping,
        standard_artifact_id=artifact_id,
        standard_sha256=sha,
    )


def test_wind_optimization_freezes_registered_wind_workflow_template() -> None:
    prepared = _prepare(_agent(), _wind_run(), _wind_mapping())

    assert prepared.passed is True
    frozen = prepared.frozen_action
    assert frozen['workflowConfigPath'] == WIND_WORKFLOW_TEMPLATE
    assert frozen['scenario'] == 'WIND'
    assert frozen['loadKind'] == 'WIND'
    assert frozen['runMode'] == 'REAL_BASELINE_OPTIMIZATION'
    assert frozen['loadDatasetArtifactId'] == 'load-wind-1'
    assert frozen['responseIds'] == ['cumulative_displacement']


def test_wind_optimization_freezes_load_target_set() -> None:
    """审批必须冻结施加目标节点集，否则执行侧失败关闭。

    `platform_store._apply_agent_standard_wind_load` 缺 loadTargetSetId 直接
    422 WIND_LOAD_TARGET_REQUIRED（不回退默认节点）。单次分析、方案比选、
    批量参数三条风链路都冻结它，优化链路必须同口径。
    """
    prepared = _prepare(_agent(), _wind_run(), _wind_mapping())

    assert prepared.frozen_action['loadTargetSetId'] == ANALYSIS_WIND_TARGET_SET_ID
    # 契约侧同步写入，供审批卡片与后续重建读取。
    assert prepared.contract_updates['loadTargetSetId'] == ANALYSIS_WIND_TARGET_SET_ID


def test_earthquake_optimization_does_not_freeze_wind_target_set() -> None:
    """地震工况不施加节点力，冻结风目标集会让审批出现无关字段。"""
    run = {
        'runId': 'run-eq-opt-2',
        'taskType': 'DAMPER_OPTIMIZATION',
        'intent': {'loadKind': 'EARTHQUAKE'},
        'workflowContract': build_engineering_contract(
            task_type='DAMPER_OPTIMIZATION',
            solver='ANSYS',
            damper_type='VISCOUS',
            response_ids=['max_girder_end_displacement'],
            load_kind='EARTHQUAKE',
        ),
    }

    prepared = _prepare(_agent(), run, {'loadKind': 'EARTHQUAKE', 'channels': []})

    assert 'loadTargetSetId' not in prepared.frozen_action
    assert 'loadTargetSetId' not in prepared.contract_updates


def test_earthquake_optimization_still_freezes_joint_template() -> None:
    run = {
        'runId': 'run-eq-opt-1',
        'goal': '地震工况优化阻尼器参数',
        'taskType': 'DAMPER_OPTIMIZATION',
        'intent': {'loadKind': 'EARTHQUAKE'},
        'workflowContract': build_engineering_contract(
            task_type='DAMPER_OPTIMIZATION',
            solver='ANSYS',
            damper_type='VISCOUS',
            response_ids=['max_girder_end_displacement'],
            load_kind='EARTHQUAKE',
        ),
    }

    prepared = _prepare(_agent(), run, {'loadKind': 'EARTHQUAKE', 'channels': []})

    assert prepared.passed is True
    assert prepared.frozen_action['scenario'] == 'EARTHQUAKE'
    assert 'joint_baseline_workflow' in prepared.frozen_action['workflowConfigPath']


def test_wind_optimization_on_openseespy_freezes_openseespy_template() -> None:
    """风工况 OpenSeesPy 优化模板已登记，与 ANSYS 同口径放行。

    这条测试此前断言失败关闭，前提是「风工况只有 ANSYS 有已登记模板」。
    模板补齐后前提不再成立；门本身的语义（只放行已登记模板）未变，
    未登记组合的失败关闭仍由 TRAFFIC/未登记工况那几条覆盖。
    """
    prepared = _prepare(_agent(), _wind_run(solver='OPENSEESPY_INPROC'), _wind_mapping())

    assert prepared.passed is True
    frozen = prepared.frozen_action
    assert frozen['workflowConfigPath'] == (
        'docs/examples/templates/openseespy_inproc_run_wind_baseline_workflow_template.json'
    )
    assert frozen['solver'] == 'OPENSEESPY_INPROC'
    assert frozen['scenario'] == 'WIND'
    # 风荷载施加对象必须冻结，否则执行侧 _apply_agent_standard_wind_load 失败关闭。
    assert frozen['loadTargetSetId'] == ANALYSIS_WIND_TARGET_SET_ID


def test_wind_optimization_rejects_tower_base_responses() -> None:
    """塔底内力提取得到，但不是风工况优化目标，审批必须失败关闭。

    真实求解确认风工况能拿到非零塔底剪力/弯矩，所以这不是"提取不出来"。
    按项目规定风工况只优化累计位移，放行塔底内力会让审批冻结的目标与模板
    objective_specs 不一致。
    """
    prepared = _prepare(
        _agent(),
        _wind_run(response_ids=['cumulative_displacement', 'max_tower_base_shear']),
        _wind_mapping(),
    )

    assert prepared.passed is False
    assert prepared.frozen_action is None
    assert prepared.failure_status == 'UNSUPPORTED'


def test_wind_optimization_rejects_responses_outside_wind_catalog() -> None:
    """风工况模板只提取累计位移；其余响应必须失败关闭。

    max_acceleration 在 RESPONSE_CATALOG 里合法，但风工况模板的
    objective_specs 不含它，放行会让审批冻结的目标与模板不一致。
    """
    prepared = _prepare(
        _agent(),
        _wind_run(response_ids=['max_acceleration']),
        _wind_mapping(),
    )

    assert prepared.passed is False
    assert prepared.frozen_action is None
    assert prepared.failure_status == 'UNSUPPORTED'


def test_wind_response_catalog_is_cumulative_displacement_only() -> None:
    """目录、目标表、模板 objective_specs 必须三方同口径。

    三者任一处漏改，审批冻结的目标就会与模板实际优化的目标不一致。
    """
    assert OPTIMIZATION_RESPONSE_CATALOG_BY_LOAD_KIND['WIND'] == frozenset({
        'cumulative_displacement',
    })
    assert [target['objective'] for target in WIND_RESPONSE_TARGETS] == [
        'cumulative_displacement',
    ]
    assert [target['fullObjective'] for target in WIND_RESPONSE_TARGETS] == [
        'wind:cumulative_displacement',
    ]
    assert [target['displayUnit'] for target in WIND_RESPONSE_TARGETS] == ['m']


def test_wind_baseline_limits_match_the_single_wind_objective() -> None:
    """约束集与目标集在单目标下相同，但仍是两个独立概念。

    目标集决定 Pareto 前沿与熵权，约束集是"不得劣于无控基线"的硬可行性门。
    地震 ANSYS 链两者就不相等（joint 模板 4 个 objective_specs、3 个 baseline
    limits），所以这里断言的是"当前相等"，不是"必须永远相等"。
    """
    objectives = [target['objective'] for target in WIND_RESPONSE_TARGETS]
    limits = [target['objective'] for target in WIND_BASELINE_LIMIT_TARGETS]

    assert limits == objectives == ['cumulative_displacement']


def test_wind_workflow_templates_constrain_the_registered_objectives() -> None:
    """两个已登记风工况 workflow 模板的 baseline_objective_limits 必须与约束集一致。"""
    expected = [target['objective'] for target in WIND_BASELINE_LIMIT_TARGETS]
    for template_name in (
        'ansys_run_wind_baseline_workflow_template.json',
        'openseespy_inproc_run_wind_baseline_workflow_template.json',
    ):
        payload = json.loads(
            (REPO_ROOT / 'docs' / 'examples' / 'templates' / template_name).read_text(
                encoding='utf-8',
            )
        )
        limits = payload['baseline_objective_limits']
        assert limits['scenario'] == 'wind', template_name
        assert limits['objectives'] == expected, template_name


def test_unregistered_load_kind_optimization_fails_closed() -> None:
    # TRAFFIC 已登记优化模板（见 test_traffic_damper_optimization），
    # 这里改用仍未登记的 GENERIC_NODAL 检验门禁本身。
    run = _wind_run()
    run['intent'] = {'loadKind': 'GENERIC_NODAL'}

    prepared = _prepare(_agent(), run, {'loadKind': 'GENERIC_NODAL', 'channels': []})

    assert prepared.passed is False
    assert prepared.failure_status == 'UNSUPPORTED'


# ---------------------------------------------------------------------------
# 平台侧请求门禁
# ---------------------------------------------------------------------------

def _optimization_params(**overrides) -> dict:
    return {
        'solver': 'ANSYS',
        'scenario': 'WIND',
        'executionTarget': 'OPTIMIZATION_DECISION',
        'runMode': 'REAL_BASELINE_OPTIMIZATION',
        **overrides,
    }


def test_store_accepts_ansys_wind_optimization_request(tmp_path: Path) -> None:
    store = _store(tmp_path)

    assert store._is_real_baseline_optimization_request(
        'MULTI_OBJECTIVE_OPTIMIZATION',
        _optimization_params(),
    ) is True


def test_store_accepts_openseespy_wind_optimization_request(tmp_path: Path) -> None:
    """放行口径取自模板登记表，OpenSeesPy 风模板补齐后与 ANSYS 同口径。"""
    store = _store(tmp_path)

    assert store._is_real_baseline_optimization_request(
        'MULTI_OBJECTIVE_OPTIMIZATION',
        _optimization_params(solver='OPENSEESPY_INPROC'),
    ) is True


def test_store_rejects_unregistered_scenario_optimization_request(tmp_path: Path) -> None:
    # TRAFFIC 已登记 ANSYS 优化模板，用仍未登记的 GENERIC_NODAL 检验门禁。
    store = _store(tmp_path)

    assert store._is_real_baseline_optimization_request(
        'MULTI_OBJECTIVE_OPTIMIZATION',
        _optimization_params(scenario='GENERIC_NODAL'),
    ) is False


def test_store_rejects_cross_load_kind_template_mismatch(tmp_path: Path) -> None:
    """风工况配地震模板必须失败关闭，反向同理。"""
    store = _store(tmp_path)

    with pytest.raises(HTTPException) as error:
        store._baseline_optimization_workflow_config_path(_optimization_params(
            workflowConfigPath='docs/examples/templates/ansys_run_joint_baseline_workflow_template.json',
        ))
    assert error.value.detail['code'] == 'INVALID_WORKFLOW_CONFIG_PATH'

    with pytest.raises(HTTPException) as error:
        store._baseline_optimization_workflow_config_path(_optimization_params(
            scenario='EARTHQUAKE',
            workflowConfigPath=WIND_WORKFLOW_TEMPLATE,
        ))
    assert error.value.detail['code'] == 'INVALID_WORKFLOW_CONFIG_PATH'


def test_store_resolves_registered_wind_template_by_scenario(tmp_path: Path) -> None:
    store = _store(tmp_path)

    resolved = store._baseline_optimization_workflow_config_path(_optimization_params())

    assert resolved.name == Path(WIND_WORKFLOW_TEMPLATE).name


# ---------------------------------------------------------------------------
# 配置装配：风荷载同时注入基线与优化配置
# ---------------------------------------------------------------------------

def _standard_wind_csv() -> bytes:
    lines = [STANDARD_HEADER]
    for time_s, value in [(0.0, 100.0), (1.0, 120.0), (2.0, 90.0)]:
        lines.append(
            f'{time_s:g},WIND,channel_1,NODAL_FORCE,NODE_GROUP,'
            f'{ANALYSIS_WIND_TARGET_SET_ID},UY,FORCE,{value:g},N'
        )
    return ('\n'.join(lines) + '\n').encode('utf-8')


def _wind_load_params(**overrides) -> dict:
    return {
        'solver': 'ANSYS',
        'scenario': 'WIND',
        'loadKind': 'WIND',
        'loadDatasetArtifactId': 'load-wind-1',
        'loadDatasetSha256': 'd' * 64,
        'loadTargetSetId': ANALYSIS_WIND_TARGET_SET_ID,
        **overrides,
    }


def _wind_load_store(tmp_path: Path, monkeypatch) -> PlatformStore:
    store = _store(tmp_path)
    monkeypatch.setattr(
        store,
        'get_artifact',
        lambda _artifact_id: SimpleNamespace(
            artifact=SimpleNamespace(
                artifact_id='load-wind-1', kind='CSV_TIMESERIES', sha256='d' * 64,
            ),
            content=_standard_wind_csv(),
        ),
    )
    return store


def test_wind_load_injects_into_both_baseline_and_optimization(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = _wind_load_store(tmp_path, monkeypatch)
    # 两侧都预置 wind_load_component：命名 component 会让 ANSYS 跳过冻结的目标
    # 节点集，必须被移除。源 metadata 为空时这条断言是空的，所以显式种上。
    baseline_config: dict = {
        'bridge_model': {
            'name': 'STbridge',
            'metadata': {'wind_load_component': 'GIRDER_WIND'},
        },
    }
    optimization_config: dict = {
        'bridge_model': {
            'name': 'STbridge',
            'metadata': {'wind_load_component': 'GIRDER_WIND'},
        },
        'load_cases': [{'name': 'wind', 'load_type': 'wind', 'scale': 1.0}],
        'active_load_cases': ['wind'],
    }

    evidence = store._apply_agent_standard_wind_load(
        baseline_config,
        tmp_path,
        _wind_load_params(),
        'ANSYS',
        optimization_config=optimization_config,
    )

    assert evidence['component'] == 'UY'
    assert evidence['targetSetId'] == ANALYSIS_WIND_TARGET_SET_ID
    assert evidence['timeStepS'] == 1.0
    solver_input = Path(evidence['solverInputPath'])
    assert solver_input.name == 'agent_wind_nodal_force_n.txt'

    # 基线用单数 load_case 字段。
    assert baseline_config['load_case']['load_type'] == 'wind'
    assert baseline_config['load_case']['path'] == str(solver_input)
    # 优化用 load_cases 列表，且只保留 wind 一个活动工况。
    assert optimization_config['active_load_cases'] == ['wind']
    assert len(optimization_config['load_cases']) == 1
    wind_case = optimization_config['load_cases'][0]
    assert wind_case['load_type'] == 'wind'
    assert wind_case['path'] == str(solver_input)
    assert wind_case['dt'] == 1.0
    # 目标节点集必须写回两侧 bridge_model，否则求解器会退回默认节点。
    for config in (baseline_config, optimization_config):
        nodes = config['bridge_model']['metadata']['wind_girder_load_nodes']
        assert nodes
        assert 'wind_load_component' not in config['bridge_model']['metadata']


def test_wind_load_requires_frozen_artifact_and_target_set(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = _wind_load_store(tmp_path, monkeypatch)

    with pytest.raises(HTTPException) as error:
        store._apply_agent_standard_wind_load(
            {'bridge_model': {}},
            tmp_path,
            _wind_load_params(loadDatasetArtifactId=None),
            'ANSYS',
        )
    assert error.value.detail['code'] == 'WIND_LOAD_ARTIFACT_REQUIRED'

    with pytest.raises(HTTPException) as error:
        store._apply_agent_standard_wind_load(
            {'bridge_model': {}},
            tmp_path,
            _wind_load_params(loadTargetSetId='NOT_REGISTERED'),
            'ANSYS',
        )
    assert error.value.detail['code'] == 'WIND_LOAD_TARGET_REQUIRED'


def test_wind_optimization_config_is_single_objective_with_dampers(tmp_path: Path) -> None:
    store = _store(tmp_path)
    source = {
        'bridge_model': {'name': 'STbridge', 'metadata': {}},
        'load_cases': [{'name': 'wind', 'load_type': 'wind', 'scale': 1.0}],
        'solver_kwargs': {
            'omit_dampers': True,
            'damper_module': 'damper_viscous',
            'postprocessor': {'mode': 'ansys-dpf-rst', 'response_nodes': [36, 107]},
        },
    }

    config = store._wind_optimization_config(
        source,
        tmp_path,
        tmp_path / 'out',
        'ANSYS',
        [{'c': 1000.0, 'alpha': 0.5}, {'c': 2000.0, 'alpha': 0.6}],
    )

    assert config['objective_specs'] == [
        {'scenario': 'wind', 'objective': 'cumulative_displacement', 'weight': 1.0},
    ]
    assert config['active_load_cases'] == ['wind']
    assert config['solver'] == 'ansys'
    assert config['solver_kwargs']['omit_dampers'] is False
    assert config['solver_kwargs']['damper_module'] == 'damper_user300_viscous'
    postprocessor = config['solver_kwargs']['postprocessor']
    assert postprocessor['cumulative_displacement_node'] == WIND_CUMULATIVE_NODE
    assert postprocessor['response_component'] == WIND_RESPONSE_COMPONENT_X
    assert config['n_doe_samples'] == 2
    assert config['run_validation'] is True
    assert config['run_review'] is True
    # 基线指标约束由工作流在基线完成后注入，不进 DOE 配置。
    assert 'baseline_objective_limits' not in config


def test_wind_optimization_config_assembles_openseespy_solver_block(tmp_path: Path) -> None:
    """OpenSeesPy 风优化装配：三处与 ANSYS 不能共用的设置必须正确。"""
    store = _store(tmp_path)

    config = store._wind_optimization_config(
        {
            'bridge_model': {'name': 'STbridge', 'metadata': {'deck_nodes': [36, 107]}},
            'load_cases': [{'name': 'wind', 'load_type': 'wind'}],
            'solver': {
                'type': 'openseespy_inproc',
                'damper_module': 'damper_viscous',
                'model_path': 'stbridge_opensees_modal_builder.py',
                'response_nodes': [36, 107],
                'omit_dampers': True,
                'postprocessor': {'mode': 'opensees-csv', 'source': 'timeseries.csv'},
            },
        },
        tmp_path,
        tmp_path / 'out',
        'OPENSEESPY_INPROC',
        [{'c': 1000.0, 'alpha': 0.5}],
    )

    # 目标与 ANSYS 侧逐字相同：同一个物理目标，不因求解器而变。
    assert config['objective_specs'] == [
        {'scenario': 'wind', 'objective': 'cumulative_displacement', 'weight': 1.0},
    ]
    assert config['active_load_cases'] == ['wind']
    solver = config['solver']
    assert solver['type'] == 'openseespy_inproc'
    # damper_user300_viscous 是 ANSYS 独有别名，OpenSees 侧不得出现。
    assert solver['damper_module'] == 'damper_viscous'
    assert solver['omit_dampers'] is False
    # opensees-csv 后处理没有 cumulative_displacement_node 选择器，displacement 列是
    # response_nodes 的逐步 max-abs 包络；必须收窄为单节点才与 ANSYS 同定义。
    assert solver['response_nodes'] == [WIND_CUMULATIVE_NODE]
    # openseespy_inproc 不支持线程并行，config_runner 会直接 raise。
    assert config['parallel']['mode'] == 'process'
    assert 'baseline_objective_limits' not in config


def test_wind_optimization_config_rejects_unregistered_solver(tmp_path: Path) -> None:
    """放行面取自已登记模板表；未登记的求解器仍须失败关闭。"""
    store = _store(tmp_path)

    with pytest.raises(HTTPException) as error:
        store._wind_optimization_config(
            {
                'bridge_model': {'name': 'STbridge', 'metadata': {}},
                'load_cases': [{'name': 'wind', 'load_type': 'wind'}],
            },
            tmp_path,
            tmp_path / 'out',
            'OPENSEES',
            [{'c': 1000.0, 'alpha': 0.5}],
        )
    assert error.value.detail['code'] == 'UNSUPPORTED_REAL_WORKFLOW_SOLVER'


# ---------------------------------------------------------------------------
# 证据口径：概览制品按荷载类型命名
# ---------------------------------------------------------------------------

def test_overview_artifact_name_follows_load_kind() -> None:
    assert OPTIMIZATION_OVERVIEW_ARTIFACT_NAMES['WIND'] == 'real_wind_workflow_overview.json'
    assert (
        OPTIMIZATION_OVERVIEW_ARTIFACT_NAMES['EARTHQUAKE']
        == 'real_earthquake_workflow_overview.json'
    )


def test_required_artifacts_follow_load_kind() -> None:
    wind = required_real_optimization_artifacts('WIND')
    earthquake = required_real_optimization_artifacts('EARTHQUAKE')

    assert 'real_wind_workflow_overview.json' in wind
    assert 'real_earthquake_workflow_overview.json' not in wind
    assert 'real_earthquake_workflow_overview.json' in earthquake
    assert 'real_wind_workflow_overview.json' not in earthquake
    # 其余必需制品两个工况一致。
    assert wind - {'real_wind_workflow_overview.json'} == (
        earthquake - {'real_earthquake_workflow_overview.json'}
    )
    # 车流有自己的概览制品名，同样不会串到风或地震的文件里。
    traffic = required_real_optimization_artifacts('TRAFFIC')
    assert 'real_traffic_workflow_overview.json' in traffic
    assert traffic - {'real_traffic_workflow_overview.json'} == (
        earthquake - {'real_earthquake_workflow_overview.json'}
    )
    # 未知荷载类型回退到地震口径，不会静默放空清单。
    assert required_real_optimization_artifacts('GENERIC_NODAL') == earthquake


def test_wind_overview_reports_wind_scenario_and_three_targets(tmp_path: Path) -> None:
    store = _store(tmp_path)
    prepared = SimpleNamespace(
        workflow_config_path=Path('run/workflow.json'),
        source_workflow_config_path=Path('templates/wind_workflow.json'),
        solver='ANSYS',
        doe_designs=[{'c': 1000.0, 'alpha': 0.5}],
        requested_doe_count=1,
        doe_design_sha256='e' * 64,
        solver_parallel={'enabled': True, 'max_workers': 4},
        output_dir=Path('run'),
    )

    overview = store._earthquake_workflow_overview(
        workflow_summary={},
        optimization_summary={
            'optimization': {
                'objective_names': ['wind:cumulative_displacement'],
                'best_objectives': [0.42],
                'parameter_names': ['c', 'alpha'],
                'best_design': [3000.0, 0.6],
            },
        },
        baseline_summary={'objectives': {'wind:cumulative_displacement': 0.9}},
        prepared_workflow=prepared,
        load_kind='WIND',
    )

    assert overview['scenario'] == 'WIND'
    # 概览只列风工况自己的单一目标，不能混进地震的三个 targetId。
    assert [target['targetId'] for target in overview['responseTargets']] == [
        'windBeamEndCumulativeDisplacement',
    ]
    assert overview['recommendedObjectives'] == {'cumulative_displacement': 0.42}
    # 基线值按 wind: 前缀取，不是 earthquake:。
    assert overview['baselineObjectives'] == {'cumulative_displacement': 0.9}
    assert overview['recommendedParameters'] == {'c': 3000.0, 'alpha': 0.6}


def test_earthquake_overview_keeps_joint_targets(tmp_path: Path) -> None:
    store = _store(tmp_path)
    prepared = SimpleNamespace(
        workflow_config_path=Path('run/workflow.json'),
        source_workflow_config_path=Path('templates/joint_workflow.json'),
        solver='ANSYS',
        doe_designs=[{'c': 1000.0, 'alpha': 0.5}],
        requested_doe_count=1,
        doe_design_sha256='f' * 64,
        solver_parallel={'enabled': True, 'max_workers': 4},
        output_dir=Path('run'),
    )

    overview = store._earthquake_workflow_overview(
        workflow_summary={},
        optimization_summary={},
        baseline_summary={'objectives': {'max_girder_end_displacement': 0.3}},
        prepared_workflow=prepared,
    )

    assert overview['scenario'] == 'EARTHQUAKE'
    target_ids = [target['targetId'] for target in overview['responseTargets']]
    assert 'beamEndDisplacement' in target_ids
    assert 'operationCumulativeDisplacement' in target_ids
    assert 'windBeamEndCumulativeDisplacement' not in target_ids
