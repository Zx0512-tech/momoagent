"""用户上传 FEM 模型与节点/单元响应输出的测试。"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.agents.analysis import AnalysisAgent
from app.agents.core import RepositorySessionMemory
from app.services.agent_engineering import build_engineering_contract
from app.services.model_import_service import ModelImportService
from app.services.platform_store import PlatformStore


STATIC_APDL = """! demo model
/PREP7
ET,1,BEAM188
N,1,0,0,0
N,2,1,0,0
E,1,2
FINISH
"""

PARAMETRIC_APDL = """/PREP7
*do,i,1,100
N,i,i*0.5,0,0
*enddo
NBLOCK,6,SOLID
EBLOCK,19,SOLID
FINISH
"""


# ---------------------------------------------------------------------------
# ModelImportService.inspect
# ---------------------------------------------------------------------------

def test_inspect_static_apdl_passes_and_counts_commands() -> None:
    report = ModelImportService().inspect('bridge.txt', STATIC_APDL.encode('utf-8'))

    assert report['format'] == 'APDL'
    assert report['nodeCommandCount'] == 2
    assert report['elementCommandCount'] == 1
    assert report['parametricModel'] is False
    assert report['validation']['forbiddenCommandScan'] == 'PASSED'
    assert report['validation']['nodeIdEnumeration'] == 'STATIC_COMMANDS_ONLY'


def test_inspect_parametric_model_skips_static_node_enumeration() -> None:
    report = ModelImportService().inspect('bridge.inp', PARAMETRIC_APDL.encode('utf-8'))

    assert report['parametricModel'] is True
    assert report['doLoopCount'] == 1
    assert report['blockCommandCount'] == 2
    assert report['validation']['nodeIdEnumeration'] == 'SKIPPED_PARAMETRIC'


@pytest.mark.parametrize('command', ['/SYS,del *.db', '/DELETE,file,db', '~CAT5IN,part'])
def test_inspect_rejects_forbidden_apdl_commands(command: str) -> None:
    content = f"/PREP7\n{command}\nN,1,0,0,0\nE,1,1\n".encode('utf-8')
    with pytest.raises(HTTPException) as exc:
        ModelImportService().inspect('bad.txt', content)
    assert exc.value.detail['code'] == 'FORBIDDEN_APDL_COMMAND'
    assert exc.value.detail['hits'][0]['line'] == 2


@pytest.mark.parametrize(
    ('file_name', 'content', 'code'),
    [
        ('model.txt', b'', 'EMPTY_FILE'),
        ('model.rst', STATIC_APDL.encode('utf-8'), 'UNSUPPORTED_FILE_TYPE'),
        ('model.txt', b'N,1,0,0,0\nE,1,1\n', 'MISSING_PREP7'),
        ('model.txt', b'/PREP7\nE,1,2\n', 'NO_NODE_DEFINITIONS'),
        ('model.txt', b'/PREP7\nN,1,0,0,0\n', 'NO_ELEMENT_DEFINITIONS'),
        ('../evil.txt', STATIC_APDL.encode('utf-8'), 'INVALID_FILE_NAME'),
    ],
)
def test_inspect_rejects_invalid_uploads(file_name: str, content: bytes, code: str) -> None:
    with pytest.raises(HTTPException) as exc:
        ModelImportService().inspect(file_name, content)
    assert exc.value.detail['code'] == code


def test_upload_registers_fem_model_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _register_artifact(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(artifact_id='art_fem_1', sha256='a' * 64)

    from app.services import model_import_service as module

    monkeypatch.setattr(module.platform_store, 'register_artifact', _register_artifact)
    result = ModelImportService().upload('bridge.txt', STATIC_APDL.encode('utf-8'))

    assert captured['kind'] == 'FEM_MODEL'
    assert captured['content'] == STATIC_APDL.encode('utf-8')
    assert result['artifactId'] == 'art_fem_1'
    assert result['inspection']['nodeCommandCount'] == 2
    assert result['usage']['solver'] == 'ANSYS'


# ---------------------------------------------------------------------------
# build_engineering_contract 扩展字段
# ---------------------------------------------------------------------------

def test_contract_freezes_custom_model_and_response_outputs() -> None:
    contract = build_engineering_contract(
        task_type='ANALYSIS',
        solver='ANSYS',
        damper_type=None,
        response_ids=[],
        model_artifact_id='art_fem_1',
        response_nodes=[36, 107],
        response_element_ids=[12],
        response_direction='Z',
    )

    assert contract['model'] == 'USER_FEM:art_fem_1'
    assert contract['modelArtifactId'] == 'art_fem_1'
    assert contract['responseNodes'] == [36, 107]
    assert contract['responseElementIds'] == [12]
    assert contract['responseComponent'] == 2


def test_contract_defaults_keep_stbridge_model_without_response_targets() -> None:
    contract = build_engineering_contract(
        task_type='ANALYSIS',
        solver='OPENSEESPY_INPROC',
        damper_type=None,
        response_ids=[],
    )

    assert contract['model'] == 'STbridge'
    assert contract['modelArtifactId'] is None
    assert contract['responseNodes'] == []


def test_contract_accepts_opensees_element_outputs() -> None:
    """OpenSees 点名单元内力已放行；仅上传自定义 FEM 模型仍限 ANSYS。"""

    contract = build_engineering_contract(
        task_type='ANALYSIS',
        solver='OPENSEESPY_INPROC',
        damper_type=None,
        response_ids=[],
        response_element_ids=[12, 20],
    )

    assert contract['responseElementIds'] == [12, 20]
    assert contract['modelArtifactId'] is None


@pytest.mark.parametrize(
    'kwargs',
    [
        {'model_artifact_id': 'art_1', 'solver': 'OPENSEESPY_INPROC', 'response_nodes': [1]},
        {'model_artifact_id': 'art_1', 'solver': 'ANSYS'},  # 缺响应节点
        {'response_element_ids': [3], 'solver': 'OPENSEESPY_INPROC', 'task_type_override': 'DAMPER_OPTIMIZATION'},
        {'model_artifact_id': 'art_1', 'solver': 'ANSYS', 'response_nodes': [1], 'task_type_override': 'DAMPER_OPTIMIZATION'},
        {'response_nodes': [1], 'response_direction': 'Q', 'solver': 'ANSYS'},
    ],
)
def test_contract_rejects_unsupported_model_output_combinations(kwargs: dict) -> None:
    task_type = kwargs.pop('task_type_override', 'ANALYSIS')
    with pytest.raises(ValueError):
        build_engineering_contract(
            task_type=task_type,
            damper_type='VISCOUS' if task_type != 'ANALYSIS' else None,
            response_ids=[],
            **kwargs,
        )


# ---------------------------------------------------------------------------
# AnalysisAgent.prepare_approval 冻结模型与响应输出
# ---------------------------------------------------------------------------

def _agent(store=None) -> AnalysisAgent:
    return AnalysisAgent(
        planner=SimpleNamespace(),
        memory=RepositorySessionMemory(lambda _session_id: []),
        store=store,
        preflight_handler=lambda _payload: {
            'passed': True,
            'workflow_path': 'docs/examples/templates/ansys_run_earthquake_baseline_template.json',
            'readiness': {'status': 'READY'},
            'config': {'kind': 'undamped_baseline'},
            'solver_version_profile': {
                'schemaVersion': '1.0',
                'solver': {'name': 'ANSYS_MAPDL', 'version': '2024 R1'},
                'responseContract': {'id': 'ANSYS_RESPONSE_R1'},
            },
        },
    )


class _FakeStore:
    def __init__(self, kind: str = 'FEM_MODEL', sha: str = 'f' * 64) -> None:
        self._record = SimpleNamespace(artifact=SimpleNamespace(kind=kind, sha256=sha))

    def get_artifact(self, _artifact_id: str):
        return self._record


def _analysis_run_with_custom_model() -> dict:
    return {
        'runId': 'run-fem-1',
        'intent': {'loadKind': 'EARTHQUAKE'},
        'workflowContract': build_engineering_contract(
            task_type='ANALYSIS',
            solver='ANSYS',
            damper_type=None,
            response_ids=[],
            model_artifact_id='art_fem_1',
            response_nodes=[36, 107],
            response_element_ids=[12],
            response_direction='Y',
        ),
    }


def test_prepare_approval_freezes_model_artifact_and_response_targets() -> None:
    agent = _agent(store=_FakeStore())

    prepared = agent.prepare_approval(
        _analysis_run_with_custom_model(),
        mapping={'loadKind': 'EARTHQUAKE', 'channels': []},
        standard_artifact_id='load-1',
        standard_sha256='c' * 64,
    )

    assert prepared.passed
    frozen = prepared.frozen_action
    assert frozen['modelArtifactId'] == 'art_fem_1'
    assert frozen['modelSha256'] == 'f' * 64
    assert frozen['responseNodes'] == [36, 107]
    assert frozen['responseElementIds'] == [12]
    assert frozen['responseComponent'] == 1
    assert prepared.contract_updates['modelSha256'] == 'f' * 64


def test_prepare_approval_rejects_unregistered_model_artifact() -> None:
    agent = _agent(store=_FakeStore(kind='JSON_SUMMARY'))

    prepared = agent.prepare_approval(
        _analysis_run_with_custom_model(),
        mapping={'loadKind': 'EARTHQUAKE', 'channels': []},
        standard_artifact_id='load-1',
        standard_sha256='c' * 64,
    )

    assert not prepared.passed
    assert prepared.preflight['reason'] == 'FEM_MODEL_NOT_REGISTERED'


def test_prepare_approval_rejects_custom_model_on_openseespy() -> None:
    agent = _agent(store=_FakeStore())
    run = _analysis_run_with_custom_model()
    run['workflowContract'] = {
        **run['workflowContract'],
        'solver': 'OPENSEESPY_INPROC',
    }

    prepared = agent.prepare_approval(
        run,
        mapping={'loadKind': 'EARTHQUAKE', 'channels': []},
        standard_artifact_id='load-1',
        standard_sha256='c' * 64,
    )

    assert not prepared.passed
    assert prepared.preflight['reason'] == 'CUSTOM_MODEL_SOLVER_UNSUPPORTED'


# ---------------------------------------------------------------------------
# PlatformStore 响应输出与自定义模型落盘
# ---------------------------------------------------------------------------

def _bare_store() -> PlatformStore:
    return PlatformStore.__new__(PlatformStore)


def test_response_outputs_keep_rst_channels_for_default_model_nodes_only() -> None:
    store = _bare_store()
    config = {
        'solver_kwargs': {
            'postprocessor': {'mode': 'ansys-dpf-rst', 'response_nodes': [36, 107], 'damper_pairs': []},
        },
    }

    evidence = store._apply_agent_response_outputs(
        config,
        {'responseNodes': [55, 66], 'responseComponent': 1},
        'ANSYS',
        custom_model=False,
    )

    postprocessor = config['solver_kwargs']['postprocessor']
    assert postprocessor['mode'] == 'ansys-dpf-rst'
    assert postprocessor['response_nodes'] == [55, 66]
    assert postprocessor['cumulative_displacement_node'] == 55
    assert evidence['postprocessorMode'] == 'ansys-dpf-rst'


def test_response_outputs_switch_to_node_postprocessor_for_custom_model() -> None:
    store = _bare_store()
    config = {
        'solver_kwargs': {
            'postprocessor': {'mode': 'ansys-dpf-rst', 'ansys_path': 'C:/ansys'},
        },
    }

    evidence = store._apply_agent_response_outputs(
        config,
        {'responseNodes': [10], 'responseElementIds': [20], 'responseComponent': 0},
        'ANSYS',
        custom_model=True,
    )

    postprocessor = config['solver_kwargs']['postprocessor']
    assert postprocessor['mode'] == 'ansys-dpf-nodes'
    assert postprocessor['response_nodes'] == [10]
    assert postprocessor['response_elements'] == [20]
    assert postprocessor['ansys_path'] == 'C:/ansys'
    assert evidence['postprocessorMode'] == 'ansys-dpf-nodes'


def test_response_outputs_override_openseespy_solver_nodes() -> None:
    store = _bare_store()
    config = {'solver': {'type': 'openseespy_inproc', 'response_nodes': [36, 107], 'response_dof': 1}}

    evidence = store._apply_agent_response_outputs(
        config,
        {'responseNodes': [42], 'responseComponent': 2},
        'OPENSEESPY_INPROC',
        custom_model=False,
    )

    assert config['solver']['response_nodes'] == [42]
    assert config['solver']['response_dof'] == 3
    assert evidence['postprocessorMode'] == 'openseespy_inproc_nodes'


def test_opensees_response_outputs_accept_element_ids() -> None:
    """OpenSees 点名单元内力必须落到 solver 配置，而不是被拒绝。"""

    store = _bare_store()
    config: dict = {}

    evidence = store._apply_agent_response_outputs(
        config,
        {'responseNodes': [42], 'responseElementIds': [12, 20], 'responseComponent': 1},
        'OPENSEESPY_INPROC',
        custom_model=False,
    )

    assert config['solver']['response_elements'] == [12, 20]
    # 单元内力取全局分量下标，不像节点 DOF 那样加一。
    assert config['solver']['response_element_component'] == 1
    assert config['solver']['response_dof'] == 2
    assert evidence['responseElementIds'] == [12, 20]
    assert evidence['postprocessorMode'] == 'openseespy_inproc_nodes_elements'


@pytest.mark.parametrize(
    ('params', 'solver', 'custom_model', 'code'),
    [
        ({'responseNodes': list(range(1, 18))}, 'ANSYS', False, 'TOO_MANY_RESPONSE_TARGETS'),
        ({'responseNodes': [1, 1]}, 'ANSYS', False, 'INVALID_RESPONSE_TARGETS'),
        ({'responseNodes': [1], 'responseComponent': 5}, 'ANSYS', False, 'INVALID_RESPONSE_COMPONENT'),
        ({'responseElementIds': [7, 7]}, 'OPENSEESPY_INPROC', False, 'INVALID_RESPONSE_TARGETS'),
        ({}, 'ANSYS', True, 'RESPONSE_NODES_REQUIRED'),
    ],
)
def test_response_outputs_reject_invalid_requests(
    params: dict, solver: str, custom_model: bool, code: str,
) -> None:
    store = _bare_store()
    with pytest.raises(HTTPException) as exc:
        store._apply_agent_response_outputs({}, params, solver, custom_model=custom_model)
    assert exc.value.detail['code'] == code


def test_custom_fem_model_is_written_to_run_dir_and_rewires_bridge_model(tmp_path: Path) -> None:
    store = _bare_store()
    content = STATIC_APDL.encode('utf-8')
    from hashlib import sha256 as _sha256

    digest = _sha256(content).hexdigest()
    store.get_artifact = lambda _artifact_id: SimpleNamespace(  # type: ignore[method-assign]
        artifact=SimpleNamespace(
            artifact_id='art_fem_1', kind='FEM_MODEL', sha256=digest, name='bridge.txt',
        ),
        content=content,
    )
    config = {'bridge_model': {'name': 'STbridge', 'source_path': 'old.txt'}}

    evidence = store._apply_agent_custom_fem_model(
        config,
        tmp_path,
        {'modelArtifactId': 'art_fem_1', 'modelSha256': digest},
        'ANSYS',
    )

    written = tmp_path / 'agent_user_fem_model.txt'
    assert written.read_bytes() == content
    assert config['bridge_model']['source_path'] == str(written)
    assert evidence['sha256'] == digest
    assert evidence['solverInputSha256'] == digest


def test_custom_fem_model_rejects_hash_mismatch(tmp_path: Path) -> None:
    store = _bare_store()
    store.get_artifact = lambda _artifact_id: SimpleNamespace(  # type: ignore[method-assign]
        artifact=SimpleNamespace(artifact_id='art_fem_1', kind='FEM_MODEL', sha256='a' * 64, name='bridge.txt'),
        content=b'/PREP7\nN,1\nE,1\n',
    )

    with pytest.raises(HTTPException) as exc:
        store._apply_agent_custom_fem_model(
            {},
            tmp_path,
            {'modelArtifactId': 'art_fem_1', 'modelSha256': 'b' * 64},
            'ANSYS',
        )
    assert exc.value.detail['code'] == 'MODEL_ARTIFACT_HASH_MISMATCH'


def test_custom_fem_model_rejects_non_ansys_solver(tmp_path: Path) -> None:
    store = _bare_store()
    with pytest.raises(HTTPException) as exc:
        store._apply_agent_custom_fem_model(
            {},
            tmp_path,
            {'modelArtifactId': 'art_fem_1', 'modelSha256': 'a' * 64},
            'OPENSEESPY_INPROC',
        )
    assert exc.value.detail['code'] == 'CUSTOM_MODEL_SOLVER_UNSUPPORTED'


# ---------------------------------------------------------------------------
# pyansys_bridge ansys-dpf-nodes 后处理器
# ---------------------------------------------------------------------------

def test_node_response_postprocessor_factory_normalizes_inputs() -> None:
    from pyansys_bridge.core.postprocessor import ansys_dpf_node_response_postprocessor

    postprocessor = ansys_dpf_node_response_postprocessor(
        response_nodes=[36, 107],
        response_component='1',
        response_elements=[12],
    )

    assert postprocessor.response_nodes == (36, 107)
    assert postprocessor.response_component == 1
    assert postprocessor.response_elements == (12,)

    with pytest.raises(ValueError):
        ansys_dpf_node_response_postprocessor(response_nodes=[])


def test_config_runner_dispatches_ansys_dpf_nodes_mode() -> None:
    from pyansys_bridge.core.postprocessor import AnsysDpfNodeResponsePostprocessor
    from pyansys_bridge.optimization.config_runner import _postprocessor

    postprocessor = _postprocessor({
        'mode': 'ansys-dpf-nodes',
        'response_nodes': [10, 20],
        'response_component': 2,
        'response_elements': [30],
    })

    assert isinstance(postprocessor, AnsysDpfNodeResponsePostprocessor)
    assert postprocessor.response_nodes == (10, 20)
    assert postprocessor.response_component == 2
    assert postprocessor.response_elements == (30,)
