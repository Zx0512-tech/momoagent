from __future__ import annotations

import json
import shutil
import subprocess
from hashlib import sha256
from types import SimpleNamespace

import pytest

from app.agents.engineering import ReviewOutcome
from app.services.agent_llm import NarrativeResult
from app.services.agent_service import AgentService


class _Job:
    status = 'SUCCEEDED'
    progress = None  # 观测信号，不影响测试的生命周期假设

    def model_dump(self, **_kwargs):
        return {
            'jobId': 'job_runtime_safety',
            'status': 'SUCCEEDED',
            'result': {},
            'artifacts': [],
        }


class _ArtifactStore:
    def __init__(self, *, register_error: Exception | None = None) -> None:
        self.register_error = register_error
        self.job_reads = 0
        self.record = None

    def refresh(self) -> None:
        return None

    def get_job(self, _job_id: str):
        self.job_reads += 1
        return _Job()

    def register_artifact(self, **kwargs):
        if self.register_error is not None:
            raise self.register_error
        content = bytes(kwargs['content'])
        artifact = SimpleNamespace(
            artifact_id='art_runtime_report',
            sha256=sha256(content).hexdigest(),
            run_id=kwargs.get('run_id'),
        )
        self.record = SimpleNamespace(
            artifact=artifact,
            preview=kwargs['preview'],
            content=content,
        )
        return artifact

    def get_artifact(self, _artifact_id: str):
        assert self.record is not None
        return self.record


class _Repository:
    def __init__(self, run: dict) -> None:
        self.run = dict(run)
        self.saved: list[dict] = []

    def get_run(self, _run_id: str):
        return dict(self.run)

    def save_run(self, run: dict) -> None:
        self.run = dict(run)
        self.saved.append(dict(run))

    def list_steps(self, _run_id: str):
        return []

    def list_tool_calls(self, _run_id: str):
        return []

    def get_approval(self, _approval_id: str):
        return None


class _Agent:
    def __init__(self, *, failure: str | None = None) -> None:
        self.failure = failure
        self.review_calls = 0
        self.build_report_calls = 0

    def review(self, _payload, *, workflow_contract):
        self.review_calls += 1
        if self.failure == 'review':
            raise RuntimeError('C:\\private\\review-input.txt')
        return ReviewOutcome(
            accepted=True,
            run_status='SUCCEEDED',
            evidence_mode='REAL_FEM',
            checks={'jobStatus': True},
            message='ok',
        )

    def build_report(self, _run, _payload, _outcome):
        self.build_report_calls += 1
        if self.failure == 'build':
            raise RuntimeError('C:\\private\\build-output.txt')
        return {
            'finite': 1.25,
            'nested': {
                'nan': float('nan'),
                'positiveInfinity': float('inf'),
                'negativeInfinity': float('-inf'),
            },
            'tupleValues': (float('nan'), 4.5),
            'items': [True, 'stable', 2],
        }

    def narrative_facts(self, report, payload):
        return {'reportKeys': sorted(report), 'jobStatus': payload.get('status')}


def _run() -> dict:
    return {
        'runId': 'run_runtime_safety',
        'jobId': 'job_runtime_safety',
        'taskType': 'DAMPER_OPTIMIZATION',
        'status': 'WAITING_JOB',
        'currentStage': 'WAITING_JOB',
        'completedSteps': ['EXECUTION'],
        'artifactIds': ['art_solver_output'],
    }


def _wire_service(monkeypatch, repository, store, agent):
    import app.services.agent_service as agent_service_module

    service = AgentService()
    monkeypatch.setattr(service, 'repository', lambda: repository)
    monkeypatch.setattr(service, '_agent_for', lambda _task_type: agent)
    monkeypatch.setattr(
        service,
        '_safe_narrate_result',
        lambda **_kwargs: NarrativeResult(
            narrativeMode='TEMPLATE_FALLBACK',
            text='ok',
            fallbackReason='TEST',
        ),
    )
    monkeypatch.setattr(agent_service_module, 'platform_store', store)
    return service


def _reject_non_standard_json(value: str):
    raise AssertionError(f'非标准 JSON 常量未被清洗: {value}')


def test_refresh_report_replaces_non_finite_values_before_preview_and_content(monkeypatch) -> None:
    repository = _Repository(_run())
    store = _ArtifactStore()
    agent = _Agent()
    service = _wire_service(monkeypatch, repository, store, agent)

    refreshed = service._refresh_agent_run(repository, _run())

    assert refreshed['status'] == 'SUCCEEDED'
    assert store.record is not None
    parsed = json.loads(
        store.record.content.decode('utf-8'),
        parse_constant=_reject_non_standard_json,
    )
    node = shutil.which('node')
    assert node is not None
    browser_parser = subprocess.run(
        [node, '-e', 'JSON.parse(process.argv[1])', store.record.content.decode('utf-8')],
        capture_output=True,
        text=True,
        check=False,
    )
    assert browser_parser.returncode == 0, browser_parser.stderr
    assert parsed == store.record.preview
    assert store.record.artifact.sha256 == sha256(store.record.content).hexdigest()
    assert parsed['finite'] == 1.25
    assert parsed['nested'] == {
        'nan': None,
        'positiveInfinity': None,
        'negativeInfinity': None,
    }
    assert parsed['tupleValues'] == [None, 4.5]
    assert parsed['items'] == [True, 'stable', 2]


@pytest.mark.parametrize('failure', ['review', 'build', 'register'])
def test_refresh_failure_is_persisted_and_not_replayed(monkeypatch, failure: str) -> None:
    repository = _Repository(_run())
    store = _ArtifactStore(
        register_error=RuntimeError('C:\\private\\artifact-store.sqlite3')
        if failure == 'register'
        else None,
    )
    agent = _Agent(failure=failure)
    service = _wire_service(monkeypatch, repository, store, agent)

    first = service.get_run('run_runtime_safety')
    second = service.get_run('run_runtime_safety')

    for result in (first, second):
        assert result['status'] == 'FAILED'
        assert result['currentStage'] == 'FAILED'
        assert result['currentStep'] == 'FAILED'
        assert result['completedSteps'] == ['EXECUTION']
        assert result['workflowGateError'] == {
            'code': 'REPORT_GENERATION_ERROR',
            'message': '结果验收或报告生成失败，运行已安全终止。',
            'details': {
                'stage': 'REPORT_GENERATION',
                'exceptionType': 'RuntimeError',
            },
        }
    # refresh 失败后幂等，不重播昂贵的验收 / 报告生成计算。
    # job_reads = 第一次 refresh 里的一次读 + 两次 _decorate_run 各添加一次进度观测。
    assert store.job_reads == 3
    assert agent.review_calls == 1
    assert agent.build_report_calls == (0 if failure == 'review' else 1)
    assert not any(saved.get('reportArtifactId') for saved in repository.saved)
    assert 'private' not in json.dumps(second, ensure_ascii=False)
