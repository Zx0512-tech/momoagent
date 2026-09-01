from __future__ import annotations

from app.services.agent_harness import WorkflowHarnessMixin
from app.services.agent_llm import HarnessModelTurn, HarnessToolCall
from app.services.agent_project_repository import EngineeringProjectRepository
from app.services.agent_repository import AgentRepository
from app.services.agent_service import AgentService
from app.services.platform_store import platform_store


class _Planner:
    def __init__(self) -> None:
        self.turn_context = None

    def run_harness_turn(self, **kwargs):
        self.turn_context = kwargs.get('turn_context')
        return HarnessModelTurn(
            content=None,
            toolCalls=[HarnessToolCall(
                toolCallId='call_memory_start',
                name='workflow.start',
                arguments={
                    'taskType': 'DAMPER_OPTIMIZATION',
                    'engineeringIntent': {
                        'taskType': 'DAMPER_OPTIMIZATION',
                        'solver': 'OPENSEESPY_INPROC',
                        'damperType': None,
                        'damperTypes': [],
                        'loadKind': None,
                        'selectedLayoutId': None,
                        'responseIds': [],
                        'budgetProfile': 'STANDARD',
                        'optimizationProfile': 'STANDARD',
                        'requiresRealFem': True,
                        'missingFields': [
                            'damperType', 'loadKind', 'selectedLayoutId', 'responseIds',
                        ],
                        'summary': '继续当前工程优化',
                    },
                },
            )],
        )


class _Service(AgentService):
    def __init__(self) -> None:
        self.planner = _Planner()
        self.captured = None

    def _create_engineering_run(self, repository, session, content, now, **kwargs):
        self.captured = kwargs
        return {
            'runId': 'agr_memory_harness',
            'sessionId': session['sessionId'],
            'taskType': kwargs['requested_task'],
            'status': 'PLANNING',
            'currentStage': 'PLANNING',
            'artifactIds': [],
            'createdAt': now,
            'updatedAt': now,
        }

    def _attach_workflow_runtime(self, repository, run, task_type):
        run['workflowId'] = f'memory-{task_type.lower()}'

    def _record_harness_tool_call(self, *args, **kwargs):
        return None

    def _persist_native_tool_exchange(self, *args, **kwargs):
        return None

    def _decorate_run(self, run):
        return run

    @staticmethod
    def _workflow_state_from_run(run):
        return {
            'workflowId': run.get('workflowId'),
            'currentStep': 'REQUIREMENTS',
            'completedSteps': [],
            'allowedTools': [],
            'requiredGate': 'test',
            'remainingRetries': 0,
        }


def test_workspace_memory_resolves_bootstrap_intent_before_run_creation(tmp_path, monkeypatch) -> None:
    state_path = tmp_path / 'agent.sqlite3'
    monkeypatch.setattr(platform_store, 'state_path', state_path)
    repository = AgentRepository(state_path)
    project_repository = EngineeringProjectRepository(state_path)
    session = {
        'sessionId': 'ags_memory',
        'ownerId': 'local',
        'title': 'Memory',
        'status': 'ACTIVE',
        'createdAt': '2026-09-01T00:00:00Z',
        'updatedAt': '2026-09-01T00:00:00Z',
    }
    repository.save_session(session)
    repository.add_message({
        'messageId': 'msg_memory',
        'sessionId': session['sessionId'],
        'role': 'USER',
        'content': '继续上次优化',
        'createdAt': '2026-09-01T00:00:00Z',
    })
    project_repository.create_project({
        'projectId': 'agp_memory',
        'ownerId': 'local',
        'name': 'Memory bridge',
        'description': '',
        'status': 'ACTIVE',
        'workspace': {
            'schemaVersion': 1,
            'modelArtifactId': None,
            'modelFileName': None,
            'modelSha256': None,
            'solver': 'ANSYS',
            'loadKind': 'EARTHQUAKE',
            'loadArtifactId': None,
            'loadSha256': None,
            'damperType': 'VISCOUS',
            'selectedLayoutId': 'TWO_PER_TOWER',
            'responseIds': ['max_tower_base_shear'],
            'optimizationProfile': 'FULL',
        },
        'workspaceRevision': 3,
        'sessionIds': [session['sessionId']],
        'createdAt': '2026-09-01T00:00:00Z',
        'updatedAt': '2026-09-01T00:00:00Z',
    })

    service = _Service()
    result = WorkflowHarnessMixin._dispatch_harness_message(
        service,
        repository,
        session,
        '继续上次优化',
        '2026-09-01T00:01:00Z',
        None,
        None,
        requested_task='DAMPER_OPTIMIZATION',
    )

    assert result['runId'] == 'agr_memory_harness'
    assert service.planner.turn_context is not None
    project_context = service.planner.turn_context['engineeringProjectContext']
    assert project_context['project'] == {
        'projectId': 'agp_memory',
        'name': 'Memory bridge',
        'workspaceRevision': 3,
    }

    assert service.captured is not None
    intent = service.captured['intent_override']
    assert intent.solver == 'ANSYS'
    assert intent.load_kind == 'EARTHQUAKE'
    assert intent.damper_type == 'VISCOUS'
    assert intent.selected_layout_id == 'TWO_PER_TOWER'
    assert intent.response_ids == ['max_tower_base_shear']
    assert intent.optimization_profile == 'FULL'
    assert intent.missing_fields == []
    assert service.captured['field_sources_override'] == {
        'solver': 'PROJECT_WORKSPACE',
        'loadKind': 'PROJECT_WORKSPACE',
        'damperType': 'PROJECT_WORKSPACE',
        'selectedLayoutId': 'PROJECT_WORKSPACE',
        'responseIds': 'PROJECT_WORKSPACE',
        'optimizationProfile': 'PROJECT_WORKSPACE',
        'budget': 'DEFAULT',
    }
