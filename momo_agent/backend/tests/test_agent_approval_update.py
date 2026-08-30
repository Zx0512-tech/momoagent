from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.agents.core import AgentState, AgentStateMachine
from app.api.v1.agent_schemas import ApprovalUpdateRequest
from app.services.agent_engineering import (
    STBRIDGE_LAYOUTS,
    DamperParameterSweepCase,
    build_damper_comparison_contract,
    build_engineering_contract,
    build_parameter_sweep_contract,
)
from app.services.agent_service import AgentService
from app.services.agent_task_handlers import orchestration_handler


class _Repository:
    def __init__(self, run: dict, approval: dict) -> None:
        self.run = run
        self.approval = approval
        self.saved_approvals: list[dict] = []

    def get_run(self, _run_id: str):
        return self.run

    def get_approval(self, approval_id: str):
        return self.approval if approval_id == self.approval['approvalId'] else next(
            (item for item in self.saved_approvals if item.get('approvalId') == approval_id),
            None,
        )

    def save_approval(self, approval: dict) -> None:
        if approval['approvalId'] == self.approval['approvalId']:
            self.approval = dict(approval)
        else:
            self.saved_approvals.append(dict(approval))

    def save_run(self, run: dict) -> None:
        self.run = run

    def list_steps(self, _run_id: str):
        return []


@pytest.mark.parametrize(
    'payload',
    [
        {'budegt': {'doeDesignCount': 15}},
        {'budget': {'doeDesginCount': 15}},
    ],
)
def test_approval_update_rejects_unknown_top_level_and_nested_fields(payload: dict) -> None:
    with pytest.raises(ValidationError) as excinfo:
        ApprovalUpdateRequest.model_validate(payload)

    assert any(error['type'] == 'extra_forbidden' for error in excinfo.value.errors())


def test_waiting_approval_can_transition_to_failed() -> None:
    assert AgentStateMachine().transition(
        AgentState.WAITING_APPROVAL,
        AgentState.FAILED,
    ) == AgentState.FAILED


def test_update_approval_rebuilds_contract_and_supersedes_old_hash(monkeypatch) -> None:
    service = AgentService()
    contract = build_engineering_contract(
        task_type='DAMPER_OPTIMIZATION',
        solver='ANSYS',
        damper_type='VISCOUS',
        response_ids=['max_tower_base_shear'],
    )
    run = {
        'runId': 'agr_update',
        'taskType': 'DAMPER_OPTIMIZATION',
        'status': 'WAITING_APPROVAL',
        'pendingApprovalId': 'approval_old',
        'workflowContract': contract,
        'intent': {'solver': 'ANSYS', 'damperType': 'VISCOUS', 'responseIds': ['max_tower_base_shear']},
        'artifactIds': [],
    }
    frozen = {
        'solver': 'ANSYS',
        'runMode': 'REAL_BASELINE_OPTIMIZATION',
        'damper': {'type': 'VISCOUS'},
        'selectedLayoutId': 'TWO_PER_TOWER',
        'selectedLayout': contract['selectedLayout'],
        'responseIds': ['max_tower_base_shear'],
        'budget': {'doeDesignCount': 15},
        'loadKind': 'EARTHQUAKE',
        'loadMapping': {'loadKind': 'EARTHQUAKE', 'channels': []},
    }
    approval = {
        'approvalId': 'approval_old',
        'runId': 'agr_update',
        'status': 'PENDING',
        'action': 'RUN_ENGINEERING_WORKFLOW',
        'frozenAction': frozen,
    }
    repository = _Repository(run, approval)
    monkeypatch.setattr(service, 'repository', lambda: repository)
    monkeypatch.setattr(service, '_agent_for', lambda _task: SimpleNamespace(
        approval_action='RUN_ENGINEERING_WORKFLOW',
        task_type='DAMPER_OPTIMIZATION',
    ))

    def prepare(_repository, current_run, _agent, **_kwargs):
        assert repository.approval['status'] == 'PENDING'
        updated = {
            'solver': current_run['workflowContract']['solver'],
            'runMode': 'REAL_BASELINE_OPTIMIZATION',
            'damper': {'type': current_run['workflowContract']['damper']['type']},
            'selectedLayoutId': current_run['workflowContract']['selectedLayoutId'],
            'responseIds': current_run['workflowContract']['responseIds'],
            'budget': current_run['workflowContract']['budget'],
        }
        new = service._create_approval(
            run_id=current_run['runId'],
            action='RUN_ENGINEERING_WORKFLOW',
            frozen_action=updated,
            summary='更新后的审批说明',
        )
        current_run.update({
            'status': 'WAITING_APPROVAL',
            'pendingApprovalId': new['approvalId'],
        })

    monkeypatch.setattr(service, '_prepare_agent_approval', prepare)

    request = ApprovalUpdateRequest.model_validate({
        'damperKind': 'VISCOUS',
        'selectedLayoutId': 'ONE_PER_TOWER',
        'responseIds': ['max_girder_end_displacement'],
        'budget': {'doeDesignCount': 24},
    })
    result = service.update_approval(
        'agr_update',
        request.model_dump(exclude_none=True, by_alias=True),
    )

    assert repository.approval['status'] == 'SUPERSEDED'
    assert result['run']['pendingApprovalId'] != 'approval_old'
    assert result['run']['workflowContract']['selectedLayoutId'] == 'ONE_PER_TOWER'
    assert result['run']['workflowContract']['budget']['doeDesignCount'] == 24
    assert result['run']['workflowContract']['responseIds'] == ['max_girder_end_displacement']
    assert result['approval']['frozenActionSha256'] != service._payload_sha256(frozen)


@pytest.mark.parametrize('count', [4, 25])
def test_approval_update_rejects_initial_doe_outside_supported_range(count: int) -> None:
    with pytest.raises(ValidationError):
        ApprovalUpdateRequest.model_validate({'budget': {'doeDesignCount': count}})


def test_approval_update_does_not_coerce_doe_budget_type() -> None:
    with pytest.raises(ValidationError):
        ApprovalUpdateRequest.model_validate({'budget': {'doeDesignCount': '15'}})


@pytest.mark.parametrize('count', [5, 15, 24])
def test_approval_update_accepts_supported_initial_doe_range(count: int) -> None:
    request = ApprovalUpdateRequest.model_validate({'budget': {'doeDesignCount': count}})
    assert request.budget is not None
    assert request.budget.doe_design_count == count


# ---------------------------------------------------------------------------
# 审批前修改不得把风工况静默退回地震模板
#
# 对比与批量的契约不内联 layoutCandidates，且冻结算例带派生字段，
# 因此这三条链路各有自己的回归：荷载类型、布置校验、算例回灌。
# ---------------------------------------------------------------------------

WIND_SWEEP_CASES = [
    {'caseId': 'wind_c1000', 'damperType': 'VISCOUS', 'parameters': {'c': 1000.0, 'alpha': 0.5, 'vfloor': 1e-6}},
    {'caseId': 'wind_c2000', 'damperType': 'VISCOUS', 'parameters': {'c': 2000.0, 'alpha': 0.5, 'vfloor': 1e-6}},
]


def _wind_run_and_approval(task_type: str, contract: dict) -> tuple[dict, dict]:
    frozen = {
        'solver': contract['solver'],
        'scenario': 'WIND',
        'loadKind': 'WIND',
        'selectedLayoutId': contract['selectedLayoutId'],
        'selectedLayout': contract['selectedLayout'],
        'responseIds': contract['responseIds'],
        'budget': contract['budget'],
        'cases': contract['cases'],
        'loadMapping': {'loadKind': 'WIND', 'channels': [{'channelId': 'ch1'}]},
    }
    run = {
        'runId': 'agr_wind',
        'taskType': task_type,
        'status': 'WAITING_APPROVAL',
        'pendingApprovalId': 'approval_old',
        'workflowContract': contract,
        'intent': {'solver': contract['solver'], 'loadKind': 'WIND'},
        'artifactIds': [],
    }
    approval = {
        'approvalId': 'approval_old',
        'runId': 'agr_wind',
        'status': 'PENDING',
        'action': 'RUN_ENGINEERING_WORKFLOW',
        'frozenAction': frozen,
    }
    return run, approval


def _patch_wind_approval(monkeypatch, task_type: str, contract: dict, changes: dict) -> dict:
    service = AgentService()
    run, approval = _wind_run_and_approval(task_type, contract)
    repository = _Repository(run, approval)
    monkeypatch.setattr(service, 'repository', lambda: repository)
    monkeypatch.setattr(service, '_agent_for', lambda _task: SimpleNamespace(
        approval_action='RUN_ENGINEERING_WORKFLOW',
        task_type=task_type,
    ))

    def prepare(_repository, current_run, _agent, **_kwargs):
        new = service._create_approval(
            run_id=current_run['runId'],
            action='RUN_ENGINEERING_WORKFLOW',
            frozen_action={'scenario': current_run['workflowContract'].get('scenario')},
            summary='更新后的审批说明',
        )
        current_run.update({'status': 'WAITING_APPROVAL', 'pendingApprovalId': new['approvalId']})

    monkeypatch.setattr(service, '_prepare_agent_approval', prepare)
    request = ApprovalUpdateRequest.model_validate(changes)
    result = service.update_approval(
        'agr_wind',
        request.model_dump(exclude_none=True, by_alias=True),
    )
    return result['run']['workflowContract']


def test_wind_comparison_approval_update_keeps_wind_load_kind(monkeypatch) -> None:
    """审批前改响应量不得把已冻结的风工况退回地震模板。"""
    contract = build_damper_comparison_contract(
        solver='ANSYS',
        damper_types=['VISCOUS', 'EDDY_CURRENT'],
        response_ids=['max_girder_end_displacement'],
        load_kind='WIND',
    )
    updated = _patch_wind_approval(
        monkeypatch,
        'DAMPER_COMPARISON',
        contract,
        {'responseIds': ['max_acceleration']},
    )

    assert updated['loadKind'] == 'WIND'
    # scenario 是执行侧 _is_real_* 谓词读的键，必须与 loadKind 同步，
    # 否则契约呈现风工况、放行判定却按地震走。
    assert updated['scenario'] == 'WIND'
    assert updated['responseIds'] == ['max_acceleration']


def test_wind_sweep_approval_update_keeps_wind_load_kind(monkeypatch) -> None:
    contract = build_parameter_sweep_contract(
        solver='OPENSEESPY_INPROC',
        cases=WIND_SWEEP_CASES,
        response_ids=['max_girder_end_displacement'],
        load_kind='WIND',
    )
    updated = _patch_wind_approval(
        monkeypatch,
        'DAMPER_PARAMETER_SWEEP',
        contract,
        {'responseIds': ['max_acceleration']},
    )

    assert updated['loadKind'] == 'WIND'
    assert updated['scenario'] == 'WIND'
    # 冻结算例带 solverModule/productionReady 等派生字段；回灌时必须剥掉，
    # 否则 extra='forbid' 的意图模型会抛 ValidationError（HTTP 500）。
    assert [case['caseId'] for case in updated['cases']] == ['wind_c1000', 'wind_c2000']
    assert all(case['productionReady'] is True for case in updated['cases'])


def test_comparison_approval_update_accepts_registered_layout(monkeypatch) -> None:
    """对比契约不内联 layoutCandidates，布置校验须走受控常量表。"""
    contract = build_damper_comparison_contract(
        solver='ANSYS',
        damper_types=['VISCOUS', 'EDDY_CURRENT'],
        response_ids=['max_girder_end_displacement'],
        load_kind='WIND',
    )
    updated = _patch_wind_approval(
        monkeypatch,
        'DAMPER_COMPARISON',
        contract,
        {'selectedLayoutId': 'ONE_PER_TOWER'},
    )

    assert updated['selectedLayoutId'] == 'ONE_PER_TOWER'
    assert updated['selectedLayout']['nodePairs'] == STBRIDGE_LAYOUTS['ONE_PER_TOWER']['nodePairs']
    # 写回的布置必须是副本，不能让受控常量被后续修改污染。
    assert updated['selectedLayout'] is not STBRIDGE_LAYOUTS['ONE_PER_TOWER']


def test_comparison_approval_update_still_rejects_unknown_layout(monkeypatch) -> None:
    contract = build_damper_comparison_contract(
        solver='ANSYS',
        damper_types=['VISCOUS', 'EDDY_CURRENT'],
        response_ids=['max_girder_end_displacement'],
        load_kind='WIND',
    )
    with pytest.raises(HTTPException) as excinfo:
        _patch_wind_approval(
            monkeypatch,
            'DAMPER_COMPARISON',
            contract,
            {'selectedLayoutId': 'FOUR_PER_TOWER'},
        )

    assert excinfo.value.detail['code'] == 'INVALID_DAMPER_LAYOUT'


@pytest.mark.parametrize('task_type', ['DAMPER_COMPARISON', 'DAMPER_PARAMETER_SWEEP'])
def test_intent_planned_contract_carries_wind_load_kind(task_type: str) -> None:
    """LLM_TOOL_CALL 路径（intent_override）没有 :1016 的重新注入兜底，
    handler 建约时必须自己带上 loadKind。"""
    intent = SimpleNamespace(
        solver='ANSYS',
        damper_type='VISCOUS',
        damper_types=['VISCOUS', 'EDDY_CURRENT'],
        cases=[DamperParameterSweepCase.model_validate(case) for case in WIND_SWEEP_CASES],
        response_ids=['max_girder_end_displacement'],
        selected_layout_id='TWO_PER_TOWER',
        load_kind='WIND',
        max_concurrent_cases=2,
    )
    contract = orchestration_handler(task_type).build_contract_from_intent(intent, load_import=None)

    assert contract['loadKind'] == 'WIND'
    assert contract['scenario'] == 'WIND'
