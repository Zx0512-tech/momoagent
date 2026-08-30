"""风工况 × ANSYS 阻尼器参数批量计算的契约、审批门与执行装配测试。"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.agents.analysis import ANALYSIS_WIND_TARGET_SET_ID, ANALYSIS_WIND_WORKFLOW_PATHS
from app.agents.damper_parameter_sweep import DamperParameterSweepAgent
from app.services import platform_store as platform_store_module
from app.services.agent_engineering import (
    DamperParameterSweepIntent,
    build_parameter_sweep_contract,
)
from app.services.platform_store import PlatformStore


REPO_ROOT = Path(__file__).resolve().parents[3]
WIND_TEMPLATE = ANALYSIS_WIND_WORKFLOW_PATHS['ANSYS']
STANDARD_HEADER = (
    'time_s,load_kind,channel_id,application_type,target_type,target_id,'
    'component,quantity,value,unit'
)
VISCOUS_CASES = [
    {'caseId': 'wind_c1000', 'damperType': 'VISCOUS', 'parameters': {'c': 1000.0, 'alpha': 0.5, 'vfloor': 1e-6}},
    {'caseId': 'wind_c2000', 'damperType': 'VISCOUS', 'parameters': {'c': 2000.0, 'alpha': 0.5, 'vfloor': 1e-6}},
]


# ---------------------------------------------------------------------------
# 契约：loadKind 参数化
# ---------------------------------------------------------------------------

def test_wind_contract_carries_wind_load_kind_and_scenario() -> None:
    contract = build_parameter_sweep_contract(
        solver='ANSYS',
        cases=VISCOUS_CASES,
        response_ids=['max_girder_end_displacement'],
        load_kind='WIND',
    )

    assert contract['loadKind'] == 'WIND'
    assert contract['scenario'] == 'WIND'
    assert [case['solverModule'] for case in contract['cases']] == [
        'damper_user300_viscous',
        'damper_user300_viscous',
    ]


def test_earthquake_contract_keeps_earthquake_default() -> None:
    contract = build_parameter_sweep_contract(
        solver='OPENSEESPY_INPROC',
        cases=VISCOUS_CASES,
        response_ids=['max_girder_end_displacement'],
    )

    assert contract['loadKind'] == 'EARTHQUAKE'
    assert contract['scenario'] == 'EARTHQUAKE'


def test_wind_contract_allows_openseespy() -> None:
    """规划器默认求解器是 OpenSees，用户只说"风"时会产出该组合。

    两个求解器都有已登记的风工况批量模板，契约层直接放行；
    审批侧的冻结结果见 test_wind_sweep_on_openseespy_freezes_openseespy_template。
    """
    contract = build_parameter_sweep_contract(
        solver='OPENSEESPY_INPROC',
        cases=VISCOUS_CASES,
        response_ids=['max_girder_end_displacement'],
        load_kind='WIND',
    )

    assert contract['loadKind'] == 'WIND'
    assert contract['solver'] == 'OPENSEESPY_INPROC'


def test_contract_rejects_unregistered_load_kind() -> None:
    # 车流已登记（见 test_traffic_damper_sweep.py），这里用仍未接入
    # 批量链的 OPERATION 守住契约层门禁。
    with pytest.raises(ValueError, match='Unsupported parameter sweep load kind'):
        build_parameter_sweep_contract(
            solver='ANSYS',
            cases=VISCOUS_CASES,
            response_ids=['max_girder_end_displacement'],
            load_kind='OPERATION',
        )


def test_wind_sweep_intent_accepts_openseespy() -> None:
    """风工况两个求解器都有已登记模板，意图校验不再按求解器收窄。"""
    intent = DamperParameterSweepIntent.model_validate({
        'taskType': 'DAMPER_PARAMETER_SWEEP',
        'solver': 'OPENSEESPY_INPROC',
        'scenario': 'WIND',
        'cases': VISCOUS_CASES,
        'responseIds': ['max_girder_end_displacement'],
        'requiresRealFem': True,
        'summary': '风工况批量计算',
    })

    assert intent.scenario == 'WIND'
    assert intent.solver == 'OPENSEESPY_INPROC'


def test_sweep_intent_still_rejects_unregistered_scenario() -> None:
    """守住 DamperParameterSweepIntent 自己的 scenario Literal。

    注意这个类在生产代码里零引用：规划器走 EngineeringIntent ->
    build_parameter_sweep_contract，车流在那条真入口上两个求解器都已放行
    （见 test_traffic_damper_sweep.py）。这里的 TRAFFIC 被拒只反映该类的
    Literal 尚未跟上，不代表车流批量链未接入。
    """
    with pytest.raises(ValidationError):
        DamperParameterSweepIntent.model_validate({
            'taskType': 'DAMPER_PARAMETER_SWEEP',
            'solver': 'ANSYS',
            'scenario': 'TRAFFIC',
            'cases': VISCOUS_CASES,
            'responseIds': ['max_girder_end_displacement'],
            'requiresRealFem': True,
            'summary': '车辆工况批量计算',
        })


def test_wind_sweep_intent_accepts_ansys() -> None:
    intent = DamperParameterSweepIntent.model_validate({
        'taskType': 'DAMPER_PARAMETER_SWEEP',
        'solver': 'ANSYS',
        'scenario': 'WIND',
        'cases': VISCOUS_CASES,
        'responseIds': ['max_girder_end_displacement'],
        'requiresRealFem': True,
        'summary': '风工况批量计算',
    })

    assert intent.scenario == 'WIND'


# ---------------------------------------------------------------------------
# 审批门
# ---------------------------------------------------------------------------

def _agent(
    *,
    readiness_status: str = 'READY',
    solver_name: str = 'ANSYS',
) -> DamperParameterSweepAgent:
    # 求解器版本画像必须跟随被测求解器：ANSYS 需要 USER300 标定证据，
    # OpenSeesPy 没有该元素，画像里塞 USER300 会让门禁测出的是假通过。
    if solver_name == 'OPENSEESPY':
        profile = {
            'schemaVersion': '1.0',
            'solver': {'name': 'OPENSEESPY', 'version': '3.5.1'},
            'responseContract': {'id': 'OPENSEES_RESPONSE_R1'},
        }
    else:
        profile = {
            'schemaVersion': '1.0',
            'solver': {'name': 'ANSYS_MAPDL', 'version': '2024 R1'},
            'responseContract': {'id': 'ANSYS_RESPONSE_R1'},
            'userElement': {'name': 'USER300', 'calibrationHashVerified': True},
        }
    agent = DamperParameterSweepAgent(
        planner=SimpleNamespace(),
        store=SimpleNamespace(),
        dispatcher=SimpleNamespace(),
        readiness_builder=lambda *_args, **_kwargs: {'status': readiness_status},
        solver_profile_builder=lambda *_args, **_kwargs: profile,
    )
    return agent


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
    }


def _wind_run(solver: str = 'ANSYS') -> dict:
    return {
        'runId': 'run-wind-sweep-1',
        'goal': '风工况下批量计算阻尼器参数',
        'intent': {'loadKind': 'WIND'},
        'workflowContract': build_parameter_sweep_contract(
            solver=solver,
            cases=VISCOUS_CASES,
            response_ids=['max_girder_end_displacement'],
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


def test_wind_sweep_freezes_registered_wind_template_and_target_set() -> None:
    prepared = _prepare(_agent(), _wind_run(), _wind_mapping())

    assert prepared.passed is True
    frozen = prepared.frozen_action
    assert frozen['workflowConfigPath'] == WIND_TEMPLATE
    assert frozen['scenario'] == 'WIND'
    assert frozen['loadKind'] == 'WIND'
    assert frozen['loadTargetSetId'] == ANALYSIS_WIND_TARGET_SET_ID
    assert frozen['runMode'] == 'REAL_DAMPER_PARAMETER_SWEEP'
    assert [case['caseId'] for case in frozen['cases']] == ['wind_c1000', 'wind_c2000']


def test_earthquake_sweep_keeps_no_target_set_and_earthquake_template() -> None:
    run = {
        'runId': 'run-eq-sweep-1',
        'goal': '地震工况批量计算',
        'intent': {'loadKind': 'EARTHQUAKE'},
        'workflowContract': build_parameter_sweep_contract(
            solver='ANSYS',
            cases=VISCOUS_CASES,
            response_ids=['max_girder_end_displacement'],
        ),
    }

    prepared = _prepare(_agent(), run, {'loadKind': 'EARTHQUAKE', 'channels': []})

    assert prepared.passed is True
    assert prepared.frozen_action['scenario'] == 'EARTHQUAKE'
    assert 'loadTargetSetId' not in prepared.frozen_action


def test_wind_sweep_on_openseespy_freezes_openseespy_template() -> None:
    # 两个求解器的风工况批量模板都已登记，OpenSeesPy 组合放行并冻结自己的模板。
    # 求解器专属细节（逐节点等权 mapping）在 test_wind_parameter_sweep_agent.py 覆盖。
    run = {
        'runId': 'run-wind-sweep-opensees',
        'goal': '风工况下批量计算阻尼器参数',
        'intent': {'loadKind': 'WIND'},
        'workflowContract': build_parameter_sweep_contract(
            solver='OPENSEESPY_INPROC',
            cases=VISCOUS_CASES,
            response_ids=['max_girder_end_displacement'],
            load_kind='WIND',
        ),
    }

    prepared = _prepare(_agent(solver_name='OPENSEESPY'), run, _wind_mapping())

    assert prepared.passed is True
    assert prepared.frozen_action['scenario'] == 'WIND'
    assert prepared.frozen_action['workflowConfigPath'] == (
        ANALYSIS_WIND_WORKFLOW_PATHS['OPENSEESPY_INPROC']
    )
    assert prepared.frozen_action['loadTargetSetId'] == ANALYSIS_WIND_TARGET_SET_ID


def test_wind_sweep_without_registered_load_artifact_fails_closed() -> None:
    prepared = _prepare(_agent(), _wind_run(), _wind_mapping(), artifact_id=None, sha=None)

    assert prepared.passed is False
    assert prepared.preflight['reason'] == 'WIND_LOAD_ARTIFACT_REQUIRED'


@pytest.mark.parametrize(
    ('channel_overrides', 'reason'),
    [
        ({'applicationType': 'UNIFORM_EXCITATION'}, 'WIND_LOAD_MAPPING_UNSUPPORTED'),
        ({'quantity': 'ACCELERATION'}, 'WIND_LOAD_MAPPING_UNSUPPORTED'),
        ({'sourceUnit': 'm/s2'}, 'WIND_LOAD_MAPPING_UNSUPPORTED'),
        ({'component': 'UX'}, 'WIND_LOAD_MAPPING_UNSUPPORTED'),
        ({'targetType': 'NODE'}, 'WIND_LOAD_TARGET_UNSUPPORTED'),
        ({'targetId': 'STBRIDGE_TRAFFIC_CENTERLINE'}, 'WIND_LOAD_TARGET_UNSUPPORTED'),
    ],
)
def test_wind_sweep_rejects_mismatched_channel_contract(channel_overrides: dict, reason: str) -> None:
    mapping = _wind_mapping([_wind_channel(**channel_overrides)])

    prepared = _prepare(_agent(), _wind_run(), mapping)

    assert prepared.passed is False
    assert prepared.preflight['reason'] == reason


def test_wind_sweep_requires_exactly_one_channel() -> None:
    mapping = _wind_mapping([_wind_channel(), _wind_channel(valueColumn='wind_fy_2')])

    prepared = _prepare(_agent(), _wind_run(), mapping)

    assert prepared.passed is False
    assert prepared.preflight['reason'] == 'WIND_LOAD_MAPPING_UNSUPPORTED'


# ---------------------------------------------------------------------------
# 平台侧请求门禁
# ---------------------------------------------------------------------------

def _sweep_params(**overrides) -> dict:
    return {
        'runMode': 'REAL_DAMPER_PARAMETER_SWEEP',
        'solver': 'ANSYS',
        'scenario': 'WIND',
        'cases': [
            {**case, 'solverModule': 'damper_user300_viscous'}
            for case in VISCOUS_CASES
        ],
        **overrides,
    }


def test_store_accepts_ansys_wind_sweep_request() -> None:
    assert PlatformStore._is_real_damper_parameter_sweep_request(
        'SOLVER_BATCH',
        _sweep_params(),
    ) is True


def test_store_accepts_openseespy_wind_sweep_request() -> None:
    # 两个求解器都有已登记的风工况批量模板，执行门禁按登记表放行，
    # 与审批门 PARAMETER_SWEEP_WORKFLOW_PATHS_BY_LOAD_KIND 同口径。
    assert PlatformStore._is_real_damper_parameter_sweep_request(
        'SOLVER_BATCH',
        _sweep_params(
            solver='OPENSEESPY_INPROC',
            cases=[{**case, 'solverModule': 'damper_viscous'} for case in VISCOUS_CASES],
        ),
    ) is True


def test_store_rejects_unregistered_scenario_sweep_request() -> None:
    # 车流已登记批量模板；OPERATION 仍未接入，用它守住平台侧放行门。
    assert PlatformStore._is_real_damper_parameter_sweep_request(
        'SOLVER_BATCH',
        _sweep_params(scenario='OPERATION'),
    ) is False


def test_store_still_accepts_openseespy_earthquake_sweep_request() -> None:
    assert PlatformStore._is_real_damper_parameter_sweep_request(
        'SOLVER_BATCH',
        _sweep_params(
            solver='OPENSEESPY_INPROC',
            scenario='EARTHQUAKE',
            cases=[{**case, 'solverModule': 'damper_viscous'} for case in VISCOUS_CASES],
        ),
    ) is True


# ---------------------------------------------------------------------------
# 执行装配：每个 case 绑定同一份冻结风荷载并保留阻尼器
# ---------------------------------------------------------------------------

def _standard_wind_csv() -> bytes:
    lines = [STANDARD_HEADER]
    for time_s, value in [(0.0, 100.0), (1.0, 120.0), (2.0, 90.0)]:
        lines.append(
            f'{time_s:g},WIND,channel_1,NODAL_FORCE,NODE_GROUP,'
            f'{ANALYSIS_WIND_TARGET_SET_ID},UY,FORCE,{value:g},N'
        )
    return ('\n'.join(lines) + '\n').encode('utf-8')


def _wind_sweep_store(tmp_path: Path, monkeypatch) -> tuple[PlatformStore, dict[str, dict]]:
    store = PlatformStore(state_path=tmp_path / 'state' / 'platform.sqlite3', recover_orphans=False)
    monkeypatch.setattr(
        platform_store_module,
        'EARTHQUAKE_WORKFLOW_OUTPUT_ROOT',
        tmp_path / 'real_workflows',
    )
    monkeypatch.setattr(
        store,
        'get_artifact',
        lambda _artifact_id: SimpleNamespace(
            artifact=SimpleNamespace(artifact_id='load-wind-1', kind='CSV_TIMESERIES', sha256='d' * 64),
            content=_standard_wind_csv(),
        ),
    )
    monkeypatch.setattr(store, '_load_json_config', lambda _path: {
        'bridge_model': {'name': 'STbridge', 'metadata': {}},
        'solver': 'ansys',
        'solver_kwargs': {},
    })
    written: dict[str, dict] = {}

    def record_config(path, payload):
        # 同一个 case 目录里既写求解配置也写案例摘要，只按目录名归档会互相覆盖；
        # 各 case 的配置文件又同名，只按文件名归档会跨 case 覆盖。用两者组合。
        written[f'{Path(path).parent.name}/{Path(path).name}'] = payload

    monkeypatch.setattr(store, '_write_json_config', record_config)
    monkeypatch.setattr(
        store,
        '_run_real_damper_comparison_case',
        lambda config_path, **_kwargs: {
            'status': 'completed',
            'case_id': Path(config_path).parent.name,
            'objectives': {'max_girder_end_displacement': 1.0},
            'metadata': {
                'solver_design': {'execution_mode': 'run'},
                'is_verified_solver_output': True,
            },
        },
    )
    monkeypatch.setattr(store, '_register_json_file_artifact', lambda **kwargs: SimpleNamespace(
        artifact_id=kwargs['name'], name=kwargs['name'], sha256='a' * 64,
    ))
    monkeypatch.setattr(store, '_register_inquiry_csv_artifacts', lambda _case_dir: [])
    monkeypatch.setattr(store, '_register_result_catalog', lambda _run_dir: SimpleNamespace(
        artifact_id='catalog', name='result_catalog.json', sha256='b' * 64,
    ))
    monkeypatch.setattr(store, '_register_real_output_manifest', lambda _run_dir: SimpleNamespace(
        artifact_id='manifest', name='real_output_manifest.json', sha256='c' * 64,
    ))
    return store, written


def test_wind_sweep_binds_frozen_wind_load_to_every_case(tmp_path: Path, monkeypatch) -> None:
    store, written = _wind_sweep_store(tmp_path, monkeypatch)

    artifacts = store._generate_real_damper_parameter_sweep_artifacts(_sweep_params(
        loadKind='WIND',
        loadDatasetArtifactId='load-wind-1',
        loadDatasetSha256='d' * 64,
        loadTargetSetId=ANALYSIS_WIND_TARGET_SET_ID,
        selectedLayoutId='TWO_PER_TOWER',
        selectedLayout={'nodePairs': [[36, 517]], 'direction': 'X', 'physicalCountPerTower': 2},
        responseIds=['max_girder_end_displacement'],
        maxConcurrentCases=1,
    ))

    template_name = Path(WIND_TEMPLATE).name
    case_configs = {
        name.split('/')[0]: payload
        for name, payload in written.items()
        if name.endswith(f'/{template_name}')
    }
    assert set(case_configs) == {'wind_c1000', 'wind_c2000'}
    for config in case_configs.values():
        assert config['load_case']['load_type'] == 'wind'
        assert config['load_case']['dt'] == 1.0
        # 每个 case 都绑定同一份冻结制品导出的求解输入，不回退到模板自带路径。
        assert Path(config['load_case']['path']).name == 'agent_wind_nodal_force_n.txt'
        # 阻尼器必须保留：批量参数计算的对象就是阻尼器本身。
        assert config['solver_kwargs']['omit_dampers'] is False
        assert config['solver_kwargs']['damper_module'] == 'damper_user300_viscous'
        assert config['bridge_model']['metadata']['wind_girder_load_nodes']
    assert case_configs['wind_c1000']['damper_params']['c'] == 1000.0
    assert case_configs['wind_c2000']['damper_params']['c'] == 2000.0
    assert any(artifact.name == 'real_damper_parameter_sweep_summary.json' for artifact in artifacts)


def test_wind_sweep_summary_records_wind_load_kind(tmp_path: Path, monkeypatch) -> None:
    store, written = _wind_sweep_store(tmp_path, monkeypatch)

    store._generate_real_damper_parameter_sweep_artifacts(_sweep_params(
        loadKind='WIND',
        loadDatasetArtifactId='load-wind-1',
        loadDatasetSha256='d' * 64,
        loadTargetSetId=ANALYSIS_WIND_TARGET_SET_ID,
        selectedLayoutId='TWO_PER_TOWER',
        selectedLayout={'nodePairs': [[36, 517]], 'direction': 'X', 'physicalCountPerTower': 2},
        responseIds=['max_girder_end_displacement'],
        maxConcurrentCases=1,
    ))

    summary = next(
        payload for name, payload in written.items()
        if payload.get('mode') == 'real_damper_parameter_sweep'
    )
    assert summary['loadKind'] == 'WIND'
    assert summary['solver'] == 'ANSYS'
    assert summary['allVerifiedExecution'] is True
    assert [case['caseId'] for case in summary['caseResults']] == ['wind_c1000', 'wind_c2000']


def test_sweep_execution_rejects_unregistered_load_kind(tmp_path: Path, monkeypatch) -> None:
    """执行侧对未登记工况必须显式 422，而不是让模板取值抛 KeyError。

    风与车流两个求解器都已登记，这道门现在守的是 OPERATION 等尚无冻结荷载通道的工况。
    """
    store, _written = _wind_sweep_store(tmp_path, monkeypatch)

    with pytest.raises(HTTPException) as error:
        store._generate_real_damper_parameter_sweep_artifacts(_sweep_params(
            loadKind='OPERATION',
            selectedLayoutId='TWO_PER_TOWER',
            selectedLayout={'nodePairs': [[36, 517]], 'direction': 'X', 'physicalCountPerTower': 2},
        ))

    assert error.value.detail['code'] == 'UNSUPPORTED_REAL_PARAMETER_SWEEP_REQUEST'
