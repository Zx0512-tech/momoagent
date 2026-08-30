"""车流工况阻尼器参数优化的模板、审批门、配置装配与证据口径测试（两个求解器）。

车流的唯一优化目标是梁端 X 向累计位移最小；塔底剪力与弯矩刻意不进目标目录
（车流是竖向移动荷载，阻尼器沿 X 向工作，塔底内力不是这条链要控的量）。
车流荷载本身是稠密矩阵（1 个时间列 + 163 个节点力列），必须与逐节点列映射
制品成对冻结才可解释。这两点是项目规定，测试固化它们以防回归。
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.agents.analysis import ANALYSIS_TRAFFIC_TARGET_SET_ID
from app.agents.damper_optimization import (
    DamperOptimizationAgent,
    OPTIMIZATION_RESPONSE_CATALOG_BY_LOAD_KIND,
    TRAFFIC_OPTIMIZATION_WORKFLOW_PATHS,
    WIND_OPTIMIZATION_WORKFLOW_PATHS,
    required_real_optimization_artifacts,
)
from app.services.agent_engineering import build_engineering_contract
from app.services.platform_store import (
    OPTIMIZATION_OVERVIEW_ARTIFACT_NAMES,
    TRAFFIC_CUMULATIVE_DISPLACEMENT_NODE,
    TRAFFIC_RESPONSE_TARGETS,
    PlatformStore,
)
from pyansys_bridge.core.ansys_load_targets import (
    DEFAULT_STBRIDGE_RANDOM_TRAFFIC_DECK_NODES as DEFAULT_STBRIDGE_TRAFFIC_LOAD_NODES,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
TRAFFIC_WORKFLOW_TEMPLATE = TRAFFIC_OPTIMIZATION_WORKFLOW_PATHS['ANSYS']
TRAFFIC_OPENSEESPY_WORKFLOW_TEMPLATE = TRAFFIC_OPTIMIZATION_WORKFLOW_PATHS['OPENSEESPY_INPROC']
TRAFFIC_OPTIMIZATION_TEMPLATE = (
    'docs/examples/templates/ansys_run_traffic_optimization_template.json'
)
# 累计位移固定取梁端节点 1 的 X 向行程（风工况取的是 107，两者不可互换）。
TRAFFIC_RESPONSE_COMPONENT_X = 0
TRAFFIC_NODES = list(DEFAULT_STBRIDGE_TRAFFIC_LOAD_NODES)
# 稠密矩阵制品与逐节点 mapping 制品成对冻结，执行侧按 ID 分别取两个制品。
MATRIX_ARTIFACT_ID = 'load-traffic-1'
MATRIX_SHA256 = 'd' * 64
MAPPING_ARTIFACT_ID = 'load-traffic-mapping-1'
MAPPING_SHA256 = 'f' * 64
FOREIGN_SHA256 = 'e' * 64
MATRIX_TIMES = (0.0, 1.0, 2.0)


def _store(tmp_path: Path) -> PlatformStore:
    """隔离状态库。省略 state_path 会落到真实生产 sqlite，测试不得触碰它。"""
    return PlatformStore(
        state_path=tmp_path / 'state' / 'platform.sqlite3',
        recover_orphans=False,
    )


# ---------------------------------------------------------------------------
# 已登记模板：真实 preflight，不做桩
# ---------------------------------------------------------------------------

def test_traffic_optimization_template_is_registered_and_single_objective() -> None:
    from pyansys_bridge.optimization.config_runner import preflight_config

    report = preflight_config(REPO_ROOT / TRAFFIC_WORKFLOW_TEMPLATE)

    assert report['kind'] == 'baseline_optimization_workflow'
    optimization = report['optimization']
    # 唯一目标：车流工况累计位移。塔底剪力/弯矩不进车流优化，多目标是后续工作。
    assert optimization['objective_names'] == ['traffic:cumulative_displacement']
    assert optimization['active_load_cases'] == ['traffic']
    assert optimization['load_case_types'] == {'traffic': 'traffic'}
    assert optimization['solver'] == 'ansys'
    # 基线半边仍是无控车流工况。
    assert report['baseline']['load_type'] == 'traffic'
    assert report['baseline']['solver'] == 'ansys'
    for section in ('baseline', 'optimization'):
        checks = report[section]['path_checks']
        assert checks
        assert all(check['exists'] is True for check in checks.values())


def test_traffic_optimization_template_freezes_x_direction_cumulative_node() -> None:
    """位移取 X 向、累计位移取梁端节点 1，是规定而非默认值，模板必须显式写死。

    节点 1 与 72 是两个梁端，momo traffic_base 实测节点 1 的累计行程更大
    （1.2479 m > 1.2419 m）；优化目标是累计位移，所以取 1，不是风工况的 107。
    """
    config = json.loads(
        (REPO_ROOT / TRAFFIC_OPTIMIZATION_TEMPLATE).read_text(encoding='utf-8')
    )
    postprocessor = config['solver_kwargs']['postprocessor']

    assert postprocessor['cumulative_displacement_node'] == TRAFFIC_CUMULATIVE_DISPLACEMENT_NODE
    assert TRAFFIC_CUMULATIVE_DISPLACEMENT_NODE == 1
    assert postprocessor['response_component'] == TRAFFIC_RESPONSE_COMPONENT_X
    assert TRAFFIC_CUMULATIVE_DISPLACEMENT_NODE in postprocessor['response_nodes']
    # 优化阶段必须保留真实阻尼器；omit_dampers 只属于无控基线。
    assert config['solver_kwargs']['omit_dampers'] is False
    assert config['solver_kwargs']['damper_module'] == 'damper_user300_viscous'


def test_traffic_optimization_template_maps_163_deck_nodes_without_component() -> None:
    """声明 traffic_load_component 会让 ANSYS 只渲染一列 TABLE。

    ansys_load_rendering 的 component 分支先 return，163 个逐节点列会被静默
    丢弃，且丢的是空间分布而非量纲，结果看上去仍然"合理"，所以必须守住。
    """
    config = json.loads(
        (REPO_ROOT / TRAFFIC_OPTIMIZATION_TEMPLATE).read_text(encoding='utf-8')
    )
    metadata = config['bridge_model']['metadata']

    assert metadata['traffic_load_nodes'] == TRAFFIC_NODES
    assert len(TRAFFIC_NODES) == 163
    assert 'traffic_load_component' not in metadata


def test_traffic_optimization_template_ships_no_bundled_traffic_record() -> None:
    """车流没有项目内置记录：路径/时间步只能来自审批冻结的制品对。

    模板若自带 path，执行侧一旦漏接绑定就会静默用模板荷载跑完全程。
    """
    config = json.loads(
        (REPO_ROOT / TRAFFIC_OPTIMIZATION_TEMPLATE).read_text(encoding='utf-8')
    )
    load_cases = config['load_cases']

    assert [case['load_type'] for case in load_cases] == ['traffic']
    assert config['active_load_cases'] == ['traffic']
    traffic_case = load_cases[0]
    # 竖向（-Y）移动荷载。
    assert traffic_case['direction'] == {'x': 0.0, 'y': -1.0, 'z': 0.0}
    for field in ('path', 'dt', 'duration'):
        assert field not in traffic_case
    assert config['objective_specs'] == [
        {'scenario': 'traffic', 'objective': 'cumulative_displacement', 'weight': 1.0}
    ]


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


def _traffic_mapping(**overrides) -> dict:
    return {
        'version': 2,
        'loadKind': 'TRAFFIC',
        'time': {'column': 'time_s', 'unit': 's'},
        'channels': [{
            # 车流是移动荷载：单条通道描述整个 163 列稠密矩阵，应用类型用
            # NODAL_FORCE_MATRIX 与风的单列总力（NODAL_FORCE）区分。
            'valueColumn': 'node_fy_N_matrix',
            'applicationType': 'NODAL_FORCE_MATRIX',
            'targetType': 'NODE_GROUP',
            'targetId': ANALYSIS_TRAFFIC_TARGET_SET_ID,
            'component': 'UY',
            'quantity': 'FORCE',
            'sourceUnit': 'N',
            'scale': 1.0,
            'matrixColumnCount': len(TRAFFIC_NODES),
        }],
        'solver': 'ANSYS',
        'pointMappingArtifactId': MAPPING_ARTIFACT_ID,
        'pointMappingSha256': MAPPING_SHA256,
        **overrides,
    }


def _traffic_run(
    *,
    solver: str = 'ANSYS',
    response_ids: list[str] | None = None,
) -> dict:
    return {
        'runId': 'run-traffic-opt-1',
        'goal': '车流荷载下优化阻尼器参数，使梁端累计位移最小',
        'taskType': 'DAMPER_OPTIMIZATION',
        'intent': {'loadKind': 'TRAFFIC'},
        'workflowContract': build_engineering_contract(
            task_type='DAMPER_OPTIMIZATION',
            solver=solver,
            damper_type='VISCOUS',
            response_ids=['cumulative_displacement'] if response_ids is None else response_ids,
            load_kind='TRAFFIC',
        ),
    }


def _prepare(agent, run, mapping, *, artifact_id=MATRIX_ARTIFACT_ID, sha='c' * 64):
    return agent.prepare_approval(
        run,
        mapping=mapping,
        standard_artifact_id=artifact_id,
        standard_sha256=sha,
    )


def test_traffic_optimization_freezes_registered_traffic_workflow_template() -> None:
    prepared = _prepare(_agent(), _traffic_run(), _traffic_mapping())

    assert prepared.passed is True
    frozen = prepared.frozen_action
    assert frozen['workflowConfigPath'] == TRAFFIC_WORKFLOW_TEMPLATE
    assert frozen['scenario'] == 'TRAFFIC'
    assert frozen['loadKind'] == 'TRAFFIC'
    assert frozen['runMode'] == 'REAL_BASELINE_OPTIMIZATION'
    assert frozen['loadDatasetArtifactId'] == MATRIX_ARTIFACT_ID
    assert frozen['responseIds'] == ['cumulative_displacement']
    # 施加对象是冻结的甲板节点集，执行侧缺这个字段会 422 而不是回退默认节点。
    assert frozen['loadTargetSetId'] == ANALYSIS_TRAFFIC_TARGET_SET_ID
    # 车流独有：矩阵制品与逐节点 mapping 制品成对冻结，DOE 每个设计点都按
    # 同一份列映射取列，否则 163 列会错位（错位是量纲不可见的）。
    assert frozen['loadPointMappingArtifactId'] == MAPPING_ARTIFACT_ID
    assert frozen['loadPointMappingSha256'] == MAPPING_SHA256
    provenance = {item['field']: item['value'] for item in frozen['inputProvenance']}
    assert provenance['loadTargetSetId'] == ANALYSIS_TRAFFIC_TARGET_SET_ID
    assert provenance['workflowConfigPath'] == TRAFFIC_WORKFLOW_TEMPLATE


def test_traffic_optimization_on_openseespy_freezes_openseespy_template() -> None:
    """车流 OpenSeesPy 优化模板已登记，与 ANSYS 同口径放行。

    这条测试此前断言失败关闭，前提是「车流优化只有 ANSYS 有已登记模板」。
    模板补齐后前提不再成立；门本身的语义（只放行已登记模板）未变，
    未登记求解器的失败关闭仍由 test_traffic_optimization_config_rejects_unregistered_solver 覆盖。
    """
    prepared = _prepare(
        _agent(),
        _traffic_run(solver='OPENSEESPY_INPROC'),
        _traffic_mapping(solver='OPENSEESPY_INPROC'),
    )

    assert prepared.passed is True
    frozen = prepared.frozen_action
    assert frozen['workflowConfigPath'] == TRAFFIC_OPENSEESPY_WORKFLOW_TEMPLATE
    assert frozen['solver'] == 'OPENSEESPY_INPROC'
    assert frozen['scenario'] == 'TRAFFIC'
    # 车流的矩阵制品与逐节点 mapping 同样要成对冻结：OpenSees 的 traffic.pyfrag
    # 读的是同一个 source_column，缺 mapping 会退化成整列施加到每个节点。
    assert frozen['loadTargetSetId'] == ANALYSIS_TRAFFIC_TARGET_SET_ID
    assert frozen['loadPointMappingArtifactId'] == MAPPING_ARTIFACT_ID


@pytest.mark.parametrize('response_id', ['max_tower_base_shear', 'max_tower_base_moment'])
def test_traffic_optimization_rejects_tower_base_responses(response_id: str) -> None:
    """塔底剪力/弯矩不是车流优化目标，选中必须失败关闭。

    这两个响应在全局 RESPONSE_CATALOG 里是合法的（地震链要用），所以工程
    合约照样能构造出来；真正拦住它们的是按荷载类型分叉的目录。
    """
    prepared = _prepare(
        _agent(),
        _traffic_run(response_ids=[response_id]),
        _traffic_mapping(),
    )

    assert prepared.passed is False
    assert prepared.frozen_action is None
    assert prepared.failure_status == 'UNSUPPORTED'
    assert prepared.preflight == {'passed': False, 'reason': 'PRODUCTION_GATE'}


def test_traffic_response_catalog_is_cumulative_displacement_only() -> None:
    catalog = OPTIMIZATION_RESPONSE_CATALOG_BY_LOAD_KIND['TRAFFIC']

    assert catalog == frozenset({'cumulative_displacement'})
    assert 'max_tower_base_shear' not in catalog
    assert 'max_tower_base_moment' not in catalog
    assert [target['objective'] for target in TRAFFIC_RESPONSE_TARGETS] == [
        'cumulative_displacement'
    ]
    assert [target['fullObjective'] for target in TRAFFIC_RESPONSE_TARGETS] == [
        'traffic:cumulative_displacement'
    ]
    assert [target['targetId'] for target in TRAFFIC_RESPONSE_TARGETS] == [
        'trafficBeamEndCumulativeDisplacement'
    ]


def test_traffic_optimization_approval_does_not_gate_missing_point_mapping() -> None:
    """现状固化：优化审批门不校验 mapping 对，缺失时冻结成 None 照样通过。

    ANALYSIS / 比选 / 扫描三条链都在审批期就用 `_load_kind_gate` 拦下缺失的
    逐节点 mapping（返回 TRAFFIC_LOAD_POINT_MAPPING_REQUIRED），
    `_prepare_engineering_optimization` 没有调这个门，真实的失败关闭推迟到执行
    侧荷载绑定（见 test_traffic_load_requires_frozen_point_mapping_pair）。
    端到端仍然失败关闭，但代价是要跑到绑定阶段才发现。
    """
    prepared = _prepare(
        _agent(),
        _traffic_run(),
        _traffic_mapping(pointMappingArtifactId=None, pointMappingSha256=None),
    )

    assert prepared.passed is True
    assert prepared.frozen_action['loadPointMappingArtifactId'] is None
    assert prepared.frozen_action['loadPointMappingSha256'] is None


# ---------------------------------------------------------------------------
# 平台侧请求门禁
# ---------------------------------------------------------------------------

def _optimization_params(**overrides) -> dict:
    return {
        'solver': 'ANSYS',
        'scenario': 'TRAFFIC',
        'executionTarget': 'OPTIMIZATION_DECISION',
        'runMode': 'REAL_BASELINE_OPTIMIZATION',
        **overrides,
    }


def test_store_accepts_ansys_traffic_optimization_request(tmp_path: Path) -> None:
    store = _store(tmp_path)

    assert store._is_real_baseline_optimization_request(
        'MULTI_OBJECTIVE_OPTIMIZATION',
        _optimization_params(),
    ) is True


def test_store_accepts_openseespy_traffic_optimization_request(tmp_path: Path) -> None:
    """放行口径取自模板登记表，OpenSeesPy 车流模板补齐后与 ANSYS 同口径。"""
    store = _store(tmp_path)

    assert store._is_real_baseline_optimization_request(
        'MULTI_OBJECTIVE_OPTIMIZATION',
        _optimization_params(solver='OPENSEESPY_INPROC'),
    ) is True


def test_store_resolves_registered_traffic_template_by_scenario(tmp_path: Path) -> None:
    store = _store(tmp_path)

    resolved = store._baseline_optimization_workflow_config_path(_optimization_params())

    assert resolved.name == Path(TRAFFIC_WORKFLOW_TEMPLATE).name


def test_store_rejects_cross_load_kind_template_mismatch(tmp_path: Path) -> None:
    """车流配风模板必须失败关闭，反向同理：两者的取数节点不同（1 vs 107）。"""
    store = _store(tmp_path)

    with pytest.raises(HTTPException) as error:
        store._baseline_optimization_workflow_config_path(_optimization_params(
            workflowConfigPath=WIND_OPTIMIZATION_WORKFLOW_PATHS['ANSYS'],
        ))
    assert error.value.detail['code'] == 'INVALID_WORKFLOW_CONFIG_PATH'

    with pytest.raises(HTTPException) as error:
        store._baseline_optimization_workflow_config_path(_optimization_params(
            scenario='WIND',
            workflowConfigPath=TRAFFIC_WORKFLOW_TEMPLATE,
        ))
    assert error.value.detail['code'] == 'INVALID_WORKFLOW_CONFIG_PATH'


# ---------------------------------------------------------------------------
# 配置装配：车流稠密矩阵同时注入基线与优化配置
# ---------------------------------------------------------------------------

def _matrix_value(node: int, row_index: int) -> float:
    """每个节点每个时刻取互不相同的力值。

    车流矩阵的失效模式是「列错位」而不是「数值错」：如果所有节点共用同一个
    数值，列顺序写错了断言也照样通过，所以让 (节点, 时刻) 唯一确定数值。
    """
    return float(node + 1000 * row_index)


def _standard_traffic_csv() -> bytes:
    """构造车流标准制品：1 个时间列 + 每个节点 1 个力列的稠密矩阵。"""
    header = ['time_s', *(f'node_{node}_fy_N' for node in TRAFFIC_NODES)]
    rows = [
        [f'{time_value:g}', *(f'{_matrix_value(node, row_index):g}' for node in TRAFFIC_NODES)]
        for row_index, time_value in enumerate(MATRIX_TIMES)
    ]
    lines = [','.join(header), *(','.join(row) for row in rows)]
    return ('\n'.join(lines) + '\n').encode('utf-8')


def _traffic_mapping_json() -> bytes:
    """构造逐节点 mapping 制品：source_column 与矩阵列号严格 1..N 双射。"""
    entries = [
        {
            'fem_node_id': node,
            'dof': 'FY',
            'scale': 1.0,
            'source_column': column,
            'group': 'traffic',
            'label': f'traffic_{column}',
        }
        for column, node in enumerate(TRAFFIC_NODES, start=1)
    ]
    return json.dumps(entries).encode('utf-8')


def _traffic_load_params(**overrides) -> dict:
    return {
        'solver': 'ANSYS',
        'scenario': 'TRAFFIC',
        'loadKind': 'TRAFFIC',
        'loadDatasetArtifactId': MATRIX_ARTIFACT_ID,
        'loadDatasetSha256': MATRIX_SHA256,
        'loadTargetSetId': ANALYSIS_TRAFFIC_TARGET_SET_ID,
        'loadPointMappingArtifactId': MAPPING_ARTIFACT_ID,
        'loadPointMappingSha256': MAPPING_SHA256,
        **overrides,
    }


def _traffic_load_store(tmp_path: Path, monkeypatch) -> PlatformStore:
    """登记矩阵制品与 mapping 制品，执行侧会按两个不同 ID 各取一次。"""
    store = _store(tmp_path)
    records = {
        MATRIX_ARTIFACT_ID: SimpleNamespace(
            artifact=SimpleNamespace(
                artifact_id=MATRIX_ARTIFACT_ID,
                kind='CSV_TIMESERIES',
                sha256=MATRIX_SHA256,
            ),
            content=_standard_traffic_csv(),
        ),
        MAPPING_ARTIFACT_ID: SimpleNamespace(
            artifact=SimpleNamespace(
                artifact_id=MAPPING_ARTIFACT_ID,
                kind='JSON_MAPPING',
                sha256=MAPPING_SHA256,
            ),
            content=_traffic_mapping_json(),
        ),
    }

    def _get_artifact(artifact_id: str):
        record = records.get(str(artifact_id))
        if record is None:
            raise HTTPException(status_code=404, detail={
                'code': 'NOT_FOUND',
                'message': f'制品 {artifact_id} 不存在',
            })
        return record

    monkeypatch.setattr(store, 'get_artifact', _get_artifact)
    return store


def test_traffic_load_injects_matrix_into_both_baseline_and_optimization(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = _traffic_load_store(tmp_path, monkeypatch)
    # 两侧都预置 traffic_load_component：命名 component 会让 ANSYS 只渲染一列
    # TABLE 并丢掉 163 个逐节点列，必须被移除。源 metadata 为空时这条断言是
    # 空的，所以显式种上。
    baseline_config: dict = {
        'bridge_model': {
            'name': 'STbridge',
            'metadata': {'traffic_load_component': 'TRAFFIC_FY'},
        },
    }
    optimization_config: dict = {
        'bridge_model': {
            'name': 'STbridge',
            'metadata': {'traffic_load_component': 'TRAFFIC_FY'},
        },
        'load_cases': [{'name': 'traffic', 'load_type': 'traffic', 'scale': 1.0}],
        'active_load_cases': ['traffic'],
    }

    evidence = store._apply_agent_standard_traffic_load(
        baseline_config,
        tmp_path,
        _traffic_load_params(),
        optimization_config=optimization_config,
    )

    assert evidence['component'] == 'UY'
    assert evidence['targetSetId'] == ANALYSIS_TRAFFIC_TARGET_SET_ID
    assert evidence['nodeCount'] == len(TRAFFIC_NODES)
    assert evidence['timeStepS'] == 1.0
    # 逐节点独立时程：求解侧不得再做等权分配或空间求和。
    assert evidence['distribution'] == 'PER_NODE_INDEPENDENT_TIME_HISTORY'
    # 两个制品的哈希都要进证据，缺一个就无法复核列映射。
    assert evidence['pointMappingArtifactId'] == MAPPING_ARTIFACT_ID
    assert evidence['pointMappingSha256'] == MAPPING_SHA256
    solver_input = Path(evidence['solverInputPath'])
    # 求解侧输入保持矩阵形态，写成单列会丢掉空间分布。
    assert solver_input.name == 'agent_traffic_nodal_force_matrix_n.csv'

    # 基线用单数 load_case 字段。
    assert baseline_config['load_case']['load_type'] == 'traffic'
    assert baseline_config['load_case']['path'] == str(solver_input)
    # 优化用 load_cases 列表，且只保留 traffic 一个活动工况。
    assert optimization_config['active_load_cases'] == ['traffic']
    assert len(optimization_config['load_cases']) == 1
    traffic_case = optimization_config['load_cases'][0]
    assert traffic_case['load_type'] == 'traffic'
    assert traffic_case['path'] == str(solver_input)
    assert traffic_case['dt'] == 1.0
    # 节点集与逐节点列映射必须写回两侧 bridge_model：优化侧漏写会让 DOE 每个
    # 设计点退回模板默认节点，而基线正确，比较结论直接失真。
    for config in (baseline_config, optimization_config):
        metadata = config['bridge_model']['metadata']
        assert metadata['traffic_load_nodes'] == TRAFFIC_NODES
        assert 'traffic_load_component' not in metadata
        assert [item['source_column'] for item in metadata['traffic_load_mappings']] == list(
            range(1, len(TRAFFIC_NODES) + 1)
        )
        assert [item['fem_node_id'] for item in metadata['traffic_load_mappings']] == TRAFFIC_NODES


@pytest.mark.parametrize(
    ('params', 'code'),
    [
        ({'loadDatasetArtifactId': None}, 'TRAFFIC_LOAD_ARTIFACT_REQUIRED'),
        ({'loadTargetSetId': None}, 'TRAFFIC_LOAD_TARGET_REQUIRED'),
        ({'loadTargetSetId': 'NOT_REGISTERED'}, 'TRAFFIC_LOAD_TARGET_REQUIRED'),
        ({'loadDatasetSha256': FOREIGN_SHA256}, 'LOAD_ARTIFACT_HASH_MISMATCH'),
    ],
)
def test_traffic_load_requires_frozen_artifact_and_target_set(
    tmp_path: Path,
    monkeypatch,
    params: dict,
    code: str,
) -> None:
    """车流没有内置默认记录：缺制品或缺施加对象必须 422，不能回退默认节点。"""
    store = _traffic_load_store(tmp_path, monkeypatch)

    with pytest.raises(HTTPException) as error:
        store._apply_agent_standard_traffic_load(
            {'bridge_model': {}},
            tmp_path,
            _traffic_load_params(**params),
        )
    assert error.value.detail['code'] == code


@pytest.mark.parametrize(
    ('params', 'code'),
    [
        ({'loadPointMappingArtifactId': None}, 'TRAFFIC_LOAD_POINT_MAPPING_REQUIRED'),
        ({'loadPointMappingSha256': None}, 'LOAD_POINT_MAPPING_HASH_MISMATCH'),
        ({'loadPointMappingSha256': FOREIGN_SHA256}, 'LOAD_POINT_MAPPING_HASH_MISMATCH'),
        # 两个制品 ID 写成同一个（现实中最容易发生的配对错误）。
        ({'loadPointMappingArtifactId': MATRIX_ARTIFACT_ID}, 'LOAD_POINT_MAPPING_HASH_MISMATCH'),
    ],
)
def test_traffic_load_requires_frozen_point_mapping_pair(
    tmp_path: Path,
    monkeypatch,
    params: dict,
    code: str,
) -> None:
    """矩阵制品单独存在不可解释：没有 mapping 就不知道哪列对应哪个节点。

    这里是车流优化链真正的 mapping 失败关闭位置——审批门放行了 None
    （见 test_traffic_optimization_approval_does_not_gate_missing_point_mapping）。
    """
    store = _traffic_load_store(tmp_path, monkeypatch)

    with pytest.raises(HTTPException) as error:
        store._apply_agent_standard_traffic_load(
            {'bridge_model': {}},
            tmp_path,
            _traffic_load_params(**params),
        )
    assert error.value.detail['code'] == code


def _optimization_source_config() -> dict:
    return {
        'bridge_model': {'name': 'STbridge', 'metadata': {}},
        'load_cases': [{'name': 'traffic', 'load_type': 'traffic', 'scale': 1.0}],
        'solver_kwargs': {
            'omit_dampers': True,
            'damper_module': 'damper_viscous',
            'postprocessor': {'mode': 'ansys-dpf-rst', 'response_nodes': [1, 72]},
        },
    }


def test_traffic_optimization_config_is_single_objective_with_dampers(tmp_path: Path) -> None:
    store = _store(tmp_path)

    config = store._traffic_optimization_config(
        _optimization_source_config(),
        tmp_path,
        tmp_path / 'out',
        'ANSYS',
        [{'c': 1000.0, 'alpha': 0.5}, {'c': 2000.0, 'alpha': 0.6}],
    )

    assert config['objective_specs'] == [
        {'scenario': 'traffic', 'objective': 'cumulative_displacement', 'weight': 1.0}
    ]
    assert config['active_load_cases'] == ['traffic']
    assert len(config['load_cases']) == 1
    assert config['solver'] == 'ansys'
    # 基线模板的 omit_dampers=True 只属于无控基线，装配时必须被翻回 False。
    assert config['solver_kwargs']['omit_dampers'] is False
    assert config['solver_kwargs']['damper_module'] == 'damper_user300_viscous'
    postprocessor = config['solver_kwargs']['postprocessor']
    assert postprocessor['cumulative_displacement_node'] == TRAFFIC_CUMULATIVE_DISPLACEMENT_NODE
    assert postprocessor['response_component'] == TRAFFIC_RESPONSE_COMPONENT_X
    assert config['n_doe_samples'] == 2
    assert config['run_validation'] is True
    assert config['run_review'] is True
    # 基线指标约束由工作流在基线完成后注入，不进 DOE 配置。
    assert 'baseline_objective_limits' not in config


def test_traffic_optimization_config_assembles_openseespy_solver_block(tmp_path: Path) -> None:
    """OpenSeesPy 车流优化装配：三处与 ANSYS 不能共用的设置必须正确。"""
    store = _store(tmp_path)

    config = store._traffic_optimization_config(
        {
            'bridge_model': {'name': 'STbridge', 'metadata': {'deck_nodes': [1, 72]}},
            'load_cases': [{'name': 'traffic', 'load_type': 'traffic'}],
            'solver': {
                'type': 'openseespy_inproc',
                'damper_module': 'damper_viscous',
                'model_path': 'stbridge_opensees_modal_builder.py',
                'response_nodes': [1, 72],
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
        {'scenario': 'traffic', 'objective': 'cumulative_displacement', 'weight': 1.0},
    ]
    assert config['active_load_cases'] == ['traffic']
    solver = config['solver']
    assert solver['type'] == 'openseespy_inproc'
    # damper_user300_viscous 是 ANSYS 独有别名，OpenSees 侧不得出现。
    assert solver['damper_module'] == 'damper_viscous'
    assert solver['omit_dampers'] is False
    # opensees-csv 后处理没有 cumulative_displacement_node 选择器，displacement 列是
    # response_nodes 的逐步 max-abs 包络；必须收窄为单节点才与 ANSYS 取节点 1 同定义。
    assert solver['response_nodes'] == [TRAFFIC_CUMULATIVE_DISPLACEMENT_NODE]
    # openseespy_inproc 不支持线程并行，config_runner 会直接 raise。
    assert config['parallel']['mode'] == 'process'
    assert 'baseline_objective_limits' not in config


def test_traffic_optimization_config_rejects_unregistered_solver(tmp_path: Path) -> None:
    """放行面取自已登记模板表；未登记的求解器仍须失败关闭。"""
    store = _store(tmp_path)

    with pytest.raises(HTTPException) as error:
        store._traffic_optimization_config(
            _optimization_source_config(),
            tmp_path,
            tmp_path / 'out',
            'OPENSEES',
            [{'c': 1000.0, 'alpha': 0.5}],
        )
    assert error.value.detail['code'] == 'UNSUPPORTED_REAL_WORKFLOW_SOLVER'


def test_traffic_optimization_config_requires_traffic_load_case(tmp_path: Path) -> None:
    """模板缺 traffic 工况时失败关闭，不能装配出一个没有荷载的优化。"""
    store = _store(tmp_path)
    source = _optimization_source_config()
    source.pop('load_cases')

    with pytest.raises(HTTPException) as error:
        store._traffic_optimization_config(
            source,
            tmp_path,
            tmp_path / 'out',
            'ANSYS',
            [{'c': 1000.0, 'alpha': 0.5}],
        )
    assert error.value.detail['code'] == 'INVALID_WORKFLOW_CONFIG_PATH'


# ---------------------------------------------------------------------------
# 证据口径：概览制品按荷载类型命名
# ---------------------------------------------------------------------------

def test_overview_artifact_name_follows_traffic_load_kind() -> None:
    assert (
        OPTIMIZATION_OVERVIEW_ARTIFACT_NAMES['TRAFFIC']
        == 'real_traffic_workflow_overview.json'
    )


def test_required_artifacts_follow_traffic_load_kind() -> None:
    """车流的结论不能写进名为 earthquake/wind 的概览文件里。"""
    traffic = required_real_optimization_artifacts('TRAFFIC')
    earthquake = required_real_optimization_artifacts('EARTHQUAKE')

    assert 'real_traffic_workflow_overview.json' in traffic
    assert 'real_earthquake_workflow_overview.json' not in traffic
    assert 'real_wind_workflow_overview.json' not in traffic
    # 其余必需制品与地震一致。
    assert traffic - {'real_traffic_workflow_overview.json'} == (
        earthquake - {'real_earthquake_workflow_overview.json'}
    )


def test_traffic_overview_reports_traffic_scenario_and_single_target(tmp_path: Path) -> None:
    store = _store(tmp_path)
    prepared = SimpleNamespace(
        workflow_config_path=Path('run/workflow.json'),
        source_workflow_config_path=Path('templates/traffic_workflow.json'),
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
                'objective_names': ['traffic:cumulative_displacement'],
                'best_objectives': [0.42],
                'parameter_names': ['c', 'alpha'],
                'best_design': [3000.0, 0.6],
            },
        },
        baseline_summary={'objectives': {'traffic:cumulative_displacement': 1.2479}},
        prepared_workflow=prepared,
        load_kind='TRAFFIC',
    )

    assert overview['scenario'] == 'TRAFFIC'
    # 概览只列车流工况的单一目标，不能混进地震的三个目标。
    assert [target['targetId'] for target in overview['responseTargets']] == [
        'trafficBeamEndCumulativeDisplacement'
    ]
    # 车流是单目标工况，运营工况不参与，否则概览会多出一条没跑过的目标。
    assert overview['operationIncluded'] is False
    assert overview['recommendedObjectives'] == {'cumulative_displacement': 0.42}
    # 基线值按 traffic: 前缀取，不是 earthquake:。
    assert overview['baselineObjectives'] == {'cumulative_displacement': 1.2479}
    assert overview['recommendedParameters'] == {'c': 3000.0, 'alpha': 0.6}
