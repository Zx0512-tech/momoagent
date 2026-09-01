from app.services.agent_task_proposal import build_engineering_task_proposal


def test_proposal_exposes_user_workspace_and_default_sources() -> None:
    run = {
        'runId': 'agr_proposal',
        'taskType': 'DAMPER_OPTIMIZATION',
        'goal': '沿用上次地震做完整黏滞阻尼优化',
        'status': 'WAITING_APPROVAL',
        'intent': {
            'taskType': 'DAMPER_OPTIMIZATION',
            'solver': 'ANSYS',
            'damperType': 'VISCOUS',
            'loadKind': 'EARTHQUAKE',
            'selectedLayoutId': 'TWO_PER_TOWER',
            'responseIds': ['max_girder_end_displacement', 'max_tower_base_shear'],
            'optimizationProfile': 'FULL',
            'missingFields': [],
            'summary': '完整黏滞阻尼优化',
        },
        'workflowContract': {
            'model': 'STbridge',
            'solver': 'ANSYS',
            'loadKind': 'EARTHQUAKE',
            'damper': {'type': 'VISCOUS'},
            'selectedLayoutId': 'TWO_PER_TOWER',
            'responseIds': ['max_girder_end_displacement', 'max_tower_base_shear'],
            'optimizationProfile': 'FULL',
            'budget': {'doeDesignCount': 15},
            'fieldSources': {
                'solver': 'USER_SPECIFIED',
                'loadKind': 'PROJECT_WORKSPACE',
                'selectedLayoutId': 'PROJECT_WORKSPACE',
                'responseIds': 'PROJECT_WORKSPACE',
                'optimizationProfile': 'USER_SPECIFIED',
                'budget': 'DEFAULT',
            },
        },
        'preflight': {'passed': True},
        'plan': ['冻结优化计划', '审批后执行真实 FEM'],
    }

    proposal = build_engineering_task_proposal(run)
    by_field = {item['field']: item for item in proposal['fields']}

    assert proposal['proposalState'] == 'READY_FOR_CONFIRMATION'
    assert proposal['readyForApproval'] is True
    assert proposal['preflightPassed'] is True
    assert by_field['solver']['source'] == 'USER_CONFIRMED'
    assert by_field['loadKind']['source'] == 'PROJECT_WORKSPACE'
    assert by_field['loadKind']['inherited'] is True
    assert by_field['budget']['source'] == 'SYSTEM_DEFAULT'
    assert by_field['damperType']['source'] == 'USER_CONFIRMED'
    assert proposal['inheritedFields'] == ['loadKind', 'selectedLayoutId', 'responseIds']
    assert proposal['proposedActions'] == ['冻结优化计划', '审批后执行真实 FEM']
    assert any('Project Workspace' in warning for warning in proposal['warnings'])


def test_proposal_keeps_missing_fields_out_of_approval_state() -> None:
    run = {
        'runId': 'agr_clarify',
        'taskType': 'ANALYSIS',
        'goal': '分析一下',
        'status': 'NEEDS_CLARIFICATION',
        'intent': {
            'taskType': 'CLARIFICATION',
            'solver': None,
            'responseIds': [],
            'missingFields': ['solver', 'responseIds'],
            'summary': '需要补充分析配置',
        },
        'workflowContract': {},
    }

    proposal = build_engineering_task_proposal(run)

    assert proposal['proposalState'] == 'NEEDS_CLARIFICATION'
    assert proposal['readyForApproval'] is False
    assert proposal['unresolvedFields'] == ['solver', 'responseIds']
    assert proposal['approvalRequired'] is True


def test_proposal_is_stable_for_same_resolved_contract() -> None:
    run = {
        'runId': 'agr_stable',
        'taskType': 'ANALYSIS',
        'goal': '做一次分析',
        'status': 'PLANNING',
        'intent': {'taskType': 'ANALYSIS', 'solver': 'OPENSEESPY_INPROC', 'missingFields': [], 'summary': '单次分析'},
        'workflowContract': {
            'model': 'STbridge',
            'solver': 'OPENSEESPY_INPROC',
            'loadKind': 'EARTHQUAKE',
            'responseIds': ['max_girder_end_displacement'],
            'fieldSources': {'solver': 'USER_SPECIFIED', 'loadKind': 'DEFAULT', 'responseIds': 'USER_SPECIFIED'},
        },
    }

    first = build_engineering_task_proposal(run)
    second = build_engineering_task_proposal(run)
    assert first['proposalId'] == second['proposalId']
