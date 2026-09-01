from __future__ import annotations

import json
import re
from hashlib import sha256
from importlib import metadata
from pathlib import Path
from typing import Any, Literal


InputSource = Literal[
    'USER_DECISION',
    'VERIFIED_TEMPLATE',
    'FILE_DERIVED',
    'BUNDLED_PROJECT_DATA',
    'OPERATIONAL_DEFAULT',
]

_SOLVER_PACKAGES = {
    'ANSYS': ('ANSYS_MAPDL', ('ansys-mapdl-core',)),
    'OPENSEESPY_INPROC': ('OPENSEESPY_INPROC', ('openseespy', 'openseespywin')),
}
_RESPONSE_CONTRACTS = {
    'ANSYS': {
        'id': 'ANSYS_BEAM4_SMISC_MMOM_R4',
        'version': 'R4',
        'description': 'BEAM4 SMISC/MMOM 梁端位移、塔底剪力、塔底弯矩与 Node107 累计位移',
    },
    'OPENSEESPY_INPROC': {
        'id': 'OPENSEESPY_SECTION_RELATIVE_R1',
        'version': 'R1',
        'description': 'OpenSeesPy 截面相对响应合同',
    },
}
_CALIBRATION_DIR = Path(__file__).resolve().parents[4] / 'docs/examples/templates/calibration'
# 每个求解器登记自己的 USER300 标定目录。把 ANSYS 目录当 OpenSeesPy 的证据用，
# 等于给 OpenSeesPy 的结果贴 ANSYS 的证据标签，因此这里按求解器分开取。
_DAMPER_CALIBRATION_CATALOGS = {
    'ANSYS': _CALIBRATION_DIR / 'user300_three_damper_calibration.json',
    'OPENSEESPY_INPROC': _CALIBRATION_DIR / 'opensees_user300_three_damper_calibration.json',
}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding='utf-8'))


def _resolve_config_path(value: str, parent: Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (parent / path).resolve()


def _installed_package(packages: tuple[str, ...]) -> tuple[str, str]:
    for package in packages:
        try:
            return package, metadata.version(package)
        except metadata.PackageNotFoundError:
            continue
    return packages[0], 'NOT_INSTALLED'


def _openseespy_runtime_version() -> str | None:
    try:
        from pyansys_bridge.core.openseespy_inproc_solver import _import_openseespy

        version = str(_import_openseespy().version()).strip()
    except (AttributeError, ImportError, OSError, RuntimeError):
        return None
    return version or None


def _ansys_release_from_executable(executable: str) -> str:
    match = re.search(r'(?:^|[/\\])v(?P<year>\d{2})(?P<release>\d)(?:[/\\]|$)', executable, re.IGNORECASE)
    if not match:
        return 'UNKNOWN'
    return f'20{match.group("year")} R{match.group("release")}'


def build_solver_version_profile(workflow_config_path: Path, *, solver: str) -> dict[str, Any]:
    normalized_solver = solver.upper()
    solver_name, packages = _SOLVER_PACKAGES[normalized_solver]
    package, package_version = _installed_package(packages)
    workflow_path = workflow_config_path.resolve()
    workflow = _load_json(workflow_path)
    optimization_path = workflow_path
    if workflow.get('optimization_config'):
        optimization_path = _resolve_config_path(str(workflow['optimization_config']), workflow_path.parent)
    optimization = _load_json(optimization_path)
    # ANSYS 模板把求解器写成字符串，参数在 solver_kwargs；OpenSeesPy 模板把 solver
    # 写成 dict，damper_module 和 damper_calibration 都嵌在里面。只读 solver_kwargs
    # 会让 OpenSeesPy 永远取不到标定，userElement 缺失、require_user300 永不通过。
    solver_kwargs = dict(optimization.get('solver_kwargs') or {})
    solver_section = optimization.get('solver')
    if isinstance(solver_section, dict):
        solver_kwargs = {**dict(solver_section), **solver_kwargs}
    executable = str(solver_kwargs.get('mapdl_executable') or '')
    if normalized_solver == 'ANSYS':
        solver_version = _ansys_release_from_executable(executable)
        version_source = 'CONFIGURED_EXECUTABLE_PATH'
    else:
        runtime_version = _openseespy_runtime_version()
        solver_version = runtime_version or package_version
        version_source = 'SOLVER_RUNTIME' if runtime_version else 'PYTHON_PACKAGE_METADATA'
        if runtime_version and package_version == 'NOT_INSTALLED':
            package = 'bundled-openseespy'
            package_version = runtime_version
    profile: dict[str, Any] = {
        'schemaVersion': '1.0',
        'solver': {
            'name': solver_name,
            'version': solver_version,
            'versionSource': version_source,
        },
        'sdk': {'package': package, 'version': package_version},
        'responseContract': dict(_RESPONSE_CONTRACTS[normalized_solver]),
    }
    calibration = dict(solver_kwargs.get('damper_calibration') or {})
    if calibration:
        artifact_path_value = str(calibration.get('artifact_path') or '')
        artifact_path = (
            _resolve_config_path(artifact_path_value, optimization_path.parent)
            if artifact_path_value
            else None
        )
        expected_sha256 = str(calibration.get('sha256') or '')
        actual_sha256 = (
            sha256(artifact_path.read_bytes()).hexdigest()
            if artifact_path and artifact_path.is_file()
            else None
        )
        profile['userElement'] = {
            'name': 'USER300',
            'module': solver_kwargs.get('damper_module'),
            'calibrationStatus': calibration.get('status'),
            'calibrationSha256': expected_sha256 or None,
            'calibrationHashVerified': bool(
                expected_sha256 and actual_sha256 and expected_sha256 == actual_sha256
            ),
        }
    return profile


def build_damper_calibration_profiles(
    damper_types: list[str],
    *,
    solver: str = 'ANSYS',
) -> list[dict[str, Any]]:
    normalized_solver = solver.upper()
    try:
        catalog_path = _DAMPER_CALIBRATION_CATALOGS[normalized_solver]
    except KeyError as exc:
        raise ValueError(f'Unsupported damper calibration solver: {solver}') from exc
    catalog = _load_json(catalog_path)
    by_type = {item['damperType']: item for item in catalog.get('profiles') or []}
    profiles = []
    for damper_type in damper_types:
        try:
            item = dict(by_type[damper_type])
        except KeyError as exc:
            raise ValueError(f'Unsupported damper calibration profile: {damper_type}') from exc
        source_path = _resolve_config_path(item['sourcePath'], catalog_path.parent)
        actual_sha256 = sha256(source_path.read_bytes()).hexdigest() if source_path.is_file() else None
        # 缺字段才算不合格；误差恰好为 0 是最好的标定结果，不能被 `or` 兜底成 1.0。
        declared_error = item.get('maxTargetRelativeError')
        verified = (
            actual_sha256 == item.get('sourceSha256')
            and declared_error is not None
            and float(declared_error) <= 0.001
        )
        profiles.append({
            'damperType': damper_type,
            'user300Type': int(item['user300Type']),
            'solver': normalized_solver,
            # 证据类别不同：ANSYS 侧是 USER300 单元验收，OpenSeesPy 侧是运行时
            # 力法则一致性采样。报告和审批卡片按这个字段区分，不做等价性宣称。
            'evidenceClass': str(catalog.get('evidenceClass') or 'USER300_ELEMENT_ACCEPTANCE'),
            'status': 'VERIFIED' if verified else 'UNVERIFIED',
            'sha256': actual_sha256,
            'sourcePath': str(source_path),
            'maxTargetRelativeError': float(item['maxTargetRelativeError']),
        })
    return profiles


def _provenance_item(field: str, source: InputSource, value: Any) -> dict[str, Any]:
    return {'field': field, 'source': source, 'value': value}


def build_agent_input_provenance(
    frozen_action: dict[str, Any],
    *,
    task_type: str,
) -> list[dict[str, Any]]:
    user_driven = task_type in {'ANALYSIS', 'DAMPER_OPTIMIZATION', 'DAMPER_COMPARISON'}
    items = [
        _provenance_item(
            'solver',
            'USER_DECISION' if user_driven else 'VERIFIED_TEMPLATE',
            frozen_action.get('solver'),
        ),
        _provenance_item(
            'workflowConfigPath',
            'VERIFIED_TEMPLATE',
            frozen_action.get('workflowConfigPath'),
        ),
    ]
    load_artifact_id = frozen_action.get('loadDatasetArtifactId')
    if load_artifact_id:
        load_source: InputSource = (
            'BUNDLED_PROJECT_DATA'
            if (frozen_action.get('loadMapping') or {}).get('source') == 'BUNDLED_PROJECT_DATA'
            else 'FILE_DERIVED'
        )
        items.append(_provenance_item('loadDataset', load_source, {
            'artifactId': load_artifact_id,
            'sha256': frozen_action.get('loadDatasetSha256'),
        }))
    else:
        items.append(_provenance_item('loadCase', 'VERIFIED_TEMPLATE', {
            'scenario': frozen_action.get('scenario') or frozen_action.get('loadKind'),
            'usesRegisteredTemplate': True,
        }))
    if frozen_action.get('loadTargetSetId'):
        items.append(_provenance_item(
            'loadTargetSetId',
            'USER_DECISION',
            frozen_action['loadTargetSetId'],
        ))
    damper = dict(frozen_action.get('damper') or {})
    if damper.get('type'):
        items.append(_provenance_item('damper.type', 'USER_DECISION', damper['type']))
    if frozen_action.get('cases'):
        items.append(_provenance_item(
            'damper.cases',
            'USER_DECISION',
            [case.get('damperType') for case in frozen_action['cases']],
        ))
        items.append(_provenance_item('damper.parameters', 'VERIFIED_TEMPLATE', [
            {
                'damperType': case.get('damperType'),
                'parameters': case.get('parameters'),
                'parameterSource': case.get('parameterSource'),
            }
            for case in frozen_action['cases']
        ]))
    if 'responseIds' in frozen_action:
        items.append(_provenance_item('responseIds', 'USER_DECISION', frozen_action.get('responseIds') or []))
    if frozen_action.get('selectedLayoutId'):
        items.append(_provenance_item(
            'selectedLayoutId',
            'VERIFIED_TEMPLATE',
            frozen_action['selectedLayoutId'],
        ))
    if 'budget' in frozen_action:
        items.append(_provenance_item('budget', 'VERIFIED_TEMPLATE', frozen_action.get('budget') or {}))
    timeout = frozen_action.get('executionTimeoutS')
    if timeout is None:
        timeout = (frozen_action.get('resources') or {}).get('executionTimeoutS')
    if timeout is not None:
        items.append(_provenance_item('executionTimeoutS', 'OPERATIONAL_DEFAULT', timeout))
    return items


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def build_output_manifest(run_dir: Path, *, allowed_root: Path) -> dict[str, Any]:
    resolved_root = allowed_root.resolve()
    resolved_run_dir = run_dir.resolve()
    if not resolved_run_dir.is_relative_to(resolved_root) or resolved_run_dir == resolved_root:
        raise ValueError('output directory is outside allowed root')
    files: list[dict[str, Any]] = []
    for path in sorted(resolved_run_dir.rglob('*'), key=lambda item: item.as_posix()):
        if not path.is_file() or path.name == 'real_output_manifest.json':
            continue
        resolved_path = path.resolve()
        if not resolved_path.is_relative_to(resolved_run_dir):
            raise ValueError('output file is outside job directory')
        files.append({
            'path': resolved_path.relative_to(resolved_run_dir).as_posix(),
            'sizeBytes': resolved_path.stat().st_size,
            'sha256': _file_sha256(resolved_path),
        })
    return {
        'schemaVersion': '1.0',
        'rootDirectory': resolved_run_dir.name,
        'fileCount': len(files),
        'totalBytes': sum(item['sizeBytes'] for item in files),
        'files': files,
    }
