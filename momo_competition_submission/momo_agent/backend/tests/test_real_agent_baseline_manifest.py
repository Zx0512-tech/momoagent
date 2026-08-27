from __future__ import annotations

import hashlib
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = REPO_ROOT / 'docs' / 'examples' / 'real_agent_baseline_manifest.json'


def template_content_sha256(path: Path) -> str:
    """按行尾归一化后的内容计算模板哈希。

    不能用 `path.read_bytes()` 的裸字节：仓库 core.autocrlf=true 且
    `.gitattributes` 未给 docs/examples/templates/*.json 钉 eol，模板在
    Windows 上检出成 CRLF、在 Linux 上检出成 LF。按裸字节登记的哈希因此
    只在"与登记时相同行尾"的机器上成立 —— 这道门会随检出平台变红变绿，
    而不是校验内容。归一化成 LF 后哈希等于 git blob 哈希（十个用例实测
    norm==blob 全部成立），门禁才真正只对内容负责。
    """
    return hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()


def test_real_agent_baseline_manifest_is_hash_complete_and_placeholder_free() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding='utf-8'))

    assert manifest['schemaVersion'] == '1.0'
    cases = manifest['cases']
    assert {case['taskType'] for case in cases} == {
        'ANALYSIS',
        'DAMPER_COMPARISON',
        'MULTI_OBJECTIVE_OPTIMIZATION',
    }
    assert len(cases) == 18
    assert {(case['solver'], case['scenario']) for case in cases if case['taskType'] == 'ANALYSIS'} == {
        ('ANSYS', 'EARTHQUAKE'),
        ('OPENSEESPY_INPROC', 'EARTHQUAKE'),
        ('ANSYS', 'WIND'),
        ('OPENSEESPY_INPROC', 'WIND'),
        ('ANSYS', 'TRAFFIC'),
        ('OPENSEESPY_INPROC', 'TRAFFIC'),
    }
    # 方案比选三个工况在两个求解器上都放行：等峰值反算 C 依赖 USER300 本构，
    # 两侧各有自己的逐型标定目录（ANSYS 是单元验收，OpenSeesPy 是运行时力法则一致性）。
    assert {
        (case['solver'], case['scenario'])
        for case in cases
        if case['taskType'] == 'DAMPER_COMPARISON'
    } == {
        ('ANSYS', 'EARTHQUAKE'),
        ('ANSYS', 'WIND'),
        ('ANSYS', 'TRAFFIC'),
        ('OPENSEESPY_INPROC', 'EARTHQUAKE'),
        ('OPENSEESPY_INPROC', 'WIND'),
        ('OPENSEESPY_INPROC', 'TRAFFIC'),
    }
    # 优化链路三个工况在两个求解器上都放行：放行面取自 platform_store 的
    # OPTIMIZATION_WORKFLOW_CONFIGS_BY_LOAD_KIND，三张表各自登记了两个求解器的
    # baseline-first 模板。车流两侧的累计位移取同一个节点（1），但取法不同：
    # ANSYS 用 cumulative_displacement_node 选节点，OpenSees 没有这个选择器，
    # 靠把 response_nodes 收窄成单节点达到同一定义。
    assert {
        (case['solver'], case['scenario'])
        for case in cases
        if case['taskType'] == 'MULTI_OBJECTIVE_OPTIMIZATION'
    } == {
        ('ANSYS', 'EARTHQUAKE'),
        ('OPENSEESPY_INPROC', 'EARTHQUAKE'),
        ('ANSYS', 'WIND'),
        ('OPENSEESPY_INPROC', 'WIND'),
        ('ANSYS', 'TRAFFIC'),
        ('OPENSEESPY_INPROC', 'TRAFFIC'),
    }

    for case in cases:
        source = REPO_ROOT / case['input']['workflowConfigPath']
        assert source.is_file(), case['caseId']
        assert template_content_sha256(source) == case['input']['templateSha256'], case['caseId']
        assert case['input']['requiresLoadArtifact'] is True
        assert case['input']['requiresLoadSha256'] is True
        assert 'real_output_manifest.json' in case['expectedArtifacts']
        assert case['goldenResponse']['finiteNumericsOnly'] is True
        serialized = json.dumps(case, ensure_ascii=False).upper()
        assert 'PENDING' not in serialized
        assert 'PLACEHOLDER' not in serialized


def test_real_agent_baseline_manifest_covers_registry_live_combinations() -> None:
    from app.services.real_execution.registry import real_execution_registry

    manifest = json.loads(MANIFEST_PATH.read_text(encoding='utf-8'))
    manifest_keys = {
        (case['taskType'], case['solver'], case['scenario'])
        for case in manifest['cases']
    }
    for capability in real_execution_registry.all():
        if capability.mode != 'CONTROLLED_AGENT' or capability.status != 'LIVE':
            continue
        if capability.job_type == 'SOLVER_BATCH' or capability.job_type == 'RESULT_INQUIRY':
            continue
        for solver in capability.solvers:
            for scenario in capability.scenarios:
                task_type = (
                    'MULTI_OBJECTIVE_OPTIMIZATION'
                    if capability.job_type in {'DAMPER_OPTIMIZATION', 'FULL_OPTIMIZATION'}
                    else capability.job_type
                )
                advertised = capability.supports(solver=solver, scenario=scenario)
                assert advertised == ((task_type, solver, scenario) in manifest_keys), (
                    capability.job_type, solver, scenario,
                )
