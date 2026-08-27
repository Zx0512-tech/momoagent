from __future__ import annotations

import math

import openseespy.opensees as ops


def viscous_force(velocity: float, c: float = 1_000_000.0, alpha: float = 0.3, vfloor: float = 1.0e-5) -> float:
    abs_v = abs(velocity)
    return c * max(abs_v, vfloor) ** (alpha - 1.0) * velocity


def eddy_force(velocity: float, fmax: float = 4_000_000.0, vcr: float = 0.2) -> float:
    return 2.0 * fmax * vcr * velocity / (velocity * velocity + vcr * vcr)


def friction_force(velocity: float, fc: float = 300_000.0, vs: float = 0.001) -> float:
    return fc * math.tanh(velocity / vs)


def main() -> int:
    velocity = 0.031415926535897934
    ops.wipe()
    ops.model("basicBuilder", "-ndm", 1, "-ndf", 1)
    ops.uniaxialMaterial("User300Viscous", 1, 1_000_000.0, 0.3, 1.0e-5)
    ops.uniaxialMaterial("User300EddyCurrent", 2, 4_000_000.0, 0.2)
    ops.uniaxialMaterial("User300Friction", 3, 300_000.0, 0.001)

    checks = [
        ("viscous", 1, viscous_force(velocity)),
        ("eddy_current", 2, eddy_force(velocity)),
        ("friction", 3, friction_force(velocity)),
    ]
    for name, tag, expected in checks:
        ops.testUniaxialMaterial(tag)
        ops.setStrain(0.0, velocity)
        actual = float(ops.getStress())
        diff = abs(actual - expected)
        print(f"{name}: actual={actual:.12g}, expected={expected:.12g}, diff={diff:.3g}")
        if diff > 1.0e-6:
            raise RuntimeError(f"{name} USER300 check failed")

    ops.wipe()
    print("OpenSeesPy USER300 runtime is available in this folder.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
