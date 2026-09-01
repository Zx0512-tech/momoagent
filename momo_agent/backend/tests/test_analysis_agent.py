from __future__ import annotations

from types import SimpleNamespace

from app.agents.analysis import AnalysisAgent
from app.agents.core import RepositorySessionMemory
from app.services.agent_engineering import build_engineering_contract


def _agent() -> AnalysisAgent:
    return AnalysisAgent(
        planner=SimpleNamespace(),
        memory=RepositorySessionMemory(lambda _session_id: []),
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


def test_prepare_approval_is_pure_and_returns_frozen_analysis_action() -> None:
    agent = _agent()
    run = {
        'runId': 'run-1',
        'intent': {'loadKind': 'EARTHQUAKE'},
        'workflowContract': build_engineering_contract(
            task_type='ANALYSIS',
            solver='ANSYS',
            damper_type=None,
            response_ids=['max_tower_base_shear'],
        ),
    }

    prepared = agent.prepare_approval(
        run,
        mapping={'loadKind': 'EARTHQUAKE', 'channels': []},
        standard_artifact_id='load-1',
        standard_sha256='c' * 64,
    )

    assert prepared.passed
    assert prepared.approval_action == 'RUN_SOLVER'
    assert prepared.frozen_action['runMode'] == 'REAL_AGENT_ANALYSIS'
    assert prepared.frozen_action['loadDatasetArtifactId'] == 'load-1'
    assert 'inputProvenance' in prepared.frozen_action
    assert 'pendingApprovalId' not in run


def test_narrative_facts_exclude_hashes_and_node_pairs() -> None:
    agent = _agent()
    report = {
        'conclusion': '诊断结果',
        'checks': {'jobSucceeded': True, 'outputManifest': False},
        'workflowContract': {'solver': 'ANSYS', 'responseIds': ['max_tower_base_shear']},
        'runMode': 'real_agent_analysis',
    }
    facts = agent.narrative_facts(
        report,
        {'result': {'objectives': {'max_tower_base_shear': 1.2}, 'isVerifiedSolverOutput': True}},
    )

    text = repr(facts)
    assert 'sha256' not in text.lower()
    assert 'nodePairs' not in text
    assert facts['failedChecks'] == ['outputManifest']
