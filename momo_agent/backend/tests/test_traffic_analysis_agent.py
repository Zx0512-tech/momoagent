"""交通荷载 × ANSYS 单次分析的审批门、荷载绑定与能力目录测试。"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.agents.analysis import (
    ANALYSIS_TRAFFIC_TARGET_SET_ID,
    ANALYSIS_TRAFFIC_WORKFLOW_PATHS,
    ANALYSIS_WORKFLOW_PATHS,
    AnalysisAgent,
    AnalysisDispatchInput,
)
from app.agents.core import RepositorySessionMemory
from app.services.agent_engineering import build_engineering_contract
from app.services.platform_store import PlatformStore
from app.services.real_execution import real_execution_registry
from pyansys_bridge.core.ansys_load_targets import (
    DEFAULT_STBRIDGE_RANDOM_TRAFFIC_DECK_NODES as DEFAULT_STBRIDGE_TRAFFIC_LOAD_NODES,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
TRAFFIC_TEMPLATE = ANALYSIS_TRAFFIC_WORKFLOW_PATHS['ANSYS']
OPENSEES_TRAFFIC_TEMPLATE = ANALYSIS_TRAFFIC_WORKFLOW_PATHS['OPENSEESPY_INPROC']
# 稠密矩阵制品与逐节点 mapping 制品成对冻结，执行侧按 ID 分别取两个制品。
MATRIX_ARTIFACT_ID = 'load-traffic-1'
MATRIX_SHA256 = 'd' * 64
MAPPING_ARTIFACT_ID = 'load-traffic-mapping-1'
MAPPING_SHA256 = 'f' * 64
FOREIGN_SHA256 = 'e' * 64
TRAFFIC_NODES = list(DEFAULT_STBRIDGE_TRAFFIC_LOAD_NODES)
MATRIX_TIMES = (0.0, 1.0, 2.0)


def _agent(*, workflow_path: str = TRAFFIC_TEMPLATE, store=None, calls: list | None = None) -> AnalysisAgent:
    def preflight_handler(payload):
        if calls is not None:
            calls.append(payload)
        return {
            'passed': True,
            'workflow_path': workflow_path,
            'readiness': {'status': 'READY'},
            'config': {'kind': 'undamped_baseline', 'load_type': 'traffic'},
            'solver_version_profile': {
                'schemaVersion': '1.0',
                'solver': {'name': 'ANSYS_MAPDL', 'version': '2024 R1'},
                'responseContract': {'id': 'ANSYS_RESPONSE_R1'},
            },
        }

    return AnalysisAgent(
        planner=SimpleNamespace(),
        memory=RepositorySessionMemory(lambda _session_id: []),
        store=store,
        preflight_handler=preflight_handler,
    )


def _traffic_channel(**overrides) -> dict:
    # 车流是移动荷载：单条通道描述整个 163 列稠密矩阵，应用类型用
    # NODAL_FORCE_MATRIX 与风的单列总力（NODAL_FORCE）区分。
    return {
        'valueColumn': 'node_fy_N_matrix',
        'applicationType': 'NODAL_FORCE_MATRIX',
        'targetType': 'NODE_GROUP',
        'targetId': ANALYSIS_TRAFFIC_TARGET_SET_ID,
        'component': 'UY',
        'quantity': 'FORCE',
        'sourceUnit': 'N',
        'scale': 1.0,
        'matrixColumnCount': len(DEFAULT_STBRIDGE_TRAFFIC_LOAD_NODES),
        **overrides,
    }


def _traffic_mapping(channels: list[dict] | None = None, **overrides) -> dict:
    return {
        'version': 2,
        'loadKind': 'TRAFFIC',
        'time': {'column': 'time_s', 'unit': 's'},
        'channels': [_traffic_channel()] if channels is None else channels,
        'solver': 'ANSYS',
        # 逐节点 mapping 制品与矩阵制品配对冻结，缺任一个都必须失败关闭。
        'pointMappingArtifactId': 'load-traffic-mapping-1',
        'pointMappingSha256': 'd' * 64,
        **overrides,
    }


def _traffic_run(solver: str = 'ANSYS') -> dict:
    return {
        'runId': 'run-traffic-1',
        'intent': {'loadKind': 'TRAFFIC'},
        'workflowContract': build_engineering_contract(
            task_type='ANALYSIS',
            solver=solver,
            damper_type=None,
            response_ids=['max_girder_end_displacement'],
            load_kind='TRAFFIC',
        ),
    }


def _prepare(agent: AnalysisAgent, run: dict, mapping: dict, *, artifact_id='load-traffic-1', sha='c' * 64):
    return agent.prepare_approval(
        run,
        mapping=mapping,
        standard_artifact_id=artifact_id,
        standard_sha256=sha,
    )


def test_traffic_analysis_freezes_registered_template_target_and_load_artifact() -> None:
    calls: list = []
    agent = _agent(calls=calls)

    prepared = _prepare(agent, _traffic_run(), _traffic_mapping())

    assert prepared.passed
    assert calls[0].load_kind == 'TRAFFIC'
    frozen = prepared.frozen_action
    assert frozen['loadKind'] == 'TRAFFIC'
    assert frozen['workflowConfigPath'] == TRAFFIC_TEMPLATE
    assert frozen['loadTargetSetId'] == ANALYSIS_TRAFFIC_TARGET_SET_ID
    assert frozen['loadDatasetArtifactId'] == 'load-traffic-1'
    assert frozen['loadDatasetSha256'] == 'c' * 64
    assert frozen['loadMapping'] == _traffic_mapping()
    target = next(item for item in frozen['inputProvenance'] if item['field'] == 'loadTargetSetId')
    assert target['value'] == ANALYSIS_TRAFFIC_TARGET_SET_ID


def test_traffic_analysis_without_registered_load_artifact_fails_closed() -> None:
    agent = _agent()

    prepared = _prepare(agent, _traffic_run(), _traffic_mapping(), artifact_id=None, sha=None)

    assert not prepared.passed
    assert prepared.failure_status == 'UNSUPPORTED'
    assert prepared.preflight['reason'] == 'TRAFFIC_LOAD_ARTIFACT_REQUIRED'
    assert prepared.frozen_action is None


def test_traffic_analysis_on_openseespy_is_registered() -> None:
    """OpenSeesPy 车流基线已登记：163 列矩阵由 traffic.pyfrag 逐节点消费。"""
    agent = _agent(workflow_path=OPENSEES_TRAFFIC_TEMPLATE)

    prepared = _prepare(agent, _traffic_run(solver='OPENSEESPY_INPROC'), _traffic_mapping())

    assert prepared.passed
    assert prepared.frozen_action['workflowConfigPath'] == OPENSEES_TRAFFIC_TEMPLATE
    assert prepared.frozen_action['solver'] == 'OPENSEESPY_INPROC'


def test_traffic_analysis_on_unregistered_solver_fails_closed() -> None:
    """求解器未登记车流模板时门禁必须失败关闭。

    直接测门函数：工程合约层的求解器枚举只有 ANSYS 与 OPENSEESPY_INPROC，
    两者都已登记车流模板，所以未登记求解器无法从 prepare_approval 走到这里。
    门本身仍必须守住这条边界——注册表是唯一事实来源，不按求解器名猜测。
    """
    gate = AnalysisAgent._load_kind_gate(
        'TRAFFIC',
        solver='ABAQUS',
        channels=[_traffic_channel()],
        standard_artifact_id='load-traffic-1',
        standard_sha256='c' * 64,
        mapping=_traffic_mapping(),
    )

    assert gate is not None
    assert gate[0] == 'TRAFFIC_SOLVER_UNSUPPORTED'


@pytest.mark.parametrize(
    'mapping_overrides',
    [
        {'pointMappingArtifactId': None},
        {'pointMappingSha256': None},
        {'pointMappingArtifactId': None, 'pointMappingSha256': None},
    ],
)
def test_traffic_analysis_without_point_mapping_artifact_fails_closed(mapping_overrides: dict) -> None:
    """矩阵制品单独存在不可解释：没有 mapping 就不知道哪列对应哪个节点。"""
    agent = _agent()

    prepared = _prepare(agent, _traffic_run(), _traffic_mapping(**mapping_overrides))

    assert not prepared.passed
    assert prepared.failure_status == 'UNSUPPORTED'
    assert prepared.preflight['reason'] == 'TRAFFIC_LOAD_POINT_MAPPING_REQUIRED'
    assert prepared.frozen_action is None


@pytest.mark.parametrize(
    ('channel_overrides', 'reason'),
    [
        ({'applicationType': 'NODAL_FORCE'}, 'TRAFFIC_LOAD_MAPPING_UNSUPPORTED'),
        ({'applicationType': 'UNIFORM_EXCITATION', 'quantity': 'ACCELERATION', 'sourceUnit': 'g'}, 'TRAFFIC_LOAD_MAPPING_UNSUPPORTED'),
        ({'quantity': 'ACCELERATION', 'sourceUnit': 'm/s2'}, 'TRAFFIC_LOAD_MAPPING_UNSUPPORTED'),
        ({'sourceUnit': 'g'}, 'TRAFFIC_LOAD_MAPPING_UNSUPPORTED'),
        ({'targetType': 'NODE'}, 'TRAFFIC_LOAD_TARGET_UNSUPPORTED'),
        ({'targetId': 'STBRIDGE_WIND_DECK_NODES'}, 'TRAFFIC_LOAD_TARGET_UNSUPPORTED'),
        ({'targetId': None}, 'TRAFFIC_LOAD_TARGET_UNSUPPORTED'),
    ],
)
def test_traffic_analysis_rejects_mismatched_channel_contract(channel_overrides: dict, reason: str) -> None:
    agent = _agent()

    prepared = _prepare(agent, _traffic_run(), _traffic_mapping([_traffic_channel(**channel_overrides)]))

    assert not prepared.passed
    assert prepared.preflight['reason'] == reason


def test_traffic_analysis_requires_exactly_one_channel() -> None:
    agent = _agent()
    channels = [_traffic_channel(), _traffic_channel(valueColumn='traffic_fx', component='UX')]

    prepared = _prepare(agent, _traffic_run(), _traffic_mapping(channels))

    assert not prepared.passed
    assert prepared.preflight['reason'] == 'TRAFFIC_LOAD_MAPPING_UNSUPPORTED'


def test_traffic_analysis_rejects_missing_channel_mapping() -> None:
    agent = _agent()

    prepared = _prepare(agent, _traffic_run(), {'loadKind': 'TRAFFIC', 'channels': []})

    assert not prepared.passed
    assert prepared.preflight['reason'] == 'TRAFFIC_LOAD_MAPPING_UNSUPPORTED'


@pytest.mark.parametrize('load_kind', ['GENERIC_NODAL'])
def test_other_load_kinds_remain_closed(load_kind: str) -> None:
    agent = _agent()
    run = _traffic_run()
    run['intent'] = {'loadKind': load_kind}

    prepared = _prepare(agent, run, {'loadKind': load_kind, 'channels': []})

    assert not prepared.passed
    assert prepared.preflight['reason'] == 'PRODUCTION_GATE'


def test_earthquake_analysis_keeps_registered_earthquake_template() -> None:
    agent = _agent(workflow_path=ANALYSIS_WORKFLOW_PATHS['ANSYS'])
    run = _traffic_run()
    run['intent'] = {'loadKind': 'EARTHQUAKE'}

    prepared = _prepare(agent, run, {'loadKind': 'EARTHQUAKE', 'channels': []})

    assert prepared.passed
    assert prepared.frozen_action['workflowConfigPath'] == ANALYSIS_WORKFLOW_PATHS['ANSYS']
    assert 'loadTargetSetId' not in prepared.frozen_action


def _traffic_frozen_action(**overrides) -> dict:
    return {
        'solver': 'ANSYS',
        'caseSetId': 'run-traffic-1',
        'runMode': 'REAL_AGENT_ANALYSIS',
        'workflowConfigPath': TRAFFIC_TEMPLATE,
        'loadKind': 'TRAFFIC',
        'loadTargetSetId': ANALYSIS_TRAFFIC_TARGET_SET_ID,
        'loadDatasetArtifactId': 'load-traffic-1',
        'loadDatasetSha256': 'c' * 64,
        'loadMapping': _traffic_mapping(),
        'responseIds': [],
        'resources': {'processCount': 1, 'coresPerProcess': 1, 'executionTimeoutS': 7200},
        **overrides,
    }


def test_dispatch_input_accepts_registered_traffic_template() -> None:
    request = AnalysisDispatchInput(run_id='run-traffic-1', frozen_action=_traffic_frozen_action())

    assert request.frozen_action['workflowConfigPath'] == TRAFFIC_TEMPLATE


@pytest.mark.parametrize(
    'overrides',
    [
        {'solver': 'OPENSEESPY_INPROC'},
        {'workflowConfigPath': ANALYSIS_WORKFLOW_PATHS['ANSYS']},
        {'loadKind': 'GENERIC_NODAL'},
        {'loadTargetSetId': 'STBRIDGE_WIND_DECK_NODES'},
        {'loadTargetSetId': None},
    ],
)
def test_dispatch_input_rejects_unregistered_traffic_combinations(overrides: dict) -> None:
    with pytest.raises(ValidationError):
        AnalysisDispatchInput(run_id='run-traffic-1', frozen_action=_traffic_frozen_action(**overrides))


def test_dispatch_input_rejects_traffic_target_on_earthquake_action() -> None:
    action = _traffic_frozen_action(
        loadKind='EARTHQUAKE',
        workflowConfigPath=ANALYSIS_WORKFLOW_PATHS['ANSYS'],
    )
    with pytest.raises(ValidationError):
        AnalysisDispatchInput(run_id='run-traffic-1', frozen_action=action)


def test_traffic_template_declares_ansys_run_mode_without_bundled_load_path() -> None:
    config = json.loads((REPO_ROOT / TRAFFIC_TEMPLATE).read_text(encoding='utf-8'))

    assert config['solver'] == 'ansys'
    assert config['solver_kwargs']['execution_mode'] == 'run'
    assert config['load_case']['load_type'] == 'traffic'
    assert config['load_case']['direction'] == {'x': 0.0, 'y': -1.0, 'z': 0.0}
    assert 'path' not in config['load_case']
    assert 'dt' not in config['load_case']
    assert 'duration' not in config['load_case']
    metadata = config['bridge_model']['metadata']
    assert metadata['traffic_load_nodes'] == list(DEFAULT_STBRIDGE_TRAFFIC_LOAD_NODES)
    source = (REPO_ROOT / TRAFFIC_TEMPLATE).parent / config['bridge_model']['source_path']
    assert source.resolve().exists()


def test_traffic_template_preflight_reports_undamped_traffic_baseline() -> None:
    from pyansys_bridge.optimization.config_runner import preflight_config

    result = preflight_config(REPO_ROOT / TRAFFIC_TEMPLATE)

    assert result['kind'] == 'undamped_baseline'
    assert result['solver'] == 'ansys'
    assert result['load_type'] == 'traffic'
    assert result['execution_mode'] == 'run'
    assert result['path_checks']['bridge_model.source_path']['exists'] is True


def _bare_store() -> PlatformStore:
    return PlatformStore.__new__(PlatformStore)


def _matrix_value(node: int, row_index: int) -> float:
    """每个节点每个时刻取互不相同的力值。

    车流矩阵的失效模式是「列错位」而不是「数值错」：如果所有节点共用同一个
    数值，列顺序写错了断言也照样通过，所以这里让 (节点, 时刻) 唯一确定数值。
    """
    return float(node + 1000 * row_index)


def _matrix_rows(nodes: list[int], times: tuple[float, ...]) -> list[list[str]]:
    """生成稠密矩阵的数据行，返回可变结构方便负例就地破坏某一格。"""
    return [
        [f'{time_value:g}', *(f'{_matrix_value(node, row_index):g}' for node in nodes)]
        for row_index, time_value in enumerate(times)
    ]


def _standard_traffic_csv(
    *,
    nodes: list[int] | None = None,
    times: tuple[float, ...] = MATRIX_TIMES,
    time_header: str = 'time_s',
    node_headers: list[str] | None = None,
    rows: list[list[str]] | None = None,
) -> bytes:
    """构造车流标准制品：1 个时间列 + 每个节点 1 个力列的稠密矩阵。

    node_headers / rows 允许负例构造畸形矩阵（表头形状错、列数不齐、非数值）。
    """
    nodes = TRAFFIC_NODES if nodes is None else nodes
    header = [time_header, *(node_headers if node_headers is not None else [f'node_{node}_fy_N' for node in nodes])]
    data_rows = _matrix_rows(nodes, times) if rows is None else rows
    lines = [','.join(header), *(','.join(row) for row in data_rows)]
    return ('\n'.join(lines) + '\n').encode('utf-8')


def _traffic_mapping_json(
    *,
    nodes: list[int] | None = None,
    columns: list[int] | None = None,
    entries: list[dict] | None = None,
    raw: bytes | None = None,
) -> bytes:
    """构造逐节点 mapping 制品：source_column 与矩阵列号严格 1..N 双射。"""
    if raw is not None:
        return raw
    nodes = TRAFFIC_NODES if nodes is None else nodes
    columns = list(range(1, len(nodes) + 1)) if columns is None else columns
    if entries is None:
        entries = [
            {
                'fem_node_id': node,
                'dof': 'FY',
                'scale': 1.0,
                'source_column': column,
                'group': 'traffic',
                'label': f'traffic_{column}',
            }
            for node, column in zip(nodes, columns)
        ]
    return json.dumps(entries).encode('utf-8')


def _store_with_traffic_artifact(
    content: bytes,
    mapping_content: bytes | None = None,
    *,
    sha: str = MATRIX_SHA256,
    mapping_sha: str = MAPPING_SHA256,
    register_mapping: bool = True,
) -> PlatformStore:
    """登记矩阵制品与 mapping 制品，执行侧会按两个不同 ID 各取一次。

    未登记的 ID 按真实 store 的行为抛 404，让「mapping 制品不存在」也能被覆盖。
    """
    store = _bare_store()
    records = {
        MATRIX_ARTIFACT_ID: SimpleNamespace(
            artifact=SimpleNamespace(artifact_id=MATRIX_ARTIFACT_ID, kind='CSV_TIMESERIES', sha256=sha),
            content=content,
        ),
    }
    if register_mapping:
        records[MAPPING_ARTIFACT_ID] = SimpleNamespace(
            artifact=SimpleNamespace(artifact_id=MAPPING_ARTIFACT_ID, kind='JSON_MAPPING', sha256=mapping_sha),
            content=_traffic_mapping_json() if mapping_content is None else mapping_content,
        )

    def _get_artifact(artifact_id: str):
        record = records.get(str(artifact_id))
        if record is None:
            raise HTTPException(status_code=404, detail={
                'code': 'NOT_FOUND',
                'message': f'制品 {artifact_id} 不存在',
            })
        return record

    store.get_artifact = _get_artifact  # type: ignore[method-assign]
    return store


def _traffic_params(**overrides) -> dict:
    return {
        'loadKind': 'TRAFFIC',
        'loadDatasetArtifactId': MATRIX_ARTIFACT_ID,
        'loadDatasetSha256': MATRIX_SHA256,
        'loadTargetSetId': ANALYSIS_TRAFFIC_TARGET_SET_ID,
        'loadPointMappingArtifactId': MAPPING_ARTIFACT_ID,
        'loadPointMappingSha256': MAPPING_SHA256,
        **overrides,
    }


def _default_matrix_rows() -> list[list[str]]:
    return _matrix_rows(TRAFFIC_NODES, MATRIX_TIMES)


def _traffic_csv_with_cell(row_index: int, column_index: int, value: str) -> bytes:
    rows = _default_matrix_rows()
    rows[row_index][column_index] = value
    return _standard_traffic_csv(rows=rows)


def _traffic_csv_with_short_row(row_index: int) -> bytes:
    rows = _default_matrix_rows()
    rows[row_index] = rows[row_index][:-1]
    return _standard_traffic_csv(rows=rows)


def test_traffic_load_artifact_is_bound_to_solver_config_and_deck_nodes(tmp_path: Path) -> None:
    store = _store_with_traffic_artifact(_standard_traffic_csv())
    config: dict = {'bridge_model': {'name': 'STbridge', 'metadata': {'traffic_load_component': 'TRAFFIC_FY'}}}

    evidence = store._apply_agent_standard_traffic_load(config, tmp_path, _traffic_params())

    load_case = config['load_case']
    assert load_case['load_type'] == 'traffic'
    assert load_case['dt'] == 1.0
    assert load_case['duration'] == 2.0
    assert load_case['scale'] == 1.0
    assert load_case['metadata']['agent_standard_load'] == evidence

    # 求解侧输入必须保持矩阵形态：表头逐节点，且第 K 列仍是第 K 个节点自己的力。
    solver_lines = Path(load_case['path']).read_text(encoding='utf-8').splitlines()
    assert solver_lines[0] == ','.join(['time_s', *(f'node_{node}_fy_N' for node in TRAFFIC_NODES)])
    assert len(solver_lines) == 1 + len(MATRIX_TIMES)
    for row_index, line in enumerate(solver_lines[1:]):
        cells = line.split(',')
        assert float(cells[0]) == MATRIX_TIMES[row_index]
        assert [float(cell) for cell in cells[1:]] == [
            _matrix_value(node, row_index) for node in TRAFFIC_NODES
        ]

    metadata = config['bridge_model']['metadata']
    assert metadata['traffic_load_nodes'] == TRAFFIC_NODES
    # 命名 component 会让 ANSYS 渲染只生成一列 TABLE 并丢弃逐节点 mapping。
    assert 'traffic_load_component' not in metadata
    assert metadata['traffic_load_mappings'] == [
        {
            'fem_node_id': node,
            'dof': 'FY',
            # 每列已经是该节点的实际力，不需要再分摊，所以 scale 恒为 1.0。
            'scale': 1.0,
            'source_column': column,
            'group': 'traffic',
            'label': f'traffic_{column}',
        }
        for column, node in enumerate(TRAFFIC_NODES, start=1)
    ]

    assert evidence['artifactId'] == MATRIX_ARTIFACT_ID
    assert evidence['sha256'] == MATRIX_SHA256
    assert evidence['pointMappingArtifactId'] == MAPPING_ARTIFACT_ID
    assert evidence['pointMappingSha256'] == MAPPING_SHA256
    assert evidence['targetSetId'] == ANALYSIS_TRAFFIC_TARGET_SET_ID
    assert evidence['targetNodes'] == TRAFFIC_NODES
    assert evidence['nodeCount'] == len(TRAFFIC_NODES)
    assert evidence['component'] == 'UY'
    assert evidence['unit'] == 'N'
    assert evidence['sampleCount'] == len(MATRIX_TIMES)
    assert evidence['timeStepS'] == 1.0
    assert evidence['durationS'] == 2.0
    # 逐节点独立时程：求解侧不得再做等权分配或空间求和。
    assert evidence['distribution'] == 'PER_NODE_INDEPENDENT_TIME_HISTORY'
    assert evidence['peakTotalForceN'] == max(
        abs(sum(_matrix_value(node, row_index) for node in TRAFFIC_NODES))
        for row_index in range(len(MATRIX_TIMES))
    )
    assert len(evidence['solverInputSha256']) == 64


# 一个与冻结目标集不相交的节点号，用于构造「mapping 自身自洽但节点集越界」。
FOREIGN_NODE = 9999
SWAPPED_NODES = [TRAFFIC_NODES[1], TRAFFIC_NODES[0], *TRAFFIC_NODES[2:]]


@pytest.mark.parametrize(
    ('params', 'content', 'mapping_content', 'code'),
    [
        # 冻结参数缺失/不一致
        (
            {'loadKind': 'TRAFFIC', 'loadTargetSetId': ANALYSIS_TRAFFIC_TARGET_SET_ID},
            _standard_traffic_csv(),
            None,
            'TRAFFIC_LOAD_ARTIFACT_REQUIRED',
        ),
        (
            _traffic_params(loadTargetSetId=None),
            _standard_traffic_csv(),
            None,
            'TRAFFIC_LOAD_TARGET_REQUIRED',
        ),
        (
            _traffic_params(loadTargetSetId='STBRIDGE_UNKNOWN_NODES'),
            _standard_traffic_csv(),
            None,
            'TRAFFIC_LOAD_TARGET_REQUIRED',
        ),
        (
            _traffic_params(loadDatasetSha256=FOREIGN_SHA256),
            _standard_traffic_csv(),
            None,
            'LOAD_ARTIFACT_HASH_MISMATCH',
        ),
        (
            _traffic_params(loadDatasetSha256=None),
            _standard_traffic_csv(),
            None,
            'LOAD_ARTIFACT_HASH_MISMATCH',
        ),
        # mapping 制品的存在性与哈希：矩阵单独存在不可解释，必须成对冻结
        (
            _traffic_params(loadPointMappingArtifactId=None),
            _standard_traffic_csv(),
            None,
            'TRAFFIC_LOAD_POINT_MAPPING_REQUIRED',
        ),
        (
            _traffic_params(loadPointMappingSha256=FOREIGN_SHA256),
            _standard_traffic_csv(),
            None,
            'LOAD_POINT_MAPPING_HASH_MISMATCH',
        ),
        (
            _traffic_params(loadPointMappingSha256=None),
            _standard_traffic_csv(),
            None,
            'LOAD_POINT_MAPPING_HASH_MISMATCH',
        ),
        (
            # 两个制品 ID 写成同一个（现实中最容易发生的配对错误）。
            _traffic_params(loadPointMappingArtifactId=MATRIX_ARTIFACT_ID),
            _standard_traffic_csv(),
            None,
            'LOAD_POINT_MAPPING_HASH_MISMATCH',
        ),
        # mapping 制品本身的结构
        (
            _traffic_params(),
            _standard_traffic_csv(),
            _traffic_mapping_json(raw=b'not-json'),
            'INVALID_TRAFFIC_POINT_MAPPING',
        ),
        (
            _traffic_params(),
            _standard_traffic_csv(),
            _traffic_mapping_json(entries=[]),
            'INVALID_TRAFFIC_POINT_MAPPING',
        ),
        (
            _traffic_params(),
            _standard_traffic_csv(),
            _traffic_mapping_json(raw=json.dumps({'fem_node_id': 1}).encode('utf-8')),
            'INVALID_TRAFFIC_POINT_MAPPING',
        ),
        (
            _traffic_params(),
            _standard_traffic_csv(),
            _traffic_mapping_json(
                entries=[{'dof': 'FY', 'source_column': column} for column in range(1, len(TRAFFIC_NODES) + 1)],
            ),
            'INVALID_TRAFFIC_POINT_MAPPING',
        ),
        (
            _traffic_params(),
            _standard_traffic_csv(),
            _traffic_mapping_json(
                entries=[{'fem_node_id': node, 'dof': 'FY'} for node in TRAFFIC_NODES],
            ),
            'INVALID_TRAFFIC_POINT_MAPPING',
        ),
        (
            _traffic_params(),
            _standard_traffic_csv(),
            _traffic_mapping_json(columns=['x', *range(2, len(TRAFFIC_NODES) + 1)]),
            'INVALID_TRAFFIC_POINT_MAPPING',
        ),
        (
            _traffic_params(),
            _standard_traffic_csv(),
            _traffic_mapping_json(nodes=['abc', *TRAFFIC_NODES[1:]]),
            'INVALID_TRAFFIC_POINT_MAPPING',
        ),
        # 矩阵表头形状
        (_traffic_params(), _standard_traffic_csv(rows=[]), None, 'EMPTY_STANDARD_LOAD'),
        (
            _traffic_params(),
            _standard_traffic_csv(time_header='t'),
            None,
            'UNSUPPORTED_STANDARD_TRAFFIC_LOAD',
        ),
        (
            # 只剩时间列：矩阵退化成没有任何节点力，等价于旧格式的"不是车流荷载"。
            _traffic_params(),
            _standard_traffic_csv(nodes=[]),
            _traffic_mapping_json(nodes=[TRAFFIC_NODES[0]]),
            'UNSUPPORTED_STANDARD_TRAFFIC_LOAD',
        ),
        # mapping 与矩阵列的双射
        (
            # 旧格式"多通道"在矩阵格式下的等价物：mapping 条目数与列数不一致。
            _traffic_params(),
            _standard_traffic_csv(nodes=TRAFFIC_NODES[:-1]),
            None,
            'TRAFFIC_LOAD_POINT_MAPPING_MISMATCH',
        ),
        (
            _traffic_params(),
            _standard_traffic_csv(),
            _traffic_mapping_json(columns=[2, 1, *range(3, len(TRAFFIC_NODES) + 1)]),
            'TRAFFIC_LOAD_POINT_MAPPING_MISMATCH',
        ),
        (
            _traffic_params(),
            _standard_traffic_csv(),
            _traffic_mapping_json(columns=list(range(0, len(TRAFFIC_NODES)))),
            'TRAFFIC_LOAD_POINT_MAPPING_MISMATCH',
        ),
        (
            # 列错位：mapping 节点顺序与矩阵列名不一致，量纲上完全看不出来。
            _traffic_params(),
            _standard_traffic_csv(),
            _traffic_mapping_json(nodes=SWAPPED_NODES),
            'TRAFFIC_LOAD_POINT_MAPPING_MISMATCH',
        ),
        (
            _traffic_params(),
            _standard_traffic_csv(
                node_headers=['node_1_fz_N', *(f'node_{node}_fy_N' for node in TRAFFIC_NODES[1:])],
            ),
            None,
            'TRAFFIC_LOAD_POINT_MAPPING_MISMATCH',
        ),
        (
            # 单位藏在列名里：kN 列名必须被拒，否则整体差 1000 倍。
            _traffic_params(),
            _standard_traffic_csv(
                node_headers=['node_1_fy_kN', *(f'node_{node}_fy_N' for node in TRAFFIC_NODES[1:])],
            ),
            None,
            'TRAFFIC_LOAD_POINT_MAPPING_MISMATCH',
        ),
        # mapping 节点集与冻结目标集
        (
            _traffic_params(),
            _standard_traffic_csv(nodes=[*TRAFFIC_NODES[:-1], FOREIGN_NODE]),
            _traffic_mapping_json(nodes=[*TRAFFIC_NODES[:-1], FOREIGN_NODE]),
            'TRAFFIC_LOAD_TARGET_MISMATCH',
        ),
        (
            # 目标集的真子集：少施加节点同样是错的荷载，不能放行。
            _traffic_params(),
            _standard_traffic_csv(nodes=TRAFFIC_NODES[:10]),
            _traffic_mapping_json(nodes=TRAFFIC_NODES[:10]),
            'TRAFFIC_LOAD_TARGET_MISMATCH',
        ),
        (
            _traffic_params(loadTargetSetId='STBRIDGE_WIND_DECK_NODES'),
            _standard_traffic_csv(),
            None,
            'TRAFFIC_LOAD_TARGET_MISMATCH',
        ),
        # 时间列
        (
            _traffic_params(),
            _traffic_csv_with_cell(0, 0, 'abc'),
            None,
            'INVALID_STANDARD_LOAD',
        ),
        (
            _traffic_params(),
            _standard_traffic_csv(times=(0.0,)),
            None,
            'INVALID_STANDARD_LOAD_TIME',
        ),
        (
            _traffic_params(),
            _standard_traffic_csv(times=(0.0, 1.0, 1.0)),
            None,
            'INVALID_STANDARD_LOAD_TIME',
        ),
        (
            _traffic_params(),
            _standard_traffic_csv(times=(0.0, 2.0, 1.0)),
            None,
            'INVALID_STANDARD_LOAD_TIME',
        ),
        (
            _traffic_params(),
            _standard_traffic_csv(times=(0.0, 1.0, 3.0)),
            None,
            'NON_UNIFORM_STANDARD_LOAD_TIME',
        ),
        # 数据行形状与数值
        (
            _traffic_params(),
            _traffic_csv_with_short_row(1),
            None,
            'INVALID_STANDARD_LOAD',
        ),
        (
            _traffic_params(),
            _traffic_csv_with_cell(1, 1, 'nan-force'),
            None,
            'INVALID_STANDARD_LOAD',
        ),
        (
            _traffic_params(),
            _traffic_csv_with_cell(2, len(TRAFFIC_NODES), ''),
            None,
            'INVALID_STANDARD_LOAD',
        ),
    ],
)
def test_traffic_load_binding_fails_closed(
    params: dict,
    content: bytes,
    mapping_content: bytes | None,
    code: str,
    tmp_path: Path,
) -> None:
    store = _store_with_traffic_artifact(content, mapping_content)

    with pytest.raises(HTTPException) as error:
        store._apply_agent_standard_traffic_load({'bridge_model': {}}, tmp_path, params)

    assert error.value.detail['code'] == code
    # 失败关闭必须不留半成品求解输入，否则后续步骤会当成有效荷载读进去。
    assert not list(tmp_path.iterdir())


def test_traffic_load_binding_fails_closed_when_mapping_artifact_is_not_registered(tmp_path: Path) -> None:
    """冻结了 mapping ID 但制品不在库里：按未登记制品失败关闭，不能退回单列。"""
    store = _store_with_traffic_artifact(_standard_traffic_csv(), register_mapping=False)

    with pytest.raises(HTTPException) as error:
        store._apply_agent_standard_traffic_load({'bridge_model': {}}, tmp_path, _traffic_params())

    assert error.value.status_code == 404
    assert error.value.detail['code'] == 'NOT_FOUND'
    assert not list(tmp_path.iterdir())


def test_capability_catalog_advertises_traffic_on_both_solvers() -> None:
    analysis = real_execution_registry.resolve('ANALYSIS', {'source': 'AGENT'})
    solver_batch = real_execution_registry.resolve('SOLVER_BATCH', {'runMode': 'REAL_AGENT_ANALYSIS'})

    for capability in (analysis, solver_batch):
        assert capability.status == 'LIVE'
        assert 'TRAFFIC' in capability.scenarios
        assert capability.supports(solver='ANSYS', scenario='TRAFFIC')
        assert capability.supports(solver='OPENSEESPY_INPROC', scenario='TRAFFIC')
        assert capability.supports(solver='ANSYS', scenario='EARTHQUAKE')
        assert capability.supports(solver='OPENSEESPY_INPROC', scenario='EARTHQUAKE')


def test_optimization_advertises_traffic_on_both_solvers() -> None:
    # 车流优化两个求解器都登记了 baseline-first 模板
    # （TRAFFIC_OPTIMIZATION_WORKFLOW_CONFIGS），与 ANALYSIS、DAMPER_COMPARISON 一致。
    # 目录仍按求解器逐一声明：广告面与真实门
    # platform_store._is_real_baseline_optimization_request 同口径，
    # 两个黄金用例（..._ANSYS_TRAFFIC 与 ..._OPENSEESPY_TRAFFIC）兜底
    # （见 test_real_agent_baseline_manifest_covers_registry_live_combinations 的双向绑定）。
    capability = real_execution_registry.resolve('DAMPER_OPTIMIZATION', {'source': 'AGENT'})
    assert 'TRAFFIC' in capability.scenarios
    assert capability.supports(solver='ANSYS', scenario='TRAFFIC')
    assert capability.supports(solver='OPENSEESPY_INPROC', scenario='TRAFFIC')


def test_full_optimization_stays_closed_for_traffic() -> None:
    # 完整优化的真实门 is_supported_full_optimization_intent 只认 ANSYS + EARTHQUAKE。
    capability = real_execution_registry.resolve('FULL_OPTIMIZATION', {'source': 'AGENT'})
    assert 'TRAFFIC' not in capability.scenarios
    assert not capability.supports(solver='ANSYS', scenario='TRAFFIC')
