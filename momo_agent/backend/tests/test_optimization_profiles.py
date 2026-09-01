from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.agent_engineering import (
    OPTIMIZATION_PROFILE_POLICIES,
    SLOT_SPECS,
    STANDARD_BUDGET,
    EngineeringIntent,
    build_engineering_contract,
    resolve_optimization_profile,
)


def test_optimization_profiles_are_registered_from_one_policy_catalog() -> None:
    assert set(OPTIMIZATION_PROFILE_POLICIES) == {'STANDARD', 'FULL', 'CUSTOM'}
    assert SLOT_SPECS['optimizationProfile']['options'] == ('STANDARD', 'FULL', 'CUSTOM')
    assert SLOT_SPECS['optimizationProfile']['default'] == 'STANDARD'
    assert STANDARD_BUDGET == resolve_optimization_profile('STANDARD').budget()


def test_engineering_intent_defaults_to_standard_optimization_profile() -> None:
    intent = EngineeringIntent(
        taskType='DAMPER_OPTIMIZATION',
        solver='OPENSEESPY_INPROC',
        damperType='VISCOUS',
        loadKind='EARTHQUAKE',
        selectedLayoutId='TWO_PER_TOWER',
        responseIds=['max_girder_end_displacement'],
        requiresRealFem=True,
        missingFields=[],
        summary='优化黏滞阻尼器',
    )

    assert intent.optimization_profile == 'STANDARD'
    assert intent.model_dump(by_alias=True)['optimizationProfile'] == 'STANDARD'


def test_engineering_intent_accepts_full_profile_without_changing_task_type() -> None:
    intent = EngineeringIntent(
        taskType='DAMPER_OPTIMIZATION',
        solver='ANSYS',
        damperType='VISCOUS',
        loadKind='EARTHQUAKE',
        selectedLayoutId='TWO_PER_TOWER',
        responseIds=['max_tower_base_shear'],
        optimizationProfile='FULL',
        requiresRealFem=True,
        missingFields=[],
        summary='完整强度阻尼器优化',
    )

    assert intent.task_type == 'DAMPER_OPTIMIZATION'
    assert intent.optimization_profile == 'FULL'


def test_damper_optimization_contract_freezes_profile_and_resolved_policy() -> None:
    contract = build_engineering_contract(
        task_type='DAMPER_OPTIMIZATION',
        solver='ANSYS',
        damper_type='VISCOUS',
        response_ids=['max_girder_end_displacement'],
        selected_layout_id='TWO_PER_TOWER',
        load_kind='EARTHQUAKE',
        optimization_profile='FULL',
    )

    policy = resolve_optimization_profile('FULL')
    assert contract['taskType'] == 'DAMPER_OPTIMIZATION'
    assert contract['optimizationProfile'] == 'FULL'
    assert contract['optimizationPolicy'] == policy.model_dump(by_alias=True)
    assert contract['budget'] == policy.budget()
    assert contract['fieldSources']['optimizationProfile'] == 'DEFAULT'


def test_pr1_profiles_preserve_existing_baseline_first_budget() -> None:
    standard = resolve_optimization_profile('STANDARD')
    full = resolve_optimization_profile('FULL')

    assert standard.budget() == STANDARD_BUDGET
    assert full.budget() == STANDARD_BUDGET
    assert standard.pareto_enabled is True
    assert full.final_fem_validation is True
    assert resolve_optimization_profile('CUSTOM').customizable is True


def test_unknown_optimization_profile_fails_closed() -> None:
    with pytest.raises(ValueError, match='Unsupported optimization profile'):
        resolve_optimization_profile('MAXIMUM')

    with pytest.raises(ValidationError):
        EngineeringIntent(
            taskType='DAMPER_OPTIMIZATION',
            solver='ANSYS',
            damperType='VISCOUS',
            loadKind='EARTHQUAKE',
            selectedLayoutId='TWO_PER_TOWER',
            responseIds=['max_girder_end_displacement'],
            optimizationProfile='MAXIMUM',
            requiresRealFem=True,
            missingFields=[],
            summary='非法 Profile',
        )
