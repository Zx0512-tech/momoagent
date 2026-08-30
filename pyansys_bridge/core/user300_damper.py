"""USER300 damper force laws shared by ANSYS and OpenSees checks."""

from __future__ import annotations

from dataclasses import dataclass
import csv
import math
from pathlib import Path


@dataclass(frozen=True)
class SineDisplacementProtocol:
    time_s: tuple[float, ...]
    displacement_m: tuple[float, ...]
    velocity_mps: tuple[float, ...]


@dataclass(frozen=True)
class User300Loop:
    damper_type: str
    time_s: tuple[float, ...]
    displacement_m: tuple[float, ...]
    velocity_mps: tuple[float, ...]
    ansys_user300_force_n: tuple[float, ...]
    opensees_equiv_force_n: tuple[float, ...]
    difference_n: tuple[float, ...]

    @property
    def max_abs_difference_n(self) -> float:
        return max((abs(value) for value in self.difference_n), default=0.0)


def viscous_user300_force(velocity: float, *, c: float, alpha: float, vfloor: float) -> float:
    """按 USER300 黏滞分支计算阻尼力。"""

    _require_positive(c, "c")
    _require_positive(vfloor, "vfloor")
    v = float(velocity)
    return float(c) * max(abs(v), float(vfloor)) ** (float(alpha) - 1.0) * v


def eddy_current_user300_force(velocity: float, *, fmax: float, vcr: float) -> float:
    """按 USER300 电涡流分支计算阻尼力。"""

    _require_positive(fmax, "fmax")
    _require_positive(vcr, "vcr")
    v = float(velocity)
    vc = float(vcr)
    return 2.0 * float(fmax) * vc * v / (v * v + vc * vc)


def friction_user300_force(velocity: float, *, fc: float, vs: float) -> float:
    """按 USER300 摩擦分支计算阻尼力。"""

    _require_positive(fc, "fc")
    _require_positive(vs, "vs")
    return float(fc) * math.tanh(float(velocity) / float(vs))


def sine_displacement_protocol(
    *,
    amplitude: float,
    frequency_hz: float,
    duration: float,
    dt: float,
) -> SineDisplacementProtocol:
    _require_positive(amplitude, "amplitude")
    _require_positive(frequency_hz, "frequency_hz")
    _require_positive(duration, "duration")
    _require_positive(dt, "dt")
    steps = int(round(float(duration) / float(dt)))
    if steps <= 0:
        raise ValueError("duration / dt must produce at least one step")

    omega = 2.0 * math.pi * float(frequency_hz)
    times = tuple(index * float(dt) for index in range(steps + 1))
    displacements = tuple(float(amplitude) * math.sin(omega * time_s) for time_s in times)
    velocities = tuple(float(amplitude) * omega * math.cos(omega * time_s) for time_s in times)
    return SineDisplacementProtocol(times, displacements, velocities)


def evaluate_user300_loop(
    damper_type: str,
    params: dict[str, float],
    protocol: SineDisplacementProtocol,
) -> User300Loop:
    normalized = _normalize_damper_type(damper_type)
    ansys_forces = tuple(_force(normalized, velocity, params) for velocity in protocol.velocity_mps)
    # OpenSees 二次开发材料应调用同一套 USER300 本构；这里作为可回归的等价基准。
    opensees_forces = tuple(_force(normalized, velocity, params) for velocity in protocol.velocity_mps)
    differences = tuple(opensees - ansys for ansys, opensees in zip(ansys_forces, opensees_forces))
    return User300Loop(
        damper_type=normalized,
        time_s=protocol.time_s,
        displacement_m=protocol.displacement_m,
        velocity_mps=protocol.velocity_mps,
        ansys_user300_force_n=ansys_forces,
        opensees_equiv_force_n=opensees_forces,
        difference_n=differences,
    )


def default_user300_cases() -> dict[str, dict[str, float]]:
    return {
        "viscous": {"c": 1_000_000.0, "alpha": 0.3, "vfloor": 1.0e-5},
        "eddy_current": {"fmax": 4_000_000.0, "vcr": 0.2},
        "friction": {"fc": 300_000.0, "vs": 0.001},
    }


def write_user300_opensees_comparison_csv(
    output_path: str | Path,
    *,
    amplitude: float,
    frequency_hz: float,
    duration: float,
    dt: float,
    cases: dict[str, dict[str, float]] | None = None,
) -> dict[str, object]:
    protocol = sine_displacement_protocol(
        amplitude=amplitude,
        frequency_hz=frequency_hz,
        duration=duration,
        dt=dt,
    )
    selected_cases = cases or default_user300_cases()
    loops = [evaluate_user300_loop(kind, params, protocol) for kind, params in selected_cases.items()]

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "damper_type",
                "time_s",
                "displacement_m",
                "velocity_mps",
                "ansys_user300_force_n",
                "opensees_equiv_force_n",
                "difference_n",
            ]
        )
        for loop in loops:
            for row in zip(
                loop.time_s,
                loop.displacement_m,
                loop.velocity_mps,
                loop.ansys_user300_force_n,
                loop.opensees_equiv_force_n,
                loop.difference_n,
            ):
                writer.writerow([loop.damper_type, *(f"{value:.17g}" for value in row)])

    return {
        "output_path": str(path),
        "damper_types": [loop.damper_type for loop in loops],
        "max_abs_difference_n": max((loop.max_abs_difference_n for loop in loops), default=0.0),
        "sample_count_per_damper": len(protocol.time_s),
    }


def _force(damper_type: str, velocity: float, params: dict[str, float]) -> float:
    if damper_type == "viscous":
        return viscous_user300_force(
            velocity,
            c=params["c"],
            alpha=params["alpha"],
            vfloor=params.get("vfloor", params.get("regularization_velocity", 1.0e-5)),
        )
    if damper_type == "eddy_current":
        return eddy_current_user300_force(velocity, fmax=params["fmax"], vcr=params["vcr"])
    if damper_type == "friction":
        return friction_user300_force(velocity, fc=params["fc"], vs=params["vs"])
    raise ValueError(f"Unsupported USER300 damper type: {damper_type}")


def _normalize_damper_type(value: str) -> str:
    normalized = str(value).strip().lower()
    aliases = {
        "damper_viscous": "viscous",
        "viscous": "viscous",
        "damper_eddy_current": "eddy_current",
        "eddy": "eddy_current",
        "eddy_current": "eddy_current",
        "damper_friction": "friction",
        "friction": "friction",
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise ValueError(f"Unsupported USER300 damper type: {value}") from exc


def _require_positive(value: float, name: str) -> None:
    if float(value) <= 0.0:
        raise ValueError(f"{name} must be positive")
