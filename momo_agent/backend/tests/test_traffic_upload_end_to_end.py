"""上传车流文件 → 自动标准化 → 执行侧接受产物的完整回路。

这条测试是"车流上传做完了"的判据。前面几个测试各自钉住一层（推断、标准化产物
形态、schema），但真正要证明的是：一份用户上传的逐节点车流表，经过链路产出的
矩阵制品与逐节点 mapping 制品，能被 platform_store._apply_agent_standard_traffic_load
原样吃下——它是求解侧的入口，有三条双射硬约束。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.agent_engineering import EngineeringIntent
from app.services.agent_service import agent_service
from app.services.platform_dispatcher import platform_dispatcher
from app.services.platform_store import (
    AGENT_LOAD_TARGET_SETS,
    AGENT_TRAFFIC_FORCE_COMPONENT,
    PlatformStore,
    platform_store,
)


client = TestClient(app)

TRAFFIC_TARGET_SET_ID = 'STBRIDGE_TRAFFIC_DECK_NODES'
TRAFFIC_NODES = tuple(AGENT_LOAD_TARGET_SETS[TRAFFIC_TARGET_SET_ID])
SAMPLE_TIMES = (0.0, 1.0, 2.0, 3.0)


@pytest.fixture(autouse=True)
def isolated_platform_state(tmp_path: Path):
    isolated = PlatformStore(state_path=tmp_path / 'agent_state.sqlite3')
    original = (
        platform_store.state_path,
        platform_store.repository,
        platform_store.jobs,
        platform_store.artifacts,
        platform_store.engineering_config,
        platform_dispatcher.store,
    )
    platform_store.state_path = isolated.state_path
    platform_store.repository = isolated.repository
    platform_store.jobs = isolated.jobs
    platform_store.artifacts = isolated.artifacts
    platform_store.engineering_config = isolated.engineering_config
    platform_dispatcher.store = platform_store
    try:
        yield
    finally:
        platform_dispatcher.stop()
        (
            platform_store.state_path,
            platform_store.repository,
            platform_store.jobs,
            platform_store.artifacts,
            platform_store.engineering_config,
            platform_dispatcher.store,
        ) = original


def _cell(node: int, row_index: int) -> float:
    """(节点, 时刻) 唯一确定数值。

    车流的失效模式是"列错位"而不是"数值错"：所有节点共用同一个数值时，列顺序
    写错了断言照样通过。这里让每一格都可反查，错位立刻现形。
    """

    return float(node + 1000 * row_index)


def _traffic_csv(*, unit_suffix: str = '_N', nodes: tuple[int, ...] = TRAFFIC_NODES) -> bytes:
    header = ['time_s', *[f'node_{node}_fy{unit_suffix}' for node in nodes]]
    lines = [','.join(header)]
    for row_index, time_value in enumerate(SAMPLE_TIMES):
        lines.append(','.join([
            f'{time_value:g}',
            *[f'{_cell(node, row_index):g}' for node in nodes],
        ]))
    return ('\n'.join(lines) + '\n').encode('utf-8')


def _configure_traffic_analysis(monkeypatch, tmp_path: Path) -> None:
    # 这一层钉的是荷载导入这条旧接口链路（上传 → 自动标准化 → 求解审批），与
    # test_agent_load_api.py 同一口径，所以同样显式用 LEGACY 运行时：Harness
    # 运行时下 planner 的 patch 不生效。
    monkeypatch.setenv('MOMO_AGENT_RUNTIME', 'LEGACY')
    monkeypatch.setattr(
        'app.services.platform_store.EARTHQUAKE_WORKFLOW_OUTPUT_ROOT',
        tmp_path / 'real_traffic_analyses',
    )
    monkeypatch.setattr(
        'app.services.agent_service.build_readiness_report',
        lambda *_args, **_kwargs: {'status': 'READY', 'blockingComponents': [], 'components': {}},
    )
    monkeypatch.setattr(
        'app.services.agent_service.preflight_config',
        lambda _path: {
            'kind': 'undamped_baseline',
            'solver': 'ansys',
            'load_type': 'traffic',
            'execution_mode': 'run',
            'path_checks': {'model': {'exists': True}},
        },
    )

    def plan_traffic(_goal, *, requested_task='AUTO', **_kwargs):
        return SimpleNamespace(
            planner_mode='LLM',
            intent=EngineeringIntent(
                taskType='ANALYSIS',
                solver='ANSYS',
                loadKind='TRAFFIC',
                responseIds=['max_girder_end_displacement'],
                missingFields=[],
                summary='受控车流荷载分析意图',
            ),
        )

    monkeypatch.setattr(agent_service.planner, 'plan_engineering', plan_traffic)


def _upload_and_run(
    content: bytes, file_name: str = 'traffic_per_node.csv'
) -> tuple[dict, dict, dict]:
    session = client.post('/api/v1/agent/sessions', json={'title': '逐节点车流荷载'}).json()
    uploaded = client.post(f'/api/v1/load-files?fileName={file_name}', content=content).json()
    run = client.post(
        f'/api/v1/agent/sessions/{session["sessionId"]}/messages',
        json={
            'content': '使用 ANSYS 分析附件逐节点车流荷载并提取主梁梁端位移',
            'fileId': uploaded['fileId'],
            'taskType': 'ANALYSIS',
        },
    ).json()
    return uploaded, run, session


def test_uploaded_traffic_file_auto_standardizes_and_executor_accepts_artifacts(
    monkeypatch, tmp_path: Path
) -> None:
    _configure_traffic_analysis(monkeypatch, tmp_path)
    uploaded, run, session = _upload_and_run(_traffic_csv())

    # 标准化已自动完成，停在下一道独立的求解审批上——人工闸门没有被绕过。
    approval = run['pendingApproval']
    assert approval['action'] == 'RUN_SOLVER'
    frozen = approval['frozenAction']
    assert frozen['loadKind'] == 'TRAFFIC'
    assert frozen['loadTargetSetId'] == TRAFFIC_TARGET_SET_ID

    suggestion = client.get(f'/api/v1/load-imports/{uploaded["importId"]}').json()['suggestion']
    assert suggestion['standardizeDecision'] == 'AUTO'
    channel = suggestion['mapping']['channels'][0]
    assert channel['applicationType'] == 'NODAL_FORCE_MATRIX'
    assert channel['quantity'] == 'FORCE'
    assert channel['sourceUnit'] == 'N'
    assert channel['matrixColumnCount'] == len(TRAFFIC_NODES)

    # 矩阵制品与逐节点 mapping 制品都被冻结，两个 SHA 都要对得上实际制品。
    matrix = platform_store.get_artifact(frozen['loadDatasetArtifactId'])
    mapping_artifact = platform_store.get_artifact(frozen['loadPointMappingArtifactId'])
    assert matrix.artifact.sha256 == frozen['loadDatasetSha256']
    assert mapping_artifact.artifact.sha256 == frozen['loadPointMappingSha256']

    # 产物形态：time_s + 每节点一列的稠密矩阵。
    reader = list(csv.reader(matrix.content.decode('utf-8').splitlines()))
    header = reader[0]
    assert header[0] == 'time_s'
    assert len(header) == 1 + len(TRAFFIC_NODES)
    assert len(reader) == 1 + len(SAMPLE_TIMES)

    node_mappings = json.loads(mapping_artifact.content.decode('utf-8'))
    mapped_nodes = [item['fem_node_id'] for item in node_mappings]
    # 执行侧的三条硬约束，逐条在这里先验证一次。
    assert [item['source_column'] for item in node_mappings] == list(range(1, len(header)))
    assert all(
        column == f'node_{node}_fy_N'
        for node, column in zip(mapped_nodes, header[1:])
    )
    assert sorted(mapped_nodes) == sorted(TRAFFIC_NODES)

    # 逐格反查：每个节点每个时刻的值必须落在它自己的列上，列错位会在这里现形。
    for row_index, row in enumerate(reader[1:]):
        for column_index, node in enumerate(mapped_nodes, start=1):
            assert float(row[column_index]) == _cell(node, row_index)

    # 判据：执行侧原样吃下这两份产物。
    baseline_config: dict = {}
    run_dir = tmp_path / 'executor_run'
    run_dir.mkdir()
    evidence = platform_store._apply_agent_standard_traffic_load(
        baseline_config,
        run_dir,
        {
            'loadKind': 'TRAFFIC',
            'loadDatasetArtifactId': frozen['loadDatasetArtifactId'],
            'loadDatasetSha256': frozen['loadDatasetSha256'],
            'loadTargetSetId': frozen['loadTargetSetId'],
            'loadPointMappingArtifactId': frozen['loadPointMappingArtifactId'],
            'loadPointMappingSha256': frozen['loadPointMappingSha256'],
        },
    )

    assert evidence['nodeCount'] == len(TRAFFIC_NODES)
    assert evidence['unit'] == 'N'
    assert evidence['component'] == AGENT_TRAFFIC_FORCE_COMPONENT
    assert evidence['timeStepS'] == pytest.approx(1.0)
    assert evidence['distribution'] == 'PER_NODE_INDEPENDENT_TIME_HISTORY'
    assert sorted(evidence['targetNodes']) == sorted(TRAFFIC_NODES)
    # 求解侧输入保持矩阵形态。
    solver_input = Path(evidence['solverInputPath'])
    solver_header = solver_input.read_text(encoding='utf-8').splitlines()[0].split(',')
    assert solver_header == header

    messages = client.get(f'/api/v1/agent/sessions/{session["sessionId"]}').json()['messages']
    message = next(item for item in reversed(messages) if item['role'] == 'ASSISTANT')
    assert f'{len(TRAFFIC_NODES)} 节点逐节点矩阵' in message['content']
    assert '统一输出 N' in message['content']


def test_uploaded_traffic_file_in_kn_is_converted_to_newton(monkeypatch, tmp_path: Path) -> None:
    """源文件写 kN：值 ×1000，列名统一改写成执行侧要求的 _fy_N 口径。"""

    _configure_traffic_analysis(monkeypatch, tmp_path)
    _, run, _ = _upload_and_run(_traffic_csv(unit_suffix='_kN'), 'traffic_kn.csv')

    frozen = run['pendingApproval']['frozenAction']
    matrix = platform_store.get_artifact(frozen['loadDatasetArtifactId'])
    reader = list(csv.reader(matrix.content.decode('utf-8').splitlines()))
    header = reader[0]

    assert header[1:] == [f'node_{node}_fy_N' for node in TRAFFIC_NODES]
    mapping_artifact = platform_store.get_artifact(frozen['loadPointMappingArtifactId'])
    mapped_nodes = [item['fem_node_id'] for item in json.loads(mapping_artifact.content.decode('utf-8'))]
    for row_index, row in enumerate(reader[1:]):
        for column_index, node in enumerate(mapped_nodes, start=1):
            assert float(row[column_index]) == pytest.approx(_cell(node, row_index) * 1000.0)


def test_uploaded_traffic_file_missing_a_node_falls_back_to_manual_mapping(
    monkeypatch, tmp_path: Path
) -> None:
    """列集合与登记目标集不一致时不得自动放行，退回人工映射确认。"""

    _configure_traffic_analysis(monkeypatch, tmp_path)
    uploaded, run, _ = _upload_and_run(
        _traffic_csv(nodes=TRAFFIC_NODES[:-1]), 'traffic_missing.csv'
    )

    suggestion = client.get(f'/api/v1/load-imports/{uploaded["importId"]}').json()['suggestion']
    assert suggestion['standardizeDecision'] == 'ASK'
    # 没有生成求解审批：链路停在映射确认上。
    approval = run.get('pendingApproval') or {}
    assert approval.get('action') != 'RUN_SOLVER'
