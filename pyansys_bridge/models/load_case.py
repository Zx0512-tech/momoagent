"""Load case contract."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any


LOAD_KINDS = frozenset({"earthquake", "wind", "traffic"})
LoadSample = float | tuple[float, ...]


@dataclass(frozen=True)
class TimeHistoryLoad:
    """Unified time-history load contract for earthquake, wind, and traffic."""

    kind: str
    dt: float
    duration: float
    samples: tuple[LoadSample, ...]
    application: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in LOAD_KINDS:
            raise ValueError(f"TimeHistoryLoad.kind must be one of {sorted(LOAD_KINDS)}")
        if self.dt <= 0:
            raise ValueError("TimeHistoryLoad.dt must be positive")
        if self.duration < 0:
            raise ValueError("TimeHistoryLoad.duration cannot be negative")
        if not self.samples:
            raise ValueError("TimeHistoryLoad.samples cannot be empty")
        object.__setattr__(self, "samples", tuple(_normalize_sample(sample) for sample in self.samples))
        object.__setattr__(self, "application", dict(self.application))
        object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def sample_count(self) -> int:
        return len(self.samples)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "dt": self.dt,
            "duration": self.duration,
            "samples": [_sample_to_json(sample) for sample in self.samples],
            "application": dict(self.application),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TimeHistoryLoad":
        return cls(
            kind=str(payload["kind"]),
            dt=float(payload["dt"]),
            duration=float(payload["duration"]),
            samples=tuple(_sample_from_json(sample) for sample in payload["samples"]),
            application=dict(payload.get("application") or {}),
            metadata=dict(payload.get("metadata") or {}),
        )

    @classmethod
    def from_series(
        cls,
        *,
        kind: str,
        samples: tuple[LoadSample, ...] | list[LoadSample],
        dt: float,
        duration: float | None = None,
        application: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "TimeHistoryLoad":
        sample_tuple = tuple(samples)
        if not sample_tuple:
            raise ValueError("samples cannot be empty")
        effective_duration = float(duration) if duration is not None else float(dt) * max(len(sample_tuple) - 1, 0)
        return cls(
            kind=kind,
            dt=float(dt),
            duration=effective_duration,
            samples=sample_tuple,
            application=application or {},
            metadata=metadata or {},
        )

    def write_csv(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        vector_width = _sample_width(self.samples[0])
        value_columns = ["value"] if vector_width == 1 else [f"value_{index}" for index in range(1, vector_width + 1)]
        header = {
            "kind": self.kind,
            "dt": self.dt,
            "duration": self.duration,
            "application": self.application,
            "metadata": self.metadata,
        }
        serialized_header = json.dumps(
            header,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
        with target.open("w", encoding="utf-8", newline="") as handle:
            handle.write(f"# time_history_load={serialized_header}\n")
            writer = csv.writer(handle)
            writer.writerow(["time_s", *value_columns])
            for index, sample in enumerate(self.samples):
                writer.writerow([index * self.dt, *_sample_values(sample, vector_width)])
        return target

    @classmethod
    def from_csv(cls, path: str | Path) -> "TimeHistoryLoad":
        source = Path(path)
        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            first_line = handle.readline().strip()
            prefix = "# time_history_load="
            if not first_line.startswith(prefix):
                raise ValueError(f"Unified time-history CSV is missing metadata header: {source}")
            header = json.loads(first_line[len(prefix) :])
            reader = csv.reader(handle)
            columns = next(reader, None)
            if not columns or columns[0] != "time_s":
                raise ValueError(f"Unified time-history CSV must start with a time_s column: {source}")
            time_values = []
            rows = []
            for row in reader:
                if not row:
                    continue
                time_values.append(float(row[0]))
                rows.append(tuple(float(cell) for cell in row[1:] if str(cell).strip() != ""))
        if not rows:
            raise ValueError(f"Unified time-history CSV contains no samples: {source}")
        _validate_time_column(time_values, float(header["dt"]), source)
        samples = tuple(_scaled_sample(row, 1.0) for row in rows)
        return cls(
            kind=str(header["kind"]),
            dt=float(header["dt"]),
            duration=float(header["duration"]),
            samples=samples,
            application=dict(header.get("application") or {}),
            metadata=dict(header.get("metadata") or {}),
        )


@dataclass(frozen=True)
class LoadPointMapping:
    """Shared load-point to FEM-node mapping for load generators."""

    load_point_id: str
    fem_node_id: int
    dof: str
    scale: float = 1.0
    group: str = ""

    def __post_init__(self) -> None:
        if not self.load_point_id:
            raise ValueError("LoadPointMapping.load_point_id is required")
        if not self.dof:
            raise ValueError("LoadPointMapping.dof is required")
        object.__setattr__(self, "fem_node_id", int(self.fem_node_id))
        object.__setattr__(self, "dof", str(self.dof).upper())
        object.__setattr__(self, "scale", float(self.scale))

    def to_dict(self) -> dict[str, Any]:
        return {
            "load_point_id": self.load_point_id,
            "fem_node_id": self.fem_node_id,
            "dof": self.dof,
            "scale": self.scale,
            "group": self.group,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "LoadPointMapping":
        return cls(
            load_point_id=str(payload.get("load_point_id") or payload.get("label")),
            fem_node_id=int(payload.get("fem_node_id", payload.get("node_id"))),
            dof=str(payload["dof"]),
            scale=float(payload.get("scale", payload.get("weight", 1.0))),
            group=str(payload.get("group") or ""),
        )

    def to_ansys_load_point(self) -> dict[str, object]:
        return {
            "group": self.group or self.load_point_id,
            "label": self.load_point_id,
            "node_id": self.fem_node_id,
            "dof": self.dof,
            "weight": self.scale,
        }


@dataclass(frozen=True)
class LoadCaseTemplateContext:
    """Explicit template context model for load-case command rendering."""

    values: dict[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", dict(self.values))

    @property
    def load_name(self) -> str:
        return str(self.values["load_name"])

    @property
    def load_type(self) -> str:
        return str(self.values["load_type"])

    @property
    def load_dt(self) -> float | None:
        value = self.values.get("load_dt")
        return None if value is None else float(value)

    @property
    def gravity_accel_y(self) -> float:
        return float(self.values["gravity_accel_y"])

    def to_dict(self) -> dict[str, Any]:
        return dict(self.values)


LOAD_POINT_MAPPING_COLUMNS = ("load_point_id", "fem_node_id", "dof", "scale", "group")


def write_load_point_mappings_csv(
    mappings: tuple[LoadPointMapping, ...] | list[LoadPointMapping],
    path: str | Path,
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=LOAD_POINT_MAPPING_COLUMNS)
        writer.writeheader()
        for mapping in mappings:
            writer.writerow(mapping.to_dict())
    return target


def load_point_mappings_from_csv(path: str | Path) -> tuple[LoadPointMapping, ...]:
    source = Path(path)
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"Load-point mapping CSV is missing a header: {source}")
        mappings = []
        for row in reader:
            if not any(str(value or "").strip() for value in row.values()):
                continue
            mappings.append(LoadPointMapping.from_dict(dict(row)))
    if not mappings:
        raise ValueError(f"Load-point mapping CSV contains no mappings: {source}")
    return tuple(mappings)


@dataclass(frozen=True)
class LoadCase:
    """A single load case or named load combination."""

    name: str
    load_type: str
    path: str | None = None
    dt: float | None = None
    duration: float | None = None
    scale: float = 1.0
    direction: dict[str, float] = field(default_factory=dict)
    components: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("LoadCase.name is required")
        if not self.load_type:
            raise ValueError("LoadCase.load_type is required")
        if self.dt is not None and self.dt <= 0:
            raise ValueError("LoadCase.dt must be positive when provided")
        if self.duration is not None and self.duration < 0:
            raise ValueError("LoadCase.duration cannot be negative")

    def file_hash(self) -> str | None:
        if self.path is None:
            return None
        file_path = Path(self.path)
        if not file_path.exists() or not file_path.is_file():
            return None
        digest = sha256()
        with file_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "load_type": self.load_type,
            "path": self.path,
            "dt": self.dt,
            "duration": self.duration,
            "scale": self.scale,
            "direction": dict(self.direction),
            "components": list(self.components),
            "metadata": dict(self.metadata),
            "file_hash": self.file_hash(),
        }

    def fingerprint(self) -> str:
        payload = repr(_fingerprint_payload(self.to_dict())).encode("utf-8")
        return sha256(payload).hexdigest()[:12]

    def command_context(self) -> dict[str, Any]:
        """Return template context values for this load case and its components."""

        return self.command_template_context().to_dict()

    def command_template_context(self) -> LoadCaseTemplateContext:
        """Return an explicit template context model for command rendering."""

        context = {
            "load_name": self.name,
            "load_type": self.load_type,
            "load_path": self.path,
            "load_scale": self.scale,
            "load_dt": self.dt,
            "load_duration": self.duration,
            "gravity_accel_x": 0.0,
            "gravity_accel_y": -9.81,
            "gravity_accel_z": 0.0,
            "gravity_time": 1.0,
            "gravity_substeps": 1,
        }
        for component in self._component_dicts_for_context():
            prefix = str(component["load_type"])
            scale = component.get("scale")
            dt = component.get("dt")
            duration = component.get("duration")
            if self.load_type == "combination":
                if scale is not None:
                    scale *= self.scale
                dt = self.dt if self.dt is not None else dt
                duration = self.duration if self.duration is not None else duration
            context.update(
                {
                    f"{prefix}_name": component["name"],
                    f"{prefix}_type": component["load_type"],
                    f"{prefix}_path": component.get("path"),
                    f"{prefix}_scale": scale,
                    f"{prefix}_dt": dt,
                    f"{prefix}_duration": duration,
                    **_direction_context(prefix, component.get("direction")),
                }
            )
        if self.load_type != "combination":
            context.update(
                {
                    f"{self.load_type}_name": self.name,
                    f"{self.load_type}_type": self.load_type,
                    f"{self.load_type}_path": self.path,
                    f"{self.load_type}_scale": self.scale,
                    f"{self.load_type}_dt": self.dt,
                    f"{self.load_type}_duration": self.duration,
                    **_direction_context(self.load_type, self.direction),
                }
            )
            context.update({f"{self.load_type}_{key}": value for key, value in self.metadata.items()})
        return LoadCaseTemplateContext(context)

    def time_history_loads(self) -> list[TimeHistoryLoad]:
        """Return unified time-history loads while preserving the legacy LoadCase API."""

        if self.load_type == "combination":
            loads: list[TimeHistoryLoad] = []
            for component in self._component_dicts_for_context():
                component_scale = component.get("scale")
                effective_scale = self.scale
                if component_scale is not None:
                    effective_scale *= float(component_scale)
                component_case = LoadCase(
                    name=str(component["name"]),
                    load_type=str(component["load_type"]),
                    path=component.get("path"),
                    dt=self.dt if self.dt is not None else component.get("dt"),
                    duration=self.duration if self.duration is not None else component.get("duration"),
                    scale=effective_scale,
                    direction=dict(component.get("direction") or {}),
                    metadata=dict(component.get("metadata") or {}),
                )
                loads.extend(component_case.time_history_loads())
            return loads

        return [
            _time_history_load_from_legacy(
                kind=self.load_type,
                path=self.path,
                dt=self.dt,
                duration=self.duration,
                scale=self.scale,
                direction=self.direction,
                metadata=self.metadata,
            )
        ]

    def _component_dicts_for_context(self) -> tuple[dict[str, Any], ...]:
        components = self.metadata.get("component_load_cases")
        if components:
            return tuple(dict(component) for component in components)
        component_types = self.metadata.get("component_types") or []
        return tuple(
            {
                "name": name,
                "load_type": component_types[index] if index < len(component_types) else _infer_load_type(name),
                "path": None,
                "scale": None,
                "dt": self.dt,
                "duration": self.duration,
                "direction": {},
            }
            for index, name in enumerate(self.components)
        )


def _infer_load_type(name: str) -> str:
    lowered = name.lower()
    if "eq" in lowered or "earthquake" in lowered:
        return "earthquake"
    if "traffic" in lowered or "vehicle" in lowered or "car" in lowered:
        return "traffic"
    if "wind" in lowered:
        return "wind"
    return name


def _fingerprint_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _fingerprint_payload(item)
            for key, item in value.items()
            if key != "load_calibration"
        }
    if isinstance(value, list):
        return [_fingerprint_payload(item) for item in value]
    return value


def _direction_context(load_type: str, direction: dict[str, Any] | None) -> dict[str, float]:
    values = _default_direction(load_type)
    if load_type == "wind":
        return {f"{load_type}_direction_{axis}": values[axis] for axis in ("x", "y", "z")}
    if direction:
        for axis in ("x", "y", "z"):
            if axis in direction:
                values[axis] = float(direction[axis])
            upper_axis = axis.upper()
            if upper_axis in direction:
                values[axis] = float(direction[upper_axis])
    return {f"{load_type}_direction_{axis}": values[axis] for axis in ("x", "y", "z")}


def _default_direction(load_type: str) -> dict[str, float]:
    if load_type == "wind":
        return {"x": 0.0, "y": 1.0, "z": 0.0}
    if load_type == "traffic":
        return {"x": 0.0, "y": -1.0, "z": 0.0}
    return {"x": 0.0, "y": 0.0, "z": 0.0}


def load_earthquake_acce_txt(
    path: str | Path,
    *,
    dt: float | None = None,
    duration: float | None = None,
    scale: float = 1.0,
    application: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> TimeHistoryLoad:
    return _load_numeric_time_history(
        "earthquake",
        path,
        dt=dt,
        duration=duration,
        scale=scale,
        application=application or {"type": "uniform_excitation", "axis": "x"},
        metadata=metadata,
    )


def load_wind_csv(
    path: str | Path,
    *,
    dt: float | None = None,
    duration: float | None = None,
    scale: float = 1.0,
    application: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> TimeHistoryLoad:
    return _load_numeric_time_history(
        "wind",
        path,
        dt=dt,
        duration=duration,
        scale=scale,
        application=application or {"type": "nodal_force"},
        metadata=metadata,
    )


def load_traffic_csv(
    path: str | Path,
    *,
    dt: float | None = None,
    duration: float | None = None,
    scale: float = 1.0,
    application: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> TimeHistoryLoad:
    return _load_numeric_time_history(
        "traffic",
        path,
        dt=dt,
        duration=duration,
        scale=scale,
        application=application or {"type": "moving_or_nodal_force"},
        metadata=metadata,
    )


def _time_history_load_from_legacy(
    *,
    kind: str,
    path: str | None,
    dt: float | None,
    duration: float | None,
    scale: float,
    direction: dict[str, float],
    metadata: dict[str, Any],
) -> TimeHistoryLoad:
    if kind not in LOAD_KINDS:
        raise ValueError(f"Unsupported time-history load type: {kind}")
    application = {
        "type": _default_application_type(kind),
        "direction": dict(_default_direction(kind) if kind == "wind" else (direction or _default_direction(kind))),
    }
    load_metadata = {"scale": float(scale), **dict(metadata)}
    if path:
        loader = {
            "earthquake": load_earthquake_acce_txt,
            "wind": load_wind_csv,
            "traffic": load_traffic_csv,
        }[kind]
        return loader(
            path,
            dt=dt,
            duration=duration,
            scale=scale,
            application=application,
            metadata=load_metadata,
        )
    effective_dt = float(dt) if dt is not None else 1.0
    effective_duration = float(duration) if duration is not None else 0.0
    if dt is None:
        load_metadata["legacy_missing_dt"] = True
    return TimeHistoryLoad(
        kind=kind,
        dt=effective_dt,
        duration=effective_duration,
        samples=(float(scale),),
        application=application,
        metadata=load_metadata,
    )


def _load_numeric_time_history(
    kind: str,
    path: str | Path,
    *,
    dt: float | None,
    duration: float | None,
    scale: float,
    application: dict[str, Any],
    metadata: dict[str, Any] | None,
) -> TimeHistoryLoad:
    source = Path(path)
    rows, columns = _read_numeric_rows(source)
    if not rows:
        raise ValueError(f"No numeric load samples found in {source}")
    inferred_dt = _infer_dt(rows)
    sample_rows = _strip_time_column(rows)
    samples = tuple(_scaled_sample(row, scale) for row in sample_rows)
    effective_dt = float(dt) if dt is not None else inferred_dt
    if effective_dt <= 0:
        raise ValueError(f"Unable to infer positive dt from {source}")
    effective_duration = float(duration) if duration is not None else effective_dt * max(len(samples) - 1, 0)
    load_metadata = {
        "source_path": str(source),
        "scale": float(scale),
    }
    if columns:
        load_metadata["columns"] = tuple(columns[1:] if _has_time_column(rows) else columns)
    if metadata:
        load_metadata.update(metadata)
    return TimeHistoryLoad(
        kind=kind,
        dt=effective_dt,
        duration=effective_duration,
        samples=samples,
        application=application,
        metadata=load_metadata,
    )


def _read_numeric_rows(path: Path) -> tuple[list[tuple[float, ...]], tuple[str, ...]]:
    rows: list[tuple[float, ...]] = []
    columns: tuple[str, ...] = ()
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith(("#", "!", "/")):
                continue
            cells = next(csv.reader([line])) if "," in line else line.split()
            if not cells:
                continue
            try:
                numeric = tuple(float(cell) for cell in cells if str(cell).strip() != "")
            except ValueError:
                if not columns:
                    columns = tuple(cell.strip() for cell in cells)
                continue
            if numeric:
                rows.append(numeric)
    return rows, columns


def _infer_dt(rows: list[tuple[float, ...]]) -> float:
    if len(rows) >= 2 and _has_time_column(rows):
        return rows[1][0] - rows[0][0]
    return 1.0


def _has_time_column(rows: list[tuple[float, ...]]) -> bool:
    return len(rows[0]) >= 2 and len(rows) >= 2 and rows[1][0] > rows[0][0]


def _strip_time_column(rows: list[tuple[float, ...]]) -> list[tuple[float, ...]]:
    if _has_time_column(rows):
        return [row[1:] for row in rows]
    return rows


def _validate_time_column(values: list[float], dt: float, source: Path) -> None:
    tolerance = max(abs(dt), 1.0) * 1.0e-9
    for index, value in enumerate(values):
        expected = index * dt
        if abs(value - expected) > tolerance:
            raise ValueError(
                f"Unified time-history CSV has inconsistent time_s at row {index + 1} in {source}: "
                f"expected {expected}, got {value}"
            )


def _scaled_sample(row: tuple[float, ...], scale: float) -> LoadSample:
    values = tuple(value * float(scale) for value in row)
    if len(values) == 1:
        return values[0]
    return values


def _sample_width(sample: LoadSample) -> int:
    if isinstance(sample, tuple):
        return len(sample)
    return 1


def _sample_values(sample: LoadSample, width: int) -> tuple[float, ...]:
    values = tuple(float(value) for value in sample) if isinstance(sample, tuple) else (float(sample),)
    if len(values) != width:
        raise ValueError("TimeHistoryLoad samples must have a consistent column count")
    return values


def _normalize_sample(sample: Any) -> LoadSample:
    if isinstance(sample, (list, tuple)):
        return tuple(float(value) for value in sample)
    return float(sample)


def _sample_to_json(sample: LoadSample) -> float | list[float]:
    if isinstance(sample, tuple):
        return list(sample)
    return sample


def _sample_from_json(sample: Any) -> LoadSample:
    return _normalize_sample(sample)


def _default_application_type(kind: str) -> str:
    if kind == "earthquake":
        return "uniform_excitation"
    if kind == "wind":
        return "nodal_force"
    return "moving_or_nodal_force"
