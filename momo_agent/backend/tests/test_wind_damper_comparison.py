"""风工况 × ANSYS 阻尼器方案比选的契约、审批门与能力目录测试。

风工况的荷载制品/通道/目标集口径复用单次 ANALYSIS 的判定（见
test_wind_analysis_agent.py），这里覆盖比选链路自己的放行矩阵、
冻结动作和计划文案。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.agents.analysis import ANALYSIS_WIND_TARGET_SET_ID, ANALYSIS_WIND_WORKFLOW_PATHS
from app.agents.damper_comparison import DamperComparisonAgent, comparison_plan
from app.services import platform_store as platform_store_module
from app.services.agent_engineering import build_damper_comparison_contract
from app.services.platform_store import (
    AGENT_WIND_ANALYSIS_CONFIGS,
    AGENT_WIND_FORCE_COMPONENT,
    PlatformStore,
)
from app.services.real_execution import real_execution_registry


WIND_TEMPLATE = ANALYSIS_WIND_WORKFLOW_PATHS['ANSYS']
OPENSEES_WIND_TEMPLATE = ANALYSIS_WIND_WORKFLOW_PATHS['OPENSEESPY_INPROC']
EARTHQUAKE_TEMPLATE = 'docs/examples/templates/ansys_run_earthquake_baseline_template.json'
DAMPER_TYPES = ['VISCOUS', 'EDDY_CURRENT']


# ---------------------------------------------------------------------------
# 测试夹具
# ---------------------------------------------------------------------------

def _profile() -> dict:
    return {
        'schemaVersion': '1.0',
        'solver': {'name': 'ANSYS_MAPDL', 'version': '2024 R1'},
        'responseContract': {'id': 'ANSYS_RESPONSE_R1'},
        'userElement': {'name': 'USER300', 'calibrationHashVerified': True},
    }


def _agent(*, profile_paths: list | None = None) -> DamperComparisonAgent:
    def solver_profile_builder(path, **_kwargs):
        if profile_paths is not None:
            profile_paths.append(path)
        return _profile()

    return DamperComparisonAgent(
        planner=SimpleNamespace(),
        store=SimpleNamespace(),
        dispatcher=SimpleNamespace(),
        readiness_builder=lambda *_args, **_kwargs: {'status': 'READY'},
        preflight_runner=lambda path: {
            'kind': 'undamped_baseline',
            # 预检返回的求解器由模板决定；比选门会核对它与审批求解器一致。
            'solver': 'openseespy_inproc' if 'openseespy' in Path(path).name else 'ansys',
            'execution_mode': 'run',
            'path_checks': {'template': {'exists': True}},
        },
        solver_profile_builder=solver_profile_builder,
        calibration_builder=lambda types, **_kwargs: [
            {'damperType': item, 'status': 'VERIFIED', 'sha256': str(index) * 64}
            for index, item in enumerate(types, start=1)
        ],
    )


def _wind_channel(**overrides) -> dict:
    return {
        'valueColumn': 'wind_fy',
        'applicationType': 'NODAL_FORCE',
        'targetType': 'NODE_GROUP',
        'targetId': ANALYSIS_WIND_TARGET_SET_ID,
        'component': AGENT_WIND_FORCE_COMPONENT,
        'quantity': 'FORCE',
        'sourceUnit': 'N',
        'scale': 1.0,
        **overrides,
    }


def _mapping(load_kind: str = 'WIND', channels: list[dict] | None = None) -> dict:
    if load_kind != 'WIND':
        return {'loadKind': load_kind, 'channels': channels or []}
    return {
        'version': 2,
        'loadKind': 'WIND',
        'time': {'column': 'time', 'unit': 's'},
        'channels': [_wind_channel()] if channels is None else channels,
        'solver': 'ANSYS',
    }


def _run(*, solver: str = 'ANSYS', load_kind: str = 'WIND') -> dict:
    return {
        'runId': 'run-wind-comparison-1',
        'intent': {'loadKind': load_kind},
        'workflowContract': build_damper_comparison_contract(
            solver=solver,
            damper_types=DAMPER_TYPES,
            response_ids=['max_girder_end_displacement'],
            load_kind=load_kind,
        ),
    }


def _prepare(agent, run, mapping, *, artifact_id='load-wind-1', sha='c' * 64):
    return agent.prepare_approval(
        run,
        mapping=mapping,
        standard_artifact_id=artifact_id,
        standard_sha256=sha,
    )


# ---------------------------------------------------------------------------
# 契约构造
# ---------------------------------------------------------------------------

def test_contract_carries_wind_load_kind_and_scenario() -> None:
    contract = build_damper_comparison_contract(
        solver='ANSYS',
        damper_types=DAMPER_TYPES,
        response_ids=['max_girder_end_displacement'],
        load_kind='WIND',
    )

    assert contract['loadKind'] == 'WIND'
    assert contract['scenario'] == 'WIND'
    # 等最大出力剖面与荷载类型无关，风工况不得改动标定基准。
    assert contract['comparisonBasis'] == 'EQUAL_PEAK_FORCE'
    assert contract['forceCapScope'] == 'PER_PHYSICAL_DAMPER'
    assert contract['forceCapN'] == 4_000_000.0
    assert contract['designVelocityMps'] == 0.2


def test_contract_defaults_to_earthquake_unchanged() -> None:
    contract = build_damper_comparison_contract(
        solver='ANSYS',
        damper_types=DAMPER_TYPES,
        response_ids=['max_girder_end_displacement'],
    )

    assert contract['loadKind'] == 'EARTHQUAKE'
    assert contract['scenario'] == 'EARTHQUAKE'


@pytest.mark.parametrize('load_kind', ['GENERIC_NODAL', 'OPERATION'])
def test_contract_rejects_unregistered_load_kinds(load_kind: str) -> None:
    """车流已登记比选（见 test_traffic_damper_comparison.py），这里只守未登记工况。"""
    with pytest.raises(ValueError, match='load kind'):
        build_damper_comparison_contract(
            solver='ANSYS',
            damper_types=DAMPER_TYPES,
            response_ids=['max_girder_end_displacement'],
            load_kind=load_kind,
        )


# ---------------------------------------------------------------------------
# 审批门：放行
# ---------------------------------------------------------------------------

def test_wind_comparison_freezes_wind_template_target_and_load_artifact() -> None:
    prepared = _prepare(_agent(), _run(), _mapping())

    assert prepared.passed
    frozen = prepared.frozen_action
    assert frozen['loadKind'] == 'WIND'
    assert frozen['scenario'] == 'WIND'
    assert frozen['workflowConfigPath'] == WIND_TEMPLATE
    assert frozen['loadTargetSetId'] == ANALYSIS_WIND_TARGET_SET_ID
    assert frozen['loadDatasetArtifactId'] == 'load-wind-1'
    assert frozen['loadDatasetSha256'] == 'c' * 64
    assert frozen['runMode'] == 'REAL_DAMPER_COMPARISON'
    assert len(frozen['cases']) == 2
    target = next(item for item in frozen['inputProvenance'] if item['field'] == 'loadTargetSetId')
    assert target['value'] == ANALYSIS_WIND_TARGET_SET_ID


def test_wind_comparison_still_reads_user300_calibration_from_joint_template() -> None:
    """风模板声明 omit_dampers 且没有 damper_calibration 段。

    USER300 校准是阻尼器单元属性，与荷载类型无关；若改为从风模板取
    profile，require_user300 门会因 userElement 缺失而失败关闭。
    """
    profile_paths: list = []

    prepared = _prepare(_agent(profile_paths=profile_paths), _run(), _mapping())

    assert prepared.passed
    assert profile_paths[0].name == 'ansys_run_joint_baseline_workflow_template.json'


def test_earthquake_comparison_keeps_earthquake_template_and_no_wind_target() -> None:
    prepared = _prepare(
        _agent(),
        _run(load_kind='EARTHQUAKE'),
        _mapping('EARTHQUAKE'),
    )

    assert prepared.passed
    assert prepared.frozen_action['workflowConfigPath'] == EARTHQUAKE_TEMPLATE
    assert prepared.frozen_action['scenario'] == 'EARTHQUAKE'
    assert 'loadTargetSetId' not in prepared.frozen_action


# ---------------------------------------------------------------------------
# 审批门：失败关闭矩阵
# ---------------------------------------------------------------------------

def test_wind_comparison_on_openseespy_freezes_opensees_template() -> None:
    """风工况比选已放行 OpenSeesPy：模板与求解器必须一起冻结。"""
    prepared = _prepare(_agent(), _run(solver='OPENSEESPY_INPROC'), _mapping())

    assert prepared.passed
    assert prepared.frozen_action['solver'] == 'OPENSEESPY_INPROC'
    assert prepared.frozen_action['workflowConfigPath'] == OPENSEES_WIND_TEMPLATE


def test_comparison_on_unregistered_solver_fails_closed() -> None:
    """放行口径按注册表判定；未登记模板的求解器仍必须失败关闭。"""
    run = _run()
    run['workflowContract']['solver'] = 'ABAQUS'

    prepared = _prepare(_agent(), run, _mapping())

    assert not prepared.passed
    assert prepared.failure_status == 'UNSUPPORTED'
    assert prepared.frozen_action is None


def test_wind_comparison_without_registered_load_artifact_fails_closed() -> None:
    prepared = _prepare(_agent(), _run(), _mapping(), artifact_id=None, sha=None)

    assert not prepared.passed
    assert prepared.preflight['reason'] == 'WIND_LOAD_ARTIFACT_REQUIRED'
    assert prepared.frozen_action is None


@pytest.mark.parametrize(
    ('channel_overrides', 'reason'),
    [
        ({'applicationType': 'UNIFORM_EXCITATION'}, 'WIND_LOAD_MAPPING_UNSUPPORTED'),
        ({'quantity': 'ACCELERATION'}, 'WIND_LOAD_MAPPING_UNSUPPORTED'),
        ({'sourceUnit': 'g'}, 'WIND_LOAD_MAPPING_UNSUPPORTED'),
        ({'component': 'UX'}, 'WIND_LOAD_MAPPING_UNSUPPORTED'),
        ({'targetType': 'NODE'}, 'WIND_LOAD_TARGET_UNSUPPORTED'),
        ({'targetId': 'STBRIDGE_TRAFFIC_CENTERLINE'}, 'WIND_LOAD_TARGET_UNSUPPORTED'),
    ],
)
def test_wind_comparison_rejects_mismatched_channel_contract(
    channel_overrides: dict,
    reason: str,
) -> None:
    prepared = _prepare(
        _agent(),
        _run(),
        _mapping(channels=[_wind_channel(**channel_overrides)]),
    )

    assert not prepared.passed
    assert prepared.preflight['reason'] == reason
    assert prepared.frozen_action is None


def test_wind_comparison_requires_exactly_one_channel() -> None:
    channels = [_wind_channel(), _wind_channel(valueColumn='wind_fx', component='UX')]

    prepared = _prepare(_agent(), _run(), _mapping(channels=channels))

    assert not prepared.passed
    assert prepared.preflight['reason'] == 'WIND_LOAD_MAPPING_UNSUPPORTED'


@pytest.mark.parametrize('load_kind', ['GENERIC_NODAL'])
def test_other_load_kinds_stay_closed_for_comparison(load_kind: str) -> None:
    run = _run(load_kind='EARTHQUAKE')
    run['intent'] = {'loadKind': load_kind}

    prepared = _prepare(_agent(), run, {'loadKind': load_kind, 'channels': []})

    assert not prepared.passed
    assert prepared.preflight['reason'] == 'PRODUCTION_GATE'
    assert prepared.frozen_action is None


# ---------------------------------------------------------------------------
# 计划文案
# ---------------------------------------------------------------------------

def test_plan_text_names_the_frozen_load_kind() -> None:
    assert '风荷载' in comparison_plan('WIND')[0]
    assert '地震荷载' in comparison_plan('EARTHQUAKE')[0]
    # 其余步骤与荷载类型无关，两种工况逐字相同。
    assert comparison_plan('WIND')[1:] == comparison_plan('EARTHQUAKE')[1:]


def test_prepared_approval_plan_follows_load_kind() -> None:
    prepared = _prepare(_agent(), _run(), _mapping())

    assert '风荷载' in prepared.plan[0]


# ---------------------------------------------------------------------------
# 能力目录
# ---------------------------------------------------------------------------

def test_capability_catalog_advertises_wind_comparison_on_both_solvers() -> None:
    capability = real_execution_registry.resolve('DAMPER_COMPARISON', {'source': 'AGENT'})

    assert capability.status == 'LIVE'
    for solver in ('ANSYS', 'OPENSEESPY_INPROC'):
        assert capability.supports(solver=solver, scenario='WIND')
        assert capability.supports(solver=solver, scenario='EARTHQUAKE')
        assert capability.supports(solver=solver, scenario='TRAFFIC')
    # 未登记工况不因新增求解器被顺带放行。
    assert not capability.supports(solver='OPENSEESPY_INPROC', scenario='GENERIC_NODAL')
    assert not capability.supports(solver='ABAQUS', scenario='WIND')



# ---------------------------------------------------------------------------
# 执行装配：OpenSeesPy 比选
# ---------------------------------------------------------------------------

STANDARD_HEADER = (
    'time_s,load_kind,channel_id,application_type,target_type,target_id,'
    'component,quantity,value,unit'
)
OPENSEES_CASES = [
    {
        'caseId': 'viscous',
        'damperType': 'VISCOUS',
        'solverModule': 'damper_viscous',
        'parameters': {'c': 7600.0, 'alpha': 0.8, 'vfloor': 0.001},
        'theoreticalPeakForceN': 4.0e6,
        'designVelocityMps': 0.5,
        'parameterSource': 'AGENT',
    },
    {
        'caseId': 'eddy',
        'damperType': 'EDDY_CURRENT',
        'solverModule': 'damper_eddy_current',
        'parameters': {'fmax': 2000.0, 'vcr': 0.4},
        'theoreticalPeakForceN': 4.0e6,
        'designVelocityMps': 0.5,
        'parameterSource': 'AGENT',
    },
]


def _standard_wind_csv() -> bytes:
    lines = [STANDARD_HEADER]
    for time_s, value in [(0.0, 100.0), (1.0, 120.0), (2.0, 90.0)]:
        lines.append(
            f'{time_s:g},WIND,channel_1,NODAL_FORCE,NODE_GROUP,'
            f'{ANALYSIS_WIND_TARGET_SET_ID},{AGENT_WIND_FORCE_COMPONENT},FORCE,{value:g},N'
        )
    return ('\n'.join(lines) + '\n').encode('utf-8')


def _comparison_params(**overrides) -> dict:
    return {
        'runMode': 'REAL_DAMPER_COMPARISON',
        'solver': 'OPENSEESPY_INPROC',
        'scenario': 'WIND',
        'loadKind': 'WIND',
        'comparisonBasis': 'EQUAL_PEAK_FORCE',
        'forceCapN': 4.0e6,
        'forceCapScope': 'PER_DAMPER',
        'designVelocityMps': 0.5,
        'selectedLayoutId': 'TWO_PER_TOWER',
        'selectedLayout': {'nodePairs': [[36, 517]], 'direction': 'X', 'physicalCountPerTower': 2},
        'responseIds': ['max_girder_end_displacement'],
        'loadDatasetArtifactId': 'load-wind-1',
        'loadDatasetSha256': 'd' * 64,
        'loadTargetSetId': ANALYSIS_WIND_TARGET_SET_ID,
        'cases': OPENSEES_CASES,
        **overrides,
    }


def _comparison_store(tmp_path: Path, monkeypatch) -> tuple[PlatformStore, dict[str, dict], list[str]]:
    """驱动真实执行装配，只替身求解器与制品登记。

    模板与 `_earthquake_baseline_config` 都走真实实现：双求解器放行后
    风险恰恰在模板装配（OpenSees 把求解选项放在嵌套 solver 字典里）。
    """
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
    written: dict[str, dict] = {}
    real_write = store._write_json_config

    def record_config(path, payload):
        # 与批量链路同一归档口径：case 目录 + 文件名组合，避免跨 case 互相覆盖。
        written[f'{Path(path).parent.name}/{Path(path).name}'] = payload
        real_write(path, payload)

    monkeypatch.setattr(store, '_write_json_config', record_config)

    def fake_case_runner(config_path: Path, *, execution_timeout_s: float | None) -> dict:
        # 真实 OpenSeesPy 执行会在 case 目录落一份 .py 命令流，这里照样落盘，
        # 让制品登记走真实的后缀判定分支。
        command_path = Path(config_path).parent / 'opensees_commands.py'
        command_path.write_text('# opensees command stream\n', encoding='utf-8')
        return {
            'case_id': Path(config_path).parent.name,
            'solver': 'openseespy_inproc',
            'status': 'completed',
            'objectives': {'max_girder_end_displacement': 0.1},
            'metadata': {
                'solver_design': {'execution_mode': 'run'},
                'is_verified_solver_output': True,
                'command_stream': {'path': str(command_path)},
            },
        }

    monkeypatch.setattr(store, '_run_real_damper_comparison_case', fake_case_runner)
    registered: list[str] = []
    monkeypatch.setattr(store, '_register_json_file_artifact', lambda **kwargs: SimpleNamespace(
        artifact_id=kwargs['name'], name=kwargs['name'], sha256='a' * 64,
    ))
    real_register = store._register_artifact

    def record_register(**kwargs):
        registered.append(kwargs['name'])
        return real_register(**kwargs)

    monkeypatch.setattr(store, '_register_artifact', record_register)
    monkeypatch.setattr(store, '_register_inquiry_csv_artifacts', lambda _case_dir: [])
    monkeypatch.setattr(store, '_register_result_catalog', lambda _run_dir: SimpleNamespace(
        artifact_id='catalog', name='result_catalog.json', sha256='b' * 64,
    ))
    monkeypatch.setattr(store, '_register_real_output_manifest', lambda _run_dir: SimpleNamespace(
        artifact_id='manifest', name='real_output_manifest.json', sha256='c' * 64,
    ))
    return store, written, registered


def test_openseespy_wind_comparison_keeps_dampers_through_nested_solver_config(
    tmp_path: Path, monkeypatch
) -> None:
    """OpenSees 模板把求解选项放在嵌套 solver 字典里，且自带 omit_dampers=true。

    比选装配只写 solver_kwargs，靠 SolverConfig.from_mapping 的 legacy_kwargs
    后置生效来覆盖模板值。这条优先级一旦反转，比选会静默跑成无控基线：
    三个算例的响应会完全相同，而没有任何字段显示阻尼器被丢掉了。
    """
    from pyansys_bridge.core.solver_factory import SolverConfig

    store, written, _registered = _comparison_store(tmp_path, monkeypatch)

    store._generate_real_damper_comparison_artifacts(_comparison_params(), job_id=None)

    template_name = Path(AGENT_WIND_ANALYSIS_CONFIGS['OPENSEESPY_INPROC']).name
    case_configs = {
        name.split('/')[0]: payload
        for name, payload in written.items()
        if name.endswith(f'/{template_name}')
    }
    assert set(case_configs) == {'viscous', 'eddy'}
    for case_id, config in case_configs.items():
        # 模板嵌套值仍是 true，说明覆盖发生在合并层而不是被就地改写。
        assert config['solver']['omit_dampers'] is True
        assert config['solver_kwargs']['omit_dampers'] is False
        # 合并后的最终求解入参才是求解器真正收到的东西。
        merged = SolverConfig.from_mapping(
            config['solver'], legacy_kwargs=config['solver_kwargs']
        )
        assert merged.type == 'openseespy_inproc'
        assert merged.kwargs['omit_dampers'] is False
        assert merged.kwargs['damper_module'] == (
            'damper_viscous' if case_id == 'viscous' else 'damper_eddy_current'
        )
        # 等峰值比选的 case 参数已是求解器单位，不得再乘工程单位系数。
        assert merged.kwargs['damper_c_scale'] == 1.0
        # nproc 是 MAPDL 专属；OpenSees 侧按业务 caseId 写进度文件。
        assert 'nproc' not in config['solver_kwargs']
        assert config['solver_kwargs']['progress_case_id'] == case_id
        # 风荷载逐 case 落盘，不回退模板路径。
        assert Path(config['load_case']['path']).name == 'agent_wind_nodal_force_n.txt'
        assert config['load_case']['dt'] == 1.0
        # OpenSees 风模块不读 wind_girder_load_nodes，必须有逐节点等权 mapping。
        assert config['bridge_model']['metadata']['wind_load_mappings']


def test_openseespy_comparison_registers_python_command_stream(
    tmp_path: Path, monkeypatch
) -> None:
    """命令流制品名的后缀取实际落盘文件：OpenSees 是 .py，不是 .apdl。"""
    store, _written, registered = _comparison_store(tmp_path, monkeypatch)

    store._generate_real_damper_comparison_artifacts(_comparison_params(), job_id=None)

    command_names = [name for name in registered if 'executed_command_stream' in name]
    assert command_names == [
        'viscous_executed_command_stream.py',
        'eddy_executed_command_stream.py',
    ]


def test_openseespy_comparison_summary_records_solver_and_load_kind(
    tmp_path: Path, monkeypatch
) -> None:
    store, written, _registered = _comparison_store(tmp_path, monkeypatch)

    store._generate_real_damper_comparison_artifacts(_comparison_params(), job_id=None)

    summary = next(
        payload for payload in written.values()
        if payload.get('mode') == 'real_damper_comparison'
    )
    assert summary['solver'] == 'OPENSEESPY_INPROC'
    assert summary['loadKind'] == 'WIND'
    assert summary['allVerifiedExecution'] is True
    assert [case['caseId'] for case in summary['caseResults']] == ['viscous', 'eddy']
    # 风荷载证据必须逐 case 留痕，供审查核对施加对象。
    for case in summary['caseResults']:
        assert case['customLoadEvidence']['targetSetId'] == ANALYSIS_WIND_TARGET_SET_ID


def test_comparison_execution_rejects_unregistered_load_kind(
    tmp_path: Path, monkeypatch
) -> None:
    """未登记工况必须显式 422，而不是让模板取值抛 KeyError 变成 500。

    车流已登记（见 AGENT_COMPARISON_CONFIGS_BY_LOAD_KIND），这里用仍未登记的
    GENERIC_NODAL 守这道门。
    """
    store, _written, _registered = _comparison_store(tmp_path, monkeypatch)

    with pytest.raises(HTTPException) as error:
        store._generate_real_damper_comparison_artifacts(
            _comparison_params(loadKind='GENERIC_NODAL', scenario='GENERIC_NODAL'), job_id=None
        )

    assert error.value.detail['code'] == 'UNSUPPORTED_REAL_COMPARISON_LOAD_KIND'
