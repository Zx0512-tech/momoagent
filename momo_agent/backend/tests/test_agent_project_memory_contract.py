from app.services.agent_engineering import EngineeringIntent
from app.services.agent_project_context import EngineeringProjectContextService
from app.services.agent_task_handlers import DamperOptimizationTaskHandler


def test_memory_provenance_survives_into_optimization_contract() -> None:
    intent = EngineeringIntent(
        taskType='DAMPER_OPTIMIZATION',
        solver='OPENSEESPY_INPROC',
        damperType='VISCOUS',
        loadKind='EARTHQUAKE',
        selectedLayoutId='TWO_PER_TOWER',
        responseIds=['max_tower_base_shear'],
        optimizationProfile='FULL',
        requiresRealFem=True,
        summary='memory contract',
    )
    context = {
        'workspace': {
            'solver': 'ANSYS',
            'loadKind': 'WIND',
            'damperType': 'VISCOUS',
            'selectedLayoutId': 'ONE_PER_TOWER',
            'responseIds': ['max_girder_end_displacement'],
            'optimizationProfile': 'STANDARD',
        }
    }
    resolved, sources = EngineeringProjectContextService().resolve_intent(
        intent,
        project_context=context,
        user_content='继续之前的优化',
    )

    contract = DamperOptimizationTaskHandler().build_contract_from_intent(
        resolved,
        load_import=None,
        field_sources=sources,
    )

    assert contract['solver'] == 'ANSYS'
    assert contract['loadKind'] == 'WIND'
    assert contract['selectedLayoutId'] == 'ONE_PER_TOWER'
    assert contract['responseIds'] == ['max_girder_end_displacement']
    assert contract['optimizationProfile'] == 'STANDARD'
    assert contract['fieldSources'] == {
        'solver': 'PROJECT_WORKSPACE',
        'loadKind': 'PROJECT_WORKSPACE',
        'damperType': 'PROJECT_WORKSPACE',
        'selectedLayoutId': 'PROJECT_WORKSPACE',
        'responseIds': 'PROJECT_WORKSPACE',
        'optimizationProfile': 'PROJECT_WORKSPACE',
        'budget': 'DEFAULT',
    }
