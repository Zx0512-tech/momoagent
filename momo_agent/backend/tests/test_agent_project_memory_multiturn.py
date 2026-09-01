from app.services.agent_engineering import EngineeringIntent
from app.services.agent_project_context import EngineeringProjectContextService


def test_prior_explicit_solver_survives_clarification_workspace_conflict() -> None:
    intent = EngineeringIntent(
        taskType='DAMPER_OPTIMIZATION',
        solver='OPENSEESPY_INPROC',
        damperType='VISCOUS',
        loadKind='EARTHQUAKE',
        selectedLayoutId='TWO_PER_TOWER',
        responseIds=['max_tower_base_shear'],
        optimizationProfile='STANDARD',
        requiresRealFem=True,
        summary='继续澄清',
    )
    context = {
        'workspace': {
            'solver': 'ANSYS',
            'loadKind': 'WIND',
            'damperType': 'FRICTION',
            'selectedLayoutId': 'ONE_PER_TOWER',
            'responseIds': ['max_girder_end_displacement'],
            'optimizationProfile': 'FULL',
        }
    }

    resolved, sources = EngineeringProjectContextService().resolve_intent(
        intent,
        project_context=context,
        user_content='粘滞，地震，每塔两个，看塔底剪力',
        prior_user_content='用 OpenSees 做阻尼器优化，其他参数我再补',
    )

    assert resolved.solver == 'OPENSEESPY_INPROC'
    assert sources['solver'] == 'USER_SPECIFIED'
    assert resolved.load_kind == 'EARTHQUAKE'
    assert resolved.damper_type == 'VISCOUS'
    assert resolved.selected_layout_id == 'TWO_PER_TOWER'
    assert resolved.response_ids == ['max_tower_base_shear']
    # Profile was never explicitly stated in either turn, so Workspace may supply it.
    assert resolved.optimization_profile == 'FULL'
    assert sources['optimizationProfile'] == 'PROJECT_WORKSPACE'


def test_current_turn_explicit_solver_overrides_both_prior_turn_and_workspace() -> None:
    intent = EngineeringIntent(
        taskType='ANALYSIS',
        solver='ANSYS',
        damperType=None,
        loadKind='EARTHQUAKE',
        selectedLayoutId=None,
        responseIds=['max_tower_base_shear'],
        requiresRealFem=True,
        summary='改求解器',
    )
    context = {'workspace': {'solver': 'OPENSEESPY_INPROC', 'loadKind': 'EARTHQUAKE'}}

    resolved, sources = EngineeringProjectContextService().resolve_intent(
        intent,
        project_context=context,
        user_content='这次改用 ANSYS',
        prior_user_content='之前先用 OpenSees 看一下',
    )

    assert resolved.solver == 'ANSYS'
    assert sources['solver'] == 'USER_SPECIFIED'
