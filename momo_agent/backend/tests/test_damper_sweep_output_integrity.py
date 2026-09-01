from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.agents.damper_parameter_sweep import DamperParameterSweepAgent
from pyansys_bridge.batch import ResultStore
from pyansys_bridge.batch.result_store import _temp_path_for
from pyansys_bridge.models import AnalysisResult


def _agent() -> DamperParameterSweepAgent:
    return DamperParameterSweepAgent(
        planner=SimpleNamespace(),
        store=SimpleNamespace(),
        dispatcher=SimpleNamespace(),
    )


def test_result_store_temp_path_fits_a_deep_windows_result_directory() -> None:
    summary_path = Path("D:/") / ("x" * 217) / "summary.json"
    temp_path = _temp_path_for(summary_path)

    assert temp_path.parent == summary_path.parent
    assert len(str(temp_path)) < 260


def test_result_store_writes_summary_and_index_in_a_deep_directory(tmp_path: Path) -> None:
    root = tmp_path / ("x" * max(1, 220 - len(str(tmp_path))))
    result = AnalysisResult(
        case_id="case-1234567890",
        solver="mock",
        status="completed",
        objectives={"max_displacement": 0.1},
        timeseries={},
        metadata={},
    )

    summary_path = ResultStore(root).save(result)

    assert summary_path.is_file()
    assert (root / "index.csv").is_file()


def test_failed_sweep_with_no_registered_results_fails_all_evidence_checks() -> None:
    outcome = _agent().review(
        {
            "jobId": "failed-sweep",
            "status": "FAILED",
            "result": None,
            "artifacts": [],
        },
        workflow_contract={"cases": [{"caseId": "case-1"}]},
    )

    assert outcome.accepted is False
    assert all(passed is False for passed in outcome.checks.values())


# ---------------------------------------------------------------------------
# 作业状态门：未完成的 Job 不得进入证据装配
#
# comparison（damper_comparison.py）和 optimization（damper_optimization.py）
# 的 review() 开头都有这道门，批量此前没有：一个仍在跑的 Job 只要结果投影
# 看起来完整，就会被算成通过。
# ---------------------------------------------------------------------------

def _looks_complete_job(status: str) -> dict:
    """构造一个"结果看起来完整"但作业状态未成功的 Job。

    这些字段足以让逐项证据检查产生 True，因此如果没有状态门，RUNNING 的
    作业也会被接受 —— 这正是本测试要挡住的行为。
    """
    return {
        "jobId": "sweep-in-flight",
        "status": status,
        "result": {
            "mode": "real_damper_parameter_sweep",
            "caseResults": [
                {"caseId": "case-1", "status": "completed", "isVerifiedSolverOutput": True},
            ],
        },
        "artifacts": [{"artifactId": "art-1", "name": "x.json", "kind": "JSON_SUMMARY", "sha256": "a" * 64}],
    }


def test_running_sweep_job_is_not_assembled_into_evidence() -> None:
    outcome = _agent().review(
        _looks_complete_job("RUNNING"),
        workflow_contract={"cases": [{"caseId": "case-1"}]},
    )

    assert outcome.accepted is False
    assert outcome.checks == {"jobStatus": False}
    assert outcome.run_status == "RUNNING"
    assert outcome.evidence_mode == "RUNNING"


def test_sweep_report_carries_planner_mode_artifacts_and_limitations() -> None:
    """批量的报告此前缺 plannerMode / artifacts / limitations，两个同门都有。"""
    agent = _agent()
    job = {
        "jobId": "sweep-1",
        "status": "SUCCEEDED",
        "result": {"mode": "real_damper_parameter_sweep", "caseResults": []},
        "artifacts": [
            {"artifactId": "art-1", "name": "result_catalog.json", "kind": "JSON_SUMMARY", "sha256": "b" * 64},
        ],
    }
    outcome = agent.review(job, workflow_contract={"cases": [{"caseId": "case-1"}]})
    report = agent.build_report(
        {"runId": "run-1", "goal": "批量扫参", "plannerMode": "LLM_TOOL_CALL"},
        job,
        outcome,
    )

    assert report["plannerMode"] == "LLM_TOOL_CALL"
    assert report["artifacts"] == [
        {"artifactId": "art-1", "name": "result_catalog.json", "kind": "JSON_SUMMARY", "sha256": "b" * 64},
    ]
    # 批量不跑无控基线、不引入优化约束，限制声明必须如实说明这一点。
    assert "无控基线" in report["limitations"]

    facts = agent.narrative_facts(report, job)
    assert facts["limitations"] == report["limitations"]
    assert facts["totalCheckCount"] == len(outcome.checks)
    assert facts["passedCheckCount"] == sum(1 for passed in outcome.checks.values() if passed)
    assert facts["failedChecks"] == sorted(
        name for name, passed in outcome.checks.items() if not passed
    )
