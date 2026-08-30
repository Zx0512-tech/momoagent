import math
from dataclasses import dataclass

from app.api.schemas import OverrideInput, WindRequest
from app.services.validation import clamp_basic_wind_speed, clamp_reference_height, resolve_roughness_length


@dataclass(frozen=True)
class PointNormativeResult:
    height: float
    ud: float
    iu: float
    iv: float
    iw: float
    sigma_u: float
    sigma_v: float
    sigma_w: float


@dataclass(frozen=True)
class TowerNormativeResult:
    levels: list[PointNormativeResult]


@dataclass(frozen=True)
class NormativeResult:
    girder: PointNormativeResult
    tower: TowerNormativeResult


def compute_design_speed(
    u10: float,
    risk: float,
    terrain: float,
    height: float,
    alpha0: float = 0.16,
    *,
    enforce_code_minimum: bool = True,
) -> float:
    reference_speed = clamp_basic_wind_speed(u10) if enforce_code_minimum else u10
    return risk * terrain * ((height / 10.0) ** alpha0) * reference_speed


def compute_turbulence(ud: float, height: float, roughness: float) -> PointNormativeResult:
    iu = 1.0 / math.log(height / roughness)
    iv = 0.88 * iu
    iw = 0.50 * iu
    return PointNormativeResult(
        height=height,
        ud=ud,
        iu=iu,
        iv=iv,
        iw=iw,
        sigma_u=iu * ud,
        sigma_v=iv * ud,
        sigma_w=iw * ud,
    )


def apply_overrides(point: PointNormativeResult, overrides: OverrideInput) -> PointNormativeResult:
    if not overrides.enabled:
        return point

    iu = overrides.iu if overrides.iu is not None else point.iu
    iv = overrides.iv if overrides.iv is not None else point.iv
    iw = overrides.iw if overrides.iw is not None else point.iw

    return PointNormativeResult(
        height=point.height,
        ud=point.ud,
        iu=iu,
        iv=iv,
        iw=iw,
        sigma_u=iu * point.ud,
        sigma_v=iv * point.ud,
        sigma_w=iw * point.ud,
    )


def compute_normative_parameters(request: WindRequest) -> NormativeResult:
    roughness = resolve_roughness_length(request.site.surface_class)
    girder_height = clamp_reference_height(request.site.girder_reference_height)
    girder_ud = compute_design_speed(
        request.site.u10,
        request.site.risk_coefficient,
        request.site.terrain_coefficient,
        girder_height,
        enforce_code_minimum=request.site.enforce_code_minimum_wind_speed,
    )
    girder_result = apply_overrides(compute_turbulence(girder_ud, girder_height, roughness), request.overrides)

    tower_levels: list[PointNormativeResult] = []
    for height in request.site.tower_heights:
        level_height = clamp_reference_height(height)
        level_ud = compute_design_speed(
            request.site.u10,
            request.site.risk_coefficient,
            request.site.terrain_coefficient,
            level_height,
            enforce_code_minimum=request.site.enforce_code_minimum_wind_speed,
        )
        tower_levels.append(apply_overrides(compute_turbulence(level_ud, level_height, roughness), request.overrides))

    return NormativeResult(girder=girder_result, tower=TowerNormativeResult(levels=tower_levels))
