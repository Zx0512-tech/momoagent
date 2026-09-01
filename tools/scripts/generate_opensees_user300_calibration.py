"""Sample the OpenSeesPy USER300 damper materials and write calibration evidence.

The three OpenSeesPy materials (``User300Viscous``, ``User300EddyCurrent``,
``User300Friction``) are driven through the in-process runtime and compared
against the closed-form force laws that the ANSYS USER300 element declares.
Each damper type gets a summary JSON, and the catalog consumed by
``app.services.agent_evidence.build_damper_calibration_profiles`` is rebuilt
from those summaries.

Evidence class: this establishes that the OpenSeesPy runtime materials
implement the declared force laws (runtime formula conformance). It is not a
cross-solver ANSYS/OpenSees agreement measurement, except for the viscous type
where a separate ANSYS-side comparison already exists and is referenced.

Run from the submission root:

    python tools/scripts/generate_opensees_user300_calibration.py
"""

from __future__ import annotations

import json
import math
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable


SUBMISSION_ROOT = Path(__file__).resolve().parents[2]
CALIBRATION_DIR = SUBMISSION_ROOT / 'docs/examples/templates/calibration'
CATALOG_PATH = CALIBRATION_DIR / 'opensees_user300_three_damper_calibration.json'
SOLVER_LABEL = 'OPENSEESPY_INPROC_USER300'

# 与 ANSYS 侧三份 summary 完全相同的参数，使两侧证据可直接对照。
VISCOUS_C = 1_000_000.0
VISCOUS_ALPHA = 0.3
VISCOUS_VFLOOR = 1.0e-5
EDDY_FMAX = 50_000.0
EDDY_VCR = 0.1
FRICTION_FC = 50_000.0
FRICTION_VS = 0.00002


def viscous_force(velocity: float) -> float:
    return VISCOUS_C * max(abs(velocity), VISCOUS_VFLOOR) ** (VISCOUS_ALPHA - 1.0) * velocity


def eddy_current_force(velocity: float) -> float:
    return 2.0 * EDDY_FMAX * EDDY_VCR * velocity / (velocity * velocity + EDDY_VCR * EDDY_VCR)


def friction_force(velocity: float) -> float:
    return FRICTION_FC * math.tanh(velocity / FRICTION_VS)


def sample_velocities() -> list[float]:
    """均匀网格 + 对数网格。

    摩擦阻尼器的过渡速度是 2e-5，单纯的均匀网格（间距 5e-3）只会采到饱和段，
    测不到 tanh 过渡；对数网格补上 1e-8..1e0 的量级覆盖。
    """
    uniform = [-1.0 + index * 0.005 for index in range(401)]
    logarithmic = [10.0 ** (-8.0 + index * 0.1) for index in range(81)]
    values = {0.0}
    values.update(round(value, 12) for value in uniform)
    for value in logarithmic:
        values.add(value)
        values.add(-value)
    return sorted(values)


def measure(material: str, tags: dict[str, int], target: Callable[[float], float]) -> dict[str, Any]:
    import openseespy.opensees as ops

    velocities = sample_velocities()
    max_abs_error = 0.0
    peak_target_force = 0.0
    ops.testUniaxialMaterial(tags[material])
    for velocity in velocities:
        expected = target(velocity)
        ops.setStrain(0.0, velocity)
        actual = float(ops.getStress())
        max_abs_error = max(max_abs_error, abs(actual - expected))
        peak_target_force = max(peak_target_force, abs(expected))
    return {
        'sample_count': len(velocities),
        'velocity_range_mps': [velocities[0], velocities[-1]],
        'peak_target_force_n': peak_target_force,
        'max_force_target_error_n': max_abs_error,
        'max_force_target_relative_error': max_abs_error / peak_target_force,
    }


def build_summaries() -> list[dict[str, Any]]:
    sys.path.insert(0, str(SUBMISSION_ROOT))
    from pyansys_bridge.core.openseespy_inproc_solver import _import_openseespy

    ops = _import_openseespy()
    runtime_version = str(ops.version()).strip()
    ops.wipe()
    ops.model('basicBuilder', '-ndm', 1, '-ndf', 1)
    ops.uniaxialMaterial('User300Viscous', 1, VISCOUS_C, VISCOUS_ALPHA, VISCOUS_VFLOOR)
    ops.uniaxialMaterial('User300EddyCurrent', 2, EDDY_FMAX, EDDY_VCR)
    ops.uniaxialMaterial('User300Friction', 3, FRICTION_FC, FRICTION_VS)
    tags = {'User300Viscous': 1, 'User300EddyCurrent': 2, 'User300Friction': 3}

    common = {
        'schemaVersion': '1.0',
        'solver': SOLVER_LABEL,
        'openseespy_version': runtime_version,
        'evidence_class': 'RUNTIME_FORMULA_CONFORMANCE',
        'source': 'openseespy_inproc_user300_runtime_sampling',
    }
    summaries = [
        {
            'path': CALIBRATION_DIR / 'opensees_user300_viscous_calibration_summary.json',
            'damperType': 'VISCOUS',
            'user300Type': 3,
            'body': {
                **common,
                'damper_type': 'viscous',
                'damper_type_keyopt': 3,
                'material': 'User300Viscous',
                'force_law': 'F = C*max(abs(v),VFLOOR)^(alpha-1)*v',
                'c_n_s_per_m_alpha': VISCOUS_C,
                'alpha': VISCOUS_ALPHA,
                'vfloor_mps': VISCOUS_VFLOOR,
                **measure('User300Viscous', tags, viscous_force),
                'cross_solver_reference': {
                    'artifact': 'user300_viscous_calibration_summary.json',
                    'max_force_ansys_opensees_difference_n': 0.015558350016362965,
                },
            },
        },
        {
            'path': CALIBRATION_DIR / 'opensees_user300_eddy_current_calibration_summary.json',
            'damperType': 'EDDY_CURRENT',
            'user300Type': 1,
            'body': {
                **common,
                'damper_type': 'eddy_current',
                'damper_type_keyopt': 1,
                'material': 'User300EddyCurrent',
                'force_law': 'F = 2*FMAX*VCR*v/(v*v+VCR*VCR)',
                'fmax_n': EDDY_FMAX,
                'vcr_mps': EDDY_VCR,
                **measure('User300EddyCurrent', tags, eddy_current_force),
            },
        },
        {
            'path': CALIBRATION_DIR / 'opensees_user300_friction_calibration_summary.json',
            'damperType': 'FRICTION',
            'user300Type': 2,
            'body': {
                **common,
                'damper_type': 'friction',
                'damper_type_keyopt': 2,
                'material': 'User300Friction',
                'force_law': 'F = FC*tanh(v/VS)',
                'fc_n': FRICTION_FC,
                'vs_mps': FRICTION_VS,
                **measure('User300Friction', tags, friction_force),
            },
        },
    ]
    ops.wipe()
    return summaries


def write_lf(path: Path, payload: dict[str, Any]) -> bytes:
    """按 LF 字节写出并返回写入内容。

    标定件的 SHA 按 LF 规范化登记（与既有 4 个标定件一致），因此这里绕过任何
    平台换行转换，直接写字节。
    """
    data = (json.dumps(payload, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    path.write_bytes(data)
    return data


def main() -> int:
    summaries = build_summaries()
    profiles = []
    for summary in summaries:
        data = write_lf(summary['path'], summary['body'])
        profiles.append({
            'damperType': summary['damperType'],
            'user300Type': summary['user300Type'],
            'sourcePath': summary['path'].name,
            'sourceSha256': sha256(data).hexdigest(),
            'maxTargetRelativeError': summary['body']['max_force_target_relative_error'],
        })
        print(
            f'{summary["damperType"]}: '
            f'max_abs_error={summary["body"]["max_force_target_error_n"]:.6g} N, '
            f'relative={summary["body"]["max_force_target_relative_error"]:.6g}'
        )
    write_lf(CATALOG_PATH, {
        'schemaVersion': '1.0',
        'solver': SOLVER_LABEL,
        'evidenceClass': 'RUNTIME_FORMULA_CONFORMANCE',
        'generator': 'tools/scripts/generate_opensees_user300_calibration.py',
        'profiles': profiles,
    })
    print(f'wrote {CATALOG_PATH.name}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
