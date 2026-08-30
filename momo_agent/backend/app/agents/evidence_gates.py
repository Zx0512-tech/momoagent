from __future__ import annotations

from pathlib import PurePath
from typing import Any


def real_preflight_passed(report: dict[str, Any]) -> bool:
    """检查完整优化模板是否为已登记的真实运行配置。"""
    baseline = report.get('baseline') or {}
    optimization = report.get('optimization') or {}
    checks = [
        check
        for section in (baseline, optimization)
        for check in (section.get('path_checks') or {}).values()
    ]
    return (
        report.get('kind') == 'baseline_optimization_workflow'
        and baseline.get('solver') == 'ansys'
        and optimization.get('solver') == 'ansys'
        and baseline.get('execution_mode') == 'run'
        and optimization.get('execution_mode') == 'run'
        and bool(checks)
        and all(check.get('exists') is True for check in checks)
    )


def solver_version_profile_passed(
    profile: dict[str, Any],
    *,
    require_user300: bool,
) -> bool:
    """检查求解器版本和响应契约证据。"""
    solver = profile.get('solver') or {}
    response_contract = profile.get('responseContract') or {}
    solver_version = str(solver.get('version') or '').strip().upper()
    base_passed = (
        profile.get('schemaVersion') == '1.0'
        and bool(solver.get('name'))
        and solver_version not in {'', 'UNKNOWN', 'NOT_INSTALLED'}
        and bool(response_contract.get('id'))
    )
    if not require_user300:
        return base_passed
    user_element = profile.get('userElement') or {}
    return (
        base_passed
        and user_element.get('name') == 'USER300'
        and user_element.get('calibrationHashVerified') is True
    )


def engineering_preflight_passed(report: dict[str, Any], solver: str) -> bool:
    """检查工程分析/优化模板是否与请求求解器匹配。"""
    expected_solver = 'ansys' if solver == 'ANSYS' else 'openseespy_inproc'
    baseline = report.get('baseline') or {}
    optimization = report.get('optimization') or {}
    checks = [
        check
        for section in (baseline, optimization)
        for check in (section.get('path_checks') or {}).values()
    ]
    return (
        report.get('kind') == 'baseline_optimization_workflow'
        and baseline.get('solver') == expected_solver
        and optimization.get('solver') == expected_solver
        and baseline.get('execution_mode') == 'run'
        and optimization.get('execution_mode') == 'run'
        and bool(checks)
        and all(check.get('exists') is True for check in checks)
    )


def input_provenance_passed(provenance: Any) -> bool:
    """检查输入字段是否都有唯一且受控的来源。"""
    if not isinstance(provenance, list) or not provenance:
        return False
    allowed_sources = {
        'USER_DECISION',
        'VERIFIED_TEMPLATE',
        'FILE_DERIVED',
        'BUNDLED_PROJECT_DATA',
        'OPERATIONAL_DEFAULT',
    }
    fields = [item.get('field') for item in provenance if isinstance(item, dict)]
    return (
        len(fields) == len(provenance)
        and len(fields) == len(set(fields))
        and {'solver', 'workflowConfigPath'} <= set(fields)
        and any(field in {'loadDataset', 'loadCase'} for field in fields)
        and all(item.get('source') in allowed_sources for item in provenance)
    )


def _artifact_preview(store: Any, artifact_id: str) -> Any:
    return store.get_artifact(artifact_id).preview


def artifacts_have_no_non_real_markers(artifacts: list[dict[str, Any]], store: Any) -> bool:
    """拒绝 Artifact 预览中的 dry-run、preview 或未验证标记。"""
    def contains_non_real_marker(value: Any) -> bool:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized_key = str(key).replace('-', '_').lower()
                if normalized_key == 'dry_run' and child is True:
                    return True
                if normalized_key in {'is_verified_solver_output', 'isverifiedsolveroutput'} and child is False:
                    return True
                if normalized_key in {'mode', 'status'} and str(child).lower() in {
                    'dry-run', 'dry_run', 'preview', 'placeholder',
                }:
                    return True
                if contains_non_real_marker(child):
                    return True
        if isinstance(value, list):
            return any(contains_non_real_marker(child) for child in value)
        return False

    try:
        previews = [_artifact_preview(store, artifact['artifactId']) for artifact in artifacts]
    except (KeyError, AttributeError, TypeError):
        return False
    return all(not contains_non_real_marker(preview) for preview in previews)


def output_manifest_passed(
    result: dict[str, Any],
    artifacts: list[dict[str, Any]],
    store: Any,
) -> bool:
    """检查真实输出 manifest 的绑定关系和路径安全性。"""
    artifact = next(
        (item for item in artifacts if item.get('name') == 'real_output_manifest.json'),
        None,
    )
    if not artifact:
        return False
    if (
        result.get('outputManifestArtifactId') != artifact.get('artifactId')
        or result.get('outputManifestSha256') != artifact.get('sha256')
    ):
        return False
    try:
        manifest = _artifact_preview(store, artifact['artifactId'])
    except (KeyError, AttributeError, TypeError):
        return False
    if not isinstance(manifest, dict) or manifest.get('schemaVersion') != '1.0':
        return False
    files = manifest.get('files')
    if not isinstance(files, list) or manifest.get('fileCount') != len(files):
        return False
    for item in files:
        if not isinstance(item, dict):
            return False
        path = PurePath(str(item.get('path') or ''))
        if not str(path) or path.is_absolute() or '..' in path.parts:
            return False
        if not isinstance(item.get('sizeBytes'), int) or item['sizeBytes'] < 0:
            return False
        if len(str(item.get('sha256') or '')) != 64:
            return False
    return True


def result_catalog_passed(
    result: dict[str, Any],
    artifacts: list[dict[str, Any]],
    store: Any,
) -> bool:
    """校验真实结果目录已登记且每个来源 CSV 可审计。"""

    artifact_id = result.get('resultCatalogArtifactId')
    if not artifact_id:
        return False
    artifact = next((item for item in artifacts if item.get('artifactId') == artifact_id), None)
    if not artifact or artifact.get('name') != 'result_catalog.json' or len(str(artifact.get('sha256') or '')) != 64:
        return False
    try:
        catalog = _artifact_preview(store, artifact_id)
    except (KeyError, AttributeError, TypeError):
        return False
    if not isinstance(catalog, dict) or catalog.get('schemaVersion') != '1.0' or catalog.get('verified') is not True:
        return False
    entries = catalog.get('entries')
    if not isinstance(entries, list) or catalog.get('entryCount') != len(entries):
        return False
    return all(
        isinstance(entry, dict)
        and isinstance(entry.get('columns'), list)
        and bool(entry.get('columns'))
        and isinstance(entry.get('units'), dict)
        and len(str(entry.get('sha256') or '')) == 64
        and entry.get('verified') is True
        for entry in entries
    )
