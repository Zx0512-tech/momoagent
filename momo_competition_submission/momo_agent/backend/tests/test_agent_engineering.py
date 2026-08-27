from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.agent_engineering import (
    DAMPER_TYPES,
    DamperParameterSweepCase,
    DamperParameterSweepIntent,
    EngineeringIntent,
    STBRIDGE_LAYOUTS,
    build_engineering_contract,
    build_damper_comparison_contract,
    build_parameter_sweep_contract,
    design_equal_peak_force_cases,
)
from app.services.agent_task_handlers import orchestration_handler


def test_parameter_sweep_accepts_all_three_registered_damper_models() -> None:
    sweep = DamperParameterSweepIntent.model_validate({
        'taskType': 'DAMPER_PARAMETER_SWEEP',
        'solver': 'OPENSEESPY_INPROC',
        'selectedLayoutId': 'TWO_PER_TOWER',
        'cases': [
            {'caseId': 'viscous', 'damperType': 'VISCOUS', 'parameters': {'c': 7600.0, 'alpha': 0.8, 'vfloor': 1e-6}},
            {'caseId': 'friction', 'damperType': 'FRICTION', 'parameters': {'fc': 300000.0, 'vs': 0.001}},
            {'caseId': 'eddy', 'damperType': 'EDDY_CURRENT', 'parameters': {'fmax': 4000000.0, 'vcr': 0.2}},
        ],
        'responseIds': ['max_girder_end_displacement'],
        'maxConcurrentCases': 4,
        'requiresRealFem': True,
        'summary': '批量计算三种阻尼器',
    })

    assert [case.damper_type for case in sweep.cases] == ['VISCOUS', 'FRICTION', 'EDDY_CURRENT']


def test_parameter_sweep_defaults_to_four_concurrent_cases() -> None:
    cases = [
        {
            'caseId': f'viscous_{index}',
            'damperType': 'VISCOUS',
            'parameters': {'c': 7600.0 + index, 'alpha': 0.8, 'vfloor': 1e-6},
        }
        for index in range(4)
    ]
    intent = DamperParameterSweepIntent.model_validate({
        'taskType': 'DAMPER_PARAMETER_SWEEP',
        'solver': 'OPENSEESPY_INPROC',
        'cases': cases,
        'responseIds': ['max_girder_end_displacement'],
        'requiresRealFem': True,
        'summary': '使用默认并发数批量计算',
    })

    contract = build_parameter_sweep_contract(
        solver=intent.solver,
        cases=cases,
        response_ids=intent.response_ids,
    )

    assert intent.max_concurrent_cases == 4
    assert contract['budget']['maxConcurrentCases'] == 4


def test_parameter_sweep_rejects_mixed_or_incomplete_damper_parameters() -> None:
    with pytest.raises(ValueError, match='must exactly match'):
        DamperParameterSweepCase(
            caseId='bad', damperType='FRICTION', parameters={'c': 7600.0},
        )


def test_registered_stbridge_layouts_are_fixed_and_symmetric() -> None:
    assert set(STBRIDGE_LAYOUTS) == {'ONE_PER_TOWER', 'TWO_PER_TOWER'}
    assert STBRIDGE_LAYOUTS['ONE_PER_TOWER']['nodePairs'] == [[36, 518], [107, 521]]
    assert STBRIDGE_LAYOUTS['TWO_PER_TOWER']['nodePairs'] == [
        [36, 517], [36, 518], [107, 520], [107, 521]
    ]
    assert all(layout['direction'] == 'X' for layout in STBRIDGE_LAYOUTS.values())


@pytest.mark.parametrize('damper_type', ['VISCOUS', 'FRICTION', 'EDDY_CURRENT'])
def test_engineering_contract_uses_type_specific_parameters(damper_type: str) -> None:
    contract = build_engineering_contract(
        task_type='DAMPER_OPTIMIZATION',
        solver='ANSYS',
        damper_type=damper_type,
        response_ids=['max_girder_end_displacement'],
    )

    assert contract['damper']['type'] == damper_type
    assert contract['damper']['parameterNames'] == DAMPER_TYPES[damper_type]['parameterNames']
    assert contract['budget'] == {
        'doeDesignCount': 15,
        'surrogateCv': 10,
        'maxActiveLearningIterations': 2,
        'candidateCount': 728,
        'maxReviewIterations': 1,
        'maxFemRelativeError': 0.05,
    }


@pytest.mark.parametrize(
    'field_overrides',
    [
        {'responseIds': ['塔底剪力']},
        {'requiresRealFem': 'false'},
    ],
)
def test_engineering_intent_rejects_values_that_require_silent_normalization(
    field_overrides: dict,
) -> None:
    with pytest.raises(ValidationError):
        EngineeringIntent.model_validate({
            'taskType': 'ANALYSIS',
            'solver': 'ANSYS',
            'damperType': None,
            'loadKind': 'EARTHQUAKE',
            'responseIds': ['max_tower_base_moment'],
            'budgetProfile': 'STANDARD',
            'requiresRealFem': True,
            'missingFields': [],
            'summary': '提取响应',
            **field_overrides,
        })


def test_analysis_contract_uses_single_solve_budget_without_damper_layout() -> None:
    contract = build_engineering_contract(
        task_type='ANALYSIS',
        solver='OPENSEESPY_INPROC',
        damper_type=None,
        response_ids=['max_tower_base_shear'],
        selected_layout_id=None,
    )

    assert contract['damper'] is None
    assert contract['selectedLayoutId'] is None
    assert contract['selectedLayout'] is None
    assert contract['budget'] == {'realSolveCount': 1, 'executionTimeoutS': 7200}
    assert contract['executionEstimate'] == {'mode': 'SINGLE_ANALYSIS', 'realSolveCount': 1}


def test_equal_peak_force_design_is_deterministic_for_all_damper_types() -> None:
    cases = design_equal_peak_force_cases(['VISCOUS', 'EDDY_CURRENT', 'FRICTION'])

    assert cases[0]['parameters'] == {'c': pytest.approx(6482626.386771), 'alpha': 0.3, 'vfloor': 1e-6}
    assert cases[1]['parameters'] == {'fmax': 4_000_000.0, 'vcr': 0.2}
    assert cases[2]['parameters'] == {'fc': 4_000_000.0, 'vs': 0.2}
    assert all(case['theoreticalPeakForceN'] == 4_000_000.0 for case in cases)
    assert all(case['parameterSource'] == 'VERIFIED_TEMPLATE' for case in cases)


def test_comparison_contract_freezes_two_cases_and_user300_modules() -> None:
    contract = build_damper_comparison_contract(
        solver='ANSYS',
        damper_types=['VISCOUS', 'EDDY_CURRENT'],
        response_ids=['max_damper_force', 'max_tower_base_shear'],
    )

    assert contract['taskType'] == 'DAMPER_COMPARISON'
    assert contract['comparisonBasis'] == 'EQUAL_PEAK_FORCE'
    assert contract['forceCapScope'] == 'PER_PHYSICAL_DAMPER'
    assert [case['damperType'] for case in contract['cases']] == ['VISCOUS', 'EDDY_CURRENT']
    assert [case['solverModule'] for case in contract['cases']] == [
        'damper_user300_viscous',
        'damper_eddy_current',
    ]
    assert contract['selectedLayoutId'] == 'TWO_PER_TOWER'
    assert contract['budget'] == {'caseCount': 2, 'maxConcurrentCases': 1, 'executionTimeoutS': 7200}


def test_comparison_contract_accepts_all_three_registered_damper_models() -> None:
    contract = build_damper_comparison_contract(
        solver='OPENSEESPY_INPROC',
        damper_types=['VISCOUS', 'FRICTION', 'EDDY_CURRENT'],
        response_ids=['max_acceleration'],
    )
    assert contract['budget']['caseCount'] == 3
    assert contract['budget']['maxConcurrentCases'] == 1
    assert {case['damperType'] for case in contract['cases']} == {
        'VISCOUS', 'FRICTION', 'EDDY_CURRENT'
    }


def test_comparison_handler_estimate_tracks_frozen_case_count() -> None:
    contract = build_damper_comparison_contract(
        solver='OPENSEESPY_INPROC',
        damper_types=['VISCOUS', 'FRICTION', 'EDDY_CURRENT'],
        response_ids=['max_acceleration'],
    )
    handler = orchestration_handler('DAMPER_COMPARISON')

    assert handler is not None
    assert handler.estimated_solves(
        budget=contract['budget'],
        contract=contract,
        cases=contract['cases'],
    ) == (3, 3)


def test_parameter_sweep_contract_has_no_optimization_budget() -> None:
    contract = build_parameter_sweep_contract(
        solver='OPENSEESPY_INPROC',
        max_concurrent_cases=3,
        cases=[
            {
                'caseId': 'v1',
                'damperType': 'VISCOUS',
                'parameters': {'c': 1000.0, 'alpha': 0.3, 'vfloor': 1.0e-6},
            },
            {
                'caseId': 'f1',
                'damperType': 'FRICTION',
                'parameters': {'fc': 1000.0, 'vs': 0.2},
            },
            {
                'caseId': 'e1',
                'damperType': 'EDDY_CURRENT',
                'parameters': {'fmax': 1000.0, 'vcr': 0.2},
            },
        ],
        response_ids=['max_acceleration'],
    )
    assert contract['executionEstimate']['realSolveCount'] == 3
    assert 'doeDesignCount' not in contract['budget']


def test_parameter_sweep_contract_preserves_requested_parallelism() -> None:
    contract = build_parameter_sweep_contract(
        solver='ANSYS',
        max_concurrent_cases=4,
        cases=[
            {
                'caseId': 'v1',
                'damperType': 'VISCOUS',
                'parameters': {'c': 1000.0, 'alpha': 0.3, 'vfloor': 1.0e-6},
            },
            {
                'caseId': 'v2',
                'damperType': 'VISCOUS',
                'parameters': {'c': 2000.0, 'alpha': 0.3, 'vfloor': 1.0e-6},
            },
        ],
        response_ids=['max_acceleration'],
    )
    assert contract['budget']['maxConcurrentCases'] == 2
