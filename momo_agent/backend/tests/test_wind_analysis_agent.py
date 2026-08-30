"""风荷载 × ANSYS 单次分析的审批门、荷载绑定与能力目录测试。"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.agents.analysis import (
    ANALYSIS_WIND_TARGET_SET_ID,
    ANALYSIS_WIND_WORKFLOW_PATHS,
    ANALYSIS_WORKFLOW_PATHS,
    AnalysisAgent,
    AnalysisDispatchInput,
)
from app.agents.core import RepositorySessionMemory
from app.services.agent_engineering import build_engineering_contract
from app.services.platform_store import PlatformStore
from app.services.real_execution import real_execution_registry
from pyansys_bridge.core.ansys_load_targets import DEFAULT_STBRIDGE_WIND_GIRDER_NODES


REPO_ROOT = Path(__file__).resolve().parents[3]
WIND_TEMPLATE = ANALYSIS_WIND_WORKFLOW_PATHS['ANSYS']
STANDARD_HEADER = (
    'time_s,load_kind,channel_id,application_type,target_type,target_id,'
    'component,quantity,value,unit'
)


# ---------------------------------------------------------------------------
# 测试夹具
# ---------------------------------------------------------------------------

def _agent(*, workflow_path: str = WIND_TEMPLATE, store=None, calls: list | None = None) -> AnalysisAgent:
    def preflight_handler(payload):
        if calls is not None:
            calls.append(payload)
        return {
            'passed': True,
            'workflow_path': workflow_path,
            'readiness': {'status': 'READY'},
            'config': {'kind': 'undamped_baseline', 'load_type': 'wind'},
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


def _wind_channel(**overrides) -> dict:
    return {
        'valueColumn': 'wind_fy',
        'applicationType': 'NODAL_FORCE',
        'targetType': 'NODE_GROUP',
        'targetId': ANALYSIS_WIND_TARGET_SET_ID,
        'component': 'UY',
        'quantity': 'FORCE',
        'sourceUnit': 'N',
        'scale': 1.0,
        **overrides,
    }


def _wind_mapping(channels: list[dict] | None = None) -> dict:
    return {
        'version': 2,
        'loadKind': 'WIND',
        'time': {'column': 'time', 'unit': 's'},
        'channels': [_wind_channel()] if channels is None else channels,
        'solver': 'ANSYS',
    }


def _wind_run(solver: str = 'ANSYS') -> dict:
    return {
        'runId': 'run-wind-1',
        'intent': {'loadKind': 'WIND'},
        'workflowContract': build_engineering_contract(
            task_type='ANALYSIS',
            solver=solver,
            damper_type=None,
            response_ids=['max_girder_end_displacement'],
            load_kind='WIND',
        ),
    }


def _prepare(agent: AnalysisAgent, run: dict, mapping: dict, *, artifact_id='load-wind-1', sha='c' * 64):
    return agent.prepare_approval(
        run,
        mapping=mapping,
        standard_artifact_id=artifact_id,
        standard_sha256=sha,
    )


# ---------------------------------------------------------------------------
# 审批门：风荷载放行条件
# ---------------------------------------------------------------------------

def test_wind_analysis_freezes_registered_template_target_and_load_artifact() -> None:
    calls: list = []
    agent = _agent(calls=calls)

    prepared = _prepare(agent, _wind_run(), _wind_mapping())

    assert prepared.passed
    assert calls[0].load_kind == 'WIND'
    frozen = prepared.frozen_action
    assert frozen['loadKind'] == 'WIND'
    assert frozen['workflowConfigPath'] == WIND_TEMPLATE
    assert frozen['loadTargetSetId'] == ANALYSIS_WIND_TARGET_SET_ID
    assert frozen['loadDatasetArtifactId'] == 'load-wind-1'
    assert frozen['loadDatasetSha256'] == 'c' * 64
    assert frozen['loadMapping'] == _wind_mapping()
    target = next(item for item in frozen['inputProvenance'] if item['field'] == 'loadTargetSetId')
    assert target['value'] == ANALYSIS_WIND_TARGET_SET_ID


def test_wind_analysis_without_registered_load_artifact_fails_closed() -> None:
    agent = _agent()

    prepared = _prepare(agent, _wind_run(), _wind_mapping(), artifact_id=None, sha=None)

    assert not prepared.passed
    assert prepared.failure_status == 'UNSUPPORTED'
    assert prepared.preflight['reason'] == 'WIND_LOAD_ARTIFACT_REQUIRED'
    assert prepared.frozen_action is None


def test_wind_analysis_on_openseespy_freezes_openseespy_template() -> None:
    agent = _agent(workflow_path=ANALYSIS_WIND_WORKFLOW_PATHS['OPENSEESPY_INPROC'])

    prepared = _prepare(agent, _wind_run(solver='OPENSEESPY_INPROC'), _wind_mapping())

    assert prepared.passed
    frozen = prepared.frozen_action
    assert frozen['solver'] == 'OPENSEESPY_INPROC'
    assert frozen['loadKind'] == 'WIND'
    assert frozen['workflowConfigPath'] == ANALYSIS_WIND_WORKFLOW_PATHS['OPENSEESPY_INPROC']
    assert frozen['loadTargetSetId'] == ANALYSIS_WIND_TARGET_SET_ID
    # 冻结动作必须能通过 dispatch 校验，否则审批通过但 Job 创建会被拒。
    AnalysisDispatchInput(run_id='run-wind-1', frozen_action=frozen)


@pytest.mark.parametrize(
    ('channel_overrides', 'reason'),
    [
        ({'applicationType': 'UNIFORM_EXCITATION', 'quantity': 'ACCELERATION', 'sourceUnit': 'g'}, 'WIND_LOAD_MAPPING_UNSUPPORTED'),
        ({'quantity': 'ACCELERATION', 'sourceUnit': 'm/s2'}, 'WIND_LOAD_MAPPING_UNSUPPORTED'),
        ({'sourceUnit': 'g'}, 'WIND_LOAD_MAPPING_UNSUPPORTED'),
        ({'component': 'UX'}, 'WIND_LOAD_MAPPING_UNSUPPORTED'),
        ({'component': 'UZ'}, 'WIND_LOAD_MAPPING_UNSUPPORTED'),
        ({'targetType': 'NODE'}, 'WIND_LOAD_TARGET_UNSUPPORTED'),
        ({'targetId': 'STBRIDGE_TRAFFIC_CENTERLINE'}, 'WIND_LOAD_TARGET_UNSUPPORTED'),
        ({'targetId': None}, 'WIND_LOAD_TARGET_UNSUPPORTED'),
    ],
)
def test_wind_analysis_rejects_mismatched_channel_contract(channel_overrides: dict, reason: str) -> None:
    agent = _agent()

    prepared = _prepare(agent, _wind_run(), _wind_mapping([_wind_channel(**channel_overrides)]))

    assert not prepared.passed
    assert prepared.preflight['reason'] == reason


def test_wind_analysis_requires_exactly_one_channel() -> None:
    agent = _agent()
    channels = [_wind_channel(), _wind_channel(valueColumn='wind_fx', component='UX')]

    prepared = _prepare(agent, _wind_run(), _wind_mapping(channels))

    assert not prepared.passed
    assert prepared.preflight['reason'] == 'WIND_LOAD_MAPPING_UNSUPPORTED'


def test_wind_analysis_rejects_missing_channel_mapping() -> None:
    agent = _agent()

    prepared = _prepare(agent, _wind_run(), {'loadKind': 'WIND', 'channels': []})

    assert not prepared.passed
    assert prepared.preflight['reason'] == 'WIND_LOAD_MAPPING_UNSUPPORTED'


# TRAFFIC 已在 ANSYS 上放行，见 test_traffic_analysis_agent.py。
@pytest.mark.parametrize('load_kind', ['GENERIC_NODAL'])
def test_other_load_kinds_remain_closed(load_kind: str) -> None:
    agent = _agent()
    run = _wind_run()
    run['intent'] = {'loadKind': load_kind}

    prepared = _prepare(agent, run, {'loadKind': load_kind, 'channels': []})

    assert not prepared.passed
    assert prepared.preflight['reason'] == 'PRODUCTION_GATE'


def test_earthquake_analysis_keeps_registered_earthquake_template() -> None:
    agent = _agent(workflow_path=ANALYSIS_WORKFLOW_PATHS['ANSYS'])
    run = _wind_run()
    run['intent'] = {'loadKind': 'EARTHQUAKE'}

    prepared = _prepare(agent, run, {'loadKind': 'EARTHQUAKE', 'channels': []})

    assert prepared.passed
    assert prepared.frozen_action['workflowConfigPath'] == ANALYSIS_WORKFLOW_PATHS['ANSYS']
    assert 'loadTargetSetId' not in prepared.frozen_action


# ---------------------------------------------------------------------------
# analysis.run 冻结动作校验
# ---------------------------------------------------------------------------

def _wind_frozen_action(**overrides) -> dict:
    return {
        'solver': 'ANSYS',
        'caseSetId': 'run-wind-1',
        'runMode': 'REAL_AGENT_ANALYSIS',
        'workflowConfigPath': WIND_TEMPLATE,
        'loadKind': 'WIND',
        'loadTargetSetId': ANALYSIS_WIND_TARGET_SET_ID,
        'loadDatasetArtifactId': 'load-wind-1',
        'loadDatasetSha256': 'c' * 64,
        'loadMapping': _wind_mapping(),
        'responseIds': [],
        'resources': {'processCount': 1, 'coresPerProcess': 1, 'executionTimeoutS': 7200},
        **overrides,
    }


def test_dispatch_input_accepts_registered_wind_template() -> None:
    request = AnalysisDispatchInput(run_id='run-wind-1', frozen_action=_wind_frozen_action())

    assert request.frozen_action['workflowConfigPath'] == WIND_TEMPLATE


@pytest.mark.parametrize(
    'overrides',
    [
        {'solver': 'OPENSEESPY_INPROC'},
        {'workflowConfigPath': ANALYSIS_WORKFLOW_PATHS['ANSYS']},
        {'loadKind': 'TRAFFIC'},
        {'loadTargetSetId': 'STBRIDGE_TRAFFIC_CENTERLINE'},
        {'loadTargetSetId': None},
    ],
)
def test_dispatch_input_rejects_unregistered_wind_combinations(overrides: dict) -> None:
    with pytest.raises(ValidationError):
        AnalysisDispatchInput(run_id='run-wind-1', frozen_action=_wind_frozen_action(**overrides))


def test_dispatch_input_rejects_wind_target_on_earthquake_action() -> None:
    action = _wind_frozen_action(
        loadKind='EARTHQUAKE',
        workflowConfigPath=ANALYSIS_WORKFLOW_PATHS['ANSYS'],
    )
    with pytest.raises(ValidationError):
        AnalysisDispatchInput(run_id='run-wind-1', frozen_action=action)


# ---------------------------------------------------------------------------
# 登记模板本身
# ---------------------------------------------------------------------------

def test_wind_template_declares_ansys_run_mode_without_bundled_load_path() -> None:
    config = json.loads((REPO_ROOT / WIND_TEMPLATE).read_text(encoding='utf-8'))

    assert config['solver'] == 'ansys'
    assert config['solver_kwargs']['execution_mode'] == 'run'
    assert config['load_case']['load_type'] == 'wind'
    assert 'path' not in config['load_case']
    assert 'dt' not in config['load_case']
    assert 'duration' not in config['load_case']
    metadata = config['bridge_model']['metadata']
    assert metadata['wind_girder_load_nodes'] == list(DEFAULT_STBRIDGE_WIND_GIRDER_NODES)
    source = (REPO_ROOT / WIND_TEMPLATE).parent / config['bridge_model']['source_path']
    assert source.resolve().exists()


def test_wind_template_preflight_reports_undamped_wind_baseline() -> None:
    from pyansys_bridge.optimization.config_runner import preflight_config

    result = preflight_config(REPO_ROOT / WIND_TEMPLATE)

    assert result['kind'] == 'undamped_baseline'
    assert result['solver'] == 'ansys'
    assert result['load_type'] == 'wind'
    assert result['execution_mode'] == 'run'
    assert result['path_checks']['bridge_model.source_path']['exists'] is True


def test_bundled_wind_source_exposes_one_force_column_per_target_node() -> None:
    """默认风荷载记录的列顺序必须与登记目标集节点顺序逐位对应。

    通道与节点的对应关系只由列序表达，一旦错位就是"求解成功但荷载装错节点"，
    量纲上看不出来，因此这里直接锁定列名与节点顺序。
    """
    from app.services.load_artifact_service import (
        BUNDLED_WIND_RELATIVE_PATH,
        bundled_wind_node_columns,
    )

    source = REPO_ROOT / BUNDLED_WIND_RELATIVE_PATH
    columns = bundled_wind_node_columns(source.read_bytes())
    lines = source.read_text(encoding='utf-8').splitlines()

    assert columns == tuple(
        f'fy_node_{node}' for node in DEFAULT_STBRIDGE_WIND_GIRDER_NODES
    )
    assert lines[0] == 'time,' + ','.join(columns)
    # 3600 s、dt=1 s：3601 个采样点加一行表头。
    assert len(lines) == 3602
    assert float(lines[1].split(',')[0]) == 0.0
    assert float(lines[-1].split(',')[0]) == 3600.0


# ---------------------------------------------------------------------------
# PlatformStore：把冻结风荷载制品绑定到求解配置
# ---------------------------------------------------------------------------

def _bare_store() -> PlatformStore:
    return PlatformStore.__new__(PlatformStore)


def _standard_wind_csv(
    *,
    rows: list[tuple[float, float]] | None = None,
    load_kind: str = 'WIND',
    application_type: str = 'NODAL_FORCE',
    target_type: str = 'NODE_GROUP',
    target_id: str = ANALYSIS_WIND_TARGET_SET_ID,
    quantity: str = 'FORCE',
    unit: str = 'N',
    channel_id: str = 'channel_1',
) -> bytes:
    rows = rows if rows is not None else [(0.0, 100.0), (1.0, 120.0), (2.0, 90.0)]
    lines = [STANDARD_HEADER]
    for time_s, value in rows:
        lines.append(
            f'{time_s:g},{load_kind},{channel_id},{application_type},{target_type},'
            f'{target_id},UY,{quantity},{value:g},{unit}'
        )
    return ('\n'.join(lines) + '\n').encode('utf-8')


def _per_node_wind_csv(
    *,
    node_count: int = len(DEFAULT_STBRIDGE_WIND_GIRDER_NODES),
    times: list[float] | None = None,
    unit: str = 'N',
) -> bytes:
    """逐节点标准制品：与 `_standardize_channels` 一致按通道分段排列。

    第 i 个通道（1 基）第 j 个时刻（0 基）的值取 i*100+j，任何列错位都会改变数值。
    """
    times = times if times is not None else [0.0, 1.0, 2.0]
    lines = [STANDARD_HEADER]
    for channel_index in range(1, node_count + 1):
        for sample_index, time_s in enumerate(times):
            value = channel_index * 100 + sample_index
            lines.append(
                f'{time_s:g},WIND,channel_{channel_index},NODAL_FORCE,NODE_GROUP,'
                f'{ANALYSIS_WIND_TARGET_SET_ID},UY,FORCE,{value:g},{unit}'
            )
    return ('\n'.join(lines) + '\n').encode('utf-8')


def _store_with_wind_artifact(content: bytes, *, sha: str = 'd' * 64) -> PlatformStore:
    store = _bare_store()
    store.get_artifact = lambda _artifact_id: SimpleNamespace(  # type: ignore[method-assign]
        artifact=SimpleNamespace(artifact_id='load-wind-1', kind='CSV_TIMESERIES', sha256=sha),
        content=content,
    )
    return store


def _wind_params(**overrides) -> dict:
    return {
        'loadKind': 'WIND',
        'loadDatasetArtifactId': 'load-wind-1',
        'loadDatasetSha256': 'd' * 64,
        'loadTargetSetId': ANALYSIS_WIND_TARGET_SET_ID,
        **overrides,
    }


def test_wind_load_artifact_is_bound_to_solver_config_and_deck_nodes(tmp_path: Path) -> None:
    store = _store_with_wind_artifact(_standard_wind_csv())
    config: dict = {'bridge_model': {'name': 'STbridge', 'metadata': {}}}

    evidence = store._apply_agent_standard_wind_load(config, tmp_path, _wind_params(), 'ANSYS')

    solver_input = Path(config['load_case']['path'])
    assert solver_input.read_text(encoding='utf-8').splitlines() == ['100', '120', '90']
    assert config['load_case']['load_type'] == 'wind'
    assert config['load_case']['dt'] == 1.0
    assert config['load_case']['duration'] == 2.0
    assert config['bridge_model']['metadata']['wind_girder_load_nodes'] == list(
        DEFAULT_STBRIDGE_WIND_GIRDER_NODES,
    )
    assert evidence['artifactId'] == 'load-wind-1'
    assert evidence['sha256'] == 'd' * 64
    assert evidence['targetSetId'] == ANALYSIS_WIND_TARGET_SET_ID
    assert evidence['targetNodes'] == list(DEFAULT_STBRIDGE_WIND_GIRDER_NODES)
    assert evidence['unit'] == 'N'
    assert evidence['sampleCount'] == 3
    assert len(evidence['solverInputSha256']) == 64


def test_ansys_wind_binding_does_not_emit_opensees_mappings(tmp_path: Path) -> None:
    """ANSYS 侧靠 _equal_weight_points 分配，不需要也不应写 OpenSees 的 mapping 键。"""
    store = _store_with_wind_artifact(_standard_wind_csv())
    config: dict = {'bridge_model': {'name': 'STbridge', 'metadata': {}}}

    evidence = store._apply_agent_standard_wind_load(config, tmp_path, _wind_params(), 'ANSYS')

    metadata = config['bridge_model']['metadata']
    assert 'wind_load_mappings' not in metadata
    assert 'wind_load_nodes' not in metadata
    assert 'nodeWeight' not in evidence


def test_openseespy_wind_binding_distributes_total_force_by_equal_weight(tmp_path: Path) -> None:
    """逐节点 mapping 是 OpenSees 等权分配的唯一来源。

    缺少 mapping 时 wind.pyfrag 会退化成对每个目标节点施加整个目标集的总力，
    即 len(target_nodes) 倍超载，且结果仍是"求解成功"，量纲上看不出错。
    """
    store = _store_with_wind_artifact(_standard_wind_csv())
    config: dict = {'bridge_model': {'name': 'STbridge', 'metadata': {}}}

    evidence = store._apply_agent_standard_wind_load(
        config, tmp_path, _wind_params(), 'OPENSEESPY_INPROC',
    )

    nodes = list(DEFAULT_STBRIDGE_WIND_GIRDER_NODES)
    metadata = config['bridge_model']['metadata']
    # ANSYS 键保留：同一函数服务两个求解器，冻结证据口径不随求解器变化。
    assert metadata['wind_girder_load_nodes'] == nodes
    assert metadata['wind_load_nodes'] == nodes
    mappings = metadata['wind_load_mappings']
    assert [item['fem_node_id'] for item in mappings] == nodes
    assert all(item['dof'] == 'FY' for item in mappings)
    # 荷载文件只有一列总力，所有 mapping 共用该列。
    assert all(item['source_column'] == 1 for item in mappings)
    assert all(item['scale'] == pytest.approx(1.0 / len(nodes)) for item in mappings)
    # 权重之和必须是 1：这正是"总力等权分配"与"每节点全量"的分界。
    assert sum(item['scale'] for item in mappings) == pytest.approx(1.0)
    assert evidence['nodeWeight'] == pytest.approx(1.0 / len(nodes))
    assert evidence['distribution'] == 'EQUAL_WEIGHT_OVER_TARGET_NODES'


@pytest.mark.parametrize('solver', ['ANSYS', 'OPENSEESPY_INPROC'])
def test_per_node_wind_binding_emits_one_source_column_per_node(solver: str, tmp_path: Path) -> None:
    """逐节点形态必须给两个求解器都写 mapping，且列序与目标节点一一对应。

    ANSYS 的 `_wind_load_points` 只在读到 wind_load_mappings 时才逐列渲染，
    否则回落到 `_equal_weight_points`——每节点取第 1 列的 1/N，即欠载 N 倍，
    且求解照样成功，没有任何字段能看出来。
    """
    nodes = list(DEFAULT_STBRIDGE_WIND_GIRDER_NODES)
    store = _store_with_wind_artifact(_per_node_wind_csv())
    config: dict = {'bridge_model': {'name': 'STbridge', 'metadata': {}}}

    evidence = store._apply_agent_standard_wind_load(config, tmp_path, _wind_params(), solver)

    metadata = config['bridge_model']['metadata']
    mappings = metadata['wind_load_mappings']
    assert metadata['wind_girder_load_nodes'] == nodes
    assert metadata['wind_load_nodes'] == nodes
    assert [item['fem_node_id'] for item in mappings] == nodes
    # 第 i 个节点取第 i 列，整量施加：逐节点形态没有权重分配。
    assert [item['source_column'] for item in mappings] == list(range(1, len(nodes) + 1))
    assert all(item['scale'] == 1.0 for item in mappings)
    assert all(item['dof'] == 'FY' for item in mappings)
    assert evidence['distribution'] == 'PER_NODE_INDEPENDENT_FORCE_COLUMNS'
    assert evidence['channelCount'] == len(nodes)
    # 等权分配的权重字段不能出现，否则审查会把逐节点形态误读成总力分配。
    assert 'nodeWeight' not in evidence


def test_per_node_wind_solver_input_keeps_time_column_and_channel_order(tmp_path: Path) -> None:
    """求解侧矩阵必须带时间列，且第 i 列就是第 i 个通道。

    两侧读取器都按"首两行第一列递增"判定并剥掉时间列，缺时间列会让第一列
    力被当成时间轴丢掉，全部 source_column 随之左移一位。
    """
    store = _store_with_wind_artifact(_per_node_wind_csv())
    config: dict = {'bridge_model': {'name': 'STbridge', 'metadata': {}}}

    evidence = store._apply_agent_standard_wind_load(config, tmp_path, _wind_params(), 'ANSYS')

    lines = Path(evidence['solverInputPath']).read_text(encoding='utf-8').splitlines()
    node_count = len(DEFAULT_STBRIDGE_WIND_GIRDER_NODES)
    assert len(lines) == 3
    assert lines[0].split(',') == ['0', *[f'{index * 100}' for index in range(1, node_count + 1)]]
    assert lines[1].split(',') == ['1', *[f'{index * 100 + 1}' for index in range(1, node_count + 1)]]
    # 时间列递增是两侧读取器识别时间列的唯一依据。
    assert [float(line.split(',')[0]) for line in lines] == [0.0, 1.0, 2.0]
    assert evidence['sampleCount'] == 3
    assert evidence['timeStepS'] == 1.0


def test_per_node_wind_binding_rejects_channel_count_between_one_and_node_count(
    tmp_path: Path,
) -> None:
    """通道数既不是 1 也不等于目标节点数时无法判定对应关系，必须失败关闭。"""
    store = _store_with_wind_artifact(_per_node_wind_csv(node_count=3))

    with pytest.raises(HTTPException) as error:
        store._apply_agent_standard_wind_load({'bridge_model': {}}, tmp_path, _wind_params(), 'ANSYS')

    assert error.value.detail['code'] == 'UNSUPPORTED_STANDARD_WIND_LOAD'


@pytest.mark.parametrize(
    ('params', 'content', 'code'),
    [
        (
            {'loadKind': 'WIND', 'loadTargetSetId': ANALYSIS_WIND_TARGET_SET_ID},
            _standard_wind_csv(),
            'WIND_LOAD_ARTIFACT_REQUIRED',
        ),
        (
            _wind_params(loadTargetSetId=None),
            _standard_wind_csv(),
            'WIND_LOAD_TARGET_REQUIRED',
        ),
        (
            _wind_params(loadDatasetSha256='e' * 64),
            _standard_wind_csv(),
            'LOAD_ARTIFACT_HASH_MISMATCH',
        ),
        (_wind_params(), _standard_wind_csv(rows=[]), 'EMPTY_STANDARD_LOAD'),
        (_wind_params(), _standard_wind_csv(load_kind='TRAFFIC'), 'UNSUPPORTED_STANDARD_WIND_LOAD'),
        (
            _wind_params(),
            _standard_wind_csv(application_type='UNIFORM_EXCITATION'),
            'UNSUPPORTED_STANDARD_WIND_LOAD',
        ),
        (_wind_params(), _standard_wind_csv(quantity='ACCELERATION'), 'UNSUPPORTED_STANDARD_WIND_LOAD'),
        (_wind_params(), _standard_wind_csv(unit='kN'), 'UNSUPPORTED_STANDARD_WIND_LOAD'),
        (_wind_params(), _standard_wind_csv(target_type='NODE'), 'WIND_LOAD_TARGET_MISMATCH'),
        (
            _wind_params(),
            _standard_wind_csv(target_id='STBRIDGE_TRAFFIC_CENTERLINE'),
            'WIND_LOAD_TARGET_MISMATCH',
        ),
        (
            _wind_params(),
            _standard_wind_csv(rows=[(0.0, 1.0), (1.0, 2.0), (3.0, 3.0)]),
            'NON_UNIFORM_STANDARD_LOAD_TIME',
        ),
        (
            _wind_params(),
            _standard_wind_csv(rows=[(0.0, 1.0)]),
            'INVALID_STANDARD_LOAD_TIME',
        ),
    ],
)
def test_wind_load_binding_fails_closed(params: dict, content: bytes, code: str, tmp_path: Path) -> None:
    store = _store_with_wind_artifact(content)

    with pytest.raises(HTTPException) as error:
        store._apply_agent_standard_wind_load({'bridge_model': {}}, tmp_path, params, 'ANSYS')

    assert error.value.detail['code'] == code
    assert not list(tmp_path.iterdir())


def test_wind_load_binding_rejects_multiple_channels(tmp_path: Path) -> None:
    first = _standard_wind_csv().decode('utf-8').splitlines()
    second = _standard_wind_csv(channel_id='channel_2').decode('utf-8').splitlines()[1:]
    content = ('\n'.join([*first, *second]) + '\n').encode('utf-8')
    store = _store_with_wind_artifact(content)

    with pytest.raises(HTTPException) as error:
        store._apply_agent_standard_wind_load({'bridge_model': {}}, tmp_path, _wind_params(), 'ANSYS')

    assert error.value.detail['code'] == 'UNSUPPORTED_STANDARD_WIND_LOAD'


# ---------------------------------------------------------------------------
# 能力目录
# ---------------------------------------------------------------------------

def test_capability_catalog_advertises_each_scenario_per_solver() -> None:
    analysis = real_execution_registry.resolve('ANALYSIS', {'source': 'AGENT'})
    solver_batch = real_execution_registry.resolve('SOLVER_BATCH', {'runMode': 'REAL_AGENT_ANALYSIS'})

    for capability in (analysis, solver_batch):
        assert capability.status == 'LIVE'
        assert 'WIND' in capability.scenarios
        assert capability.supports(solver='ANSYS', scenario='WIND')
        assert capability.supports(solver='OPENSEESPY_INPROC', scenario='WIND')
        assert capability.supports(solver='ANSYS', scenario='EARTHQUAKE')
        assert capability.supports(solver='OPENSEESPY_INPROC', scenario='EARTHQUAKE')
        # 车流两个求解器都已登记模板，见 test_traffic_analysis_agent.py。
        assert 'TRAFFIC' in capability.scenarios
        assert capability.supports(solver='ANSYS', scenario='TRAFFIC')
        assert capability.supports(solver='OPENSEESPY_INPROC', scenario='TRAFFIC')


def test_optimization_capabilities_advertise_wind_on_both_solvers() -> None:
    """优化链路两个求解器都已登记风工况模板，目录须逐求解器声明并与真实门一致。

    此前 OpenSeesPy 缺风工况优化模板，目录整体声明 scenarios=('EARTHQUAKE',)；
    而真实门 platform_store._is_real_baseline_optimization_request 按已登记模板
    放行 WIND + ANSYS —— 广告比实际窄，风工况优化因此绕过了基线清单守卫
    （守卫遍历 capability.scenarios）。现在两者都已登记，目录必须同时广告。
    车流同理：两个求解器各有已登记模板，逐求解器都要广告。
    """
    for job_type in ('DAMPER_OPTIMIZATION', 'MULTI_OBJECTIVE_OPTIMIZATION'):
        capability = real_execution_registry.resolve(
            job_type,
            {'source': 'AGENT'} if job_type == 'DAMPER_OPTIMIZATION'
            else {'runMode': 'REAL_BASELINE_OPTIMIZATION'},
        )
        assert capability.status == 'LIVE'
        assert capability.supports(solver='ANSYS', scenario='WIND')
        assert capability.supports(solver='ANSYS', scenario='EARTHQUAKE')
        assert capability.supports(solver='OPENSEESPY_INPROC', scenario='EARTHQUAKE')
        assert capability.supports(solver='OPENSEESPY_INPROC', scenario='WIND')
        assert capability.supports(solver='ANSYS', scenario='TRAFFIC')
        assert capability.supports(solver='OPENSEESPY_INPROC', scenario='TRAFFIC')


def test_full_optimization_capability_matches_its_ansys_earthquake_only_gate() -> None:
    """完整优化的真实门只认 ANSYS + EARTHQUAKE，目录不得广告 OpenSeesPy。"""
    capability = real_execution_registry.resolve('FULL_OPTIMIZATION', {'source': 'AGENT'})

    assert capability.scenarios == ('EARTHQUAKE',)
    assert capability.solvers == ('ANSYS',)
    assert capability.supports(solver='ANSYS', scenario='EARTHQUAKE')
    assert not capability.supports(solver='OPENSEESPY_INPROC', scenario='EARTHQUAKE')
    assert not capability.supports(solver='ANSYS', scenario='WIND')
