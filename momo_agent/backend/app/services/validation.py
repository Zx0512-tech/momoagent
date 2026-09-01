SURFACE_ROUGHNESS = {
    "A": 0.01,
    "B": 0.05,
    "C": 0.3,
    "D": 1.0,
}


def normalize_surface_class(surface_class: str) -> str:
    normalized = surface_class.strip().upper()
    if normalized not in SURFACE_ROUGHNESS:
        raise ValueError(f"Unsupported surface class: {surface_class}")
    return normalized


def resolve_roughness_length(surface_class: str) -> float:
    return SURFACE_ROUGHNESS[normalize_surface_class(surface_class)]


def clamp_reference_height(height: float) -> float:
    return max(height, 10.0)


def clamp_basic_wind_speed(u10: float) -> float:
    return max(u10, 24.5)
