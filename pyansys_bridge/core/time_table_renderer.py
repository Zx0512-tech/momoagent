"""APDL time-history TABLE rendering for unified loads."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Iterable

from pyansys_bridge.models import TimeHistoryLoad


DEFAULT_TABLE_NAMES = {
    "earthquake": "EQ_ACC_TABLE",
    "wind": "WIND_LOAD",
    "traffic": "TRAFFIC_LOAD",
}


def render_time_table(load: TimeHistoryLoad, table_name: str) -> str:
    """Render one APDL TABLE for a time-history load."""

    source_path = load.metadata.get("source_path")
    if source_path and load.metadata.get("inline_table_from_source"):
        return _render_inline_time_table(load, table_name)
    if source_path:
        rows = _table_row_count(source_path, load.dt, load.duration)
        path_var = str(load.metadata.get("path_var") or f"{table_name}_PATH")
        fname, ext = _tread_file_args(source_path)
        lines = [
            f"{path_var}='{source_path}'",
            f"*DIM,{table_name},TABLE,{rows},1,1,TIME",
            f"*TREAD,{table_name},{fname},{ext}",
        ]
        scale = load.metadata.get("scale")
        if scale is not None and float(scale) != 1.0:
            lines.append(f"*VOPER,{table_name}(1,1),{table_name}(1,1),MULT,{float(scale)}")
        lines.append(f"{table_name}_ROWS={rows}")
        return "\n".join(lines)

    rows = _inline_table_row_count(load)
    return _render_inline_time_table(load, table_name, rows=rows)


def _render_inline_time_table(load: TimeHistoryLoad, table_name: str, rows: int | None = None) -> str:
    rows = _inline_table_row_count(load) if rows is None else rows
    lines = [f"*DIM,{table_name},TABLE,{rows},1,1,TIME"]
    for index in range(1, rows + 1):
        sample = load.samples[min(index - 1, load.sample_count - 1)]
        time_value = (index - 1) * load.dt
        lines.append(f"{table_name}({index},0)={time_value:g}")
        lines.append(f"{table_name}({index},1)={_sample_value(sample):g}")
    lines.append(f"{table_name}_ROWS={rows}")
    return "\n".join(lines)


def render_load_tables(loads: Iterable[TimeHistoryLoad]) -> str:
    """Render all APDL load TABLE blocks."""

    rendered = []
    for load in loads:
        block = _render_load_tables_by_kind(load)
        if block:
            rendered.append(f"{_load_comment(load)}\n{block}")
    return "\n".join(block for block in rendered if block)


def _render_load_tables_by_kind(load: TimeHistoryLoad) -> str:
    table_name = str(load.application.get("table_name") or DEFAULT_TABLE_NAMES[load.kind])
    if load.application.get("type") == "stbridge_traffic_macro":
        return ""
    distributed = load.application.get("distributed_tables")
    if distributed:
        return _render_distributed_tables(load, distributed)
    return render_time_table(load, table_name)


def _load_comment(load: TimeHistoryLoad) -> str:
    name = load.metadata.get("name") or load.kind
    path = load.metadata.get("source_path")
    scale = load.metadata.get("scale", 1.0)
    return f"! load={name}, path={path}, scale={scale}, dt={load.dt}, duration={load.duration}"


def _render_distributed_tables(load: TimeHistoryLoad, spec) -> str:
    source_name = str(spec["source_name"])
    rows = _table_row_count(load.metadata.get("source_path"), load.dt, load.duration)
    source_column_count = _source_column_count(spec.get("tables", ()))
    configured_source = load.metadata.get("source_path")
    existing_source = _existing_source_path(configured_source)
    if configured_source and Path(str(configured_source)).is_absolute() and existing_source is None:
        raise ValueError(
            f"ANSYS distributed TABLE requires an existing source file: {configured_source}"
        )
    if existing_source is not None:
        return _render_distributed_vread_tables(
            load,
            spec,
            source_name,
            rows,
            str(spec.get("path_var") or f"{source_name}_PATH"),
            existing_source,
        )
    lines = _source_table_lines(
        load,
        source_name,
        rows,
        str(spec.get("path_var") or f"{source_name}_PATH"),
        source_column_count,
    )
    for table in spec.get("tables", ()):
        source_column = _source_column(table)
        offset = float(table.get("offset", 0.0))
        lines.extend(
            [
                f"*DIM,{table['name']},TABLE,{rows},1,1,TIME",
                f"*DO,IROW,1,{rows}",
                f"{table['name']}(IROW,0)={source_name}(IROW,0)",
                f"{table['name']}(IROW,1)={source_name}(IROW,{source_column})*{_apdl_factor(table['factor'])}+{offset:g}",
                "*ENDDO",
            ]
        )
    return "\n".join(lines)


def _render_distributed_vread_tables(
    load: TimeHistoryLoad,
    spec,
    source_name: str,
    rows: int,
    path_var: str,
    source_path: Path,
) -> str:
    lines = [f"{path_var}='{source_path}'"]
    for table in spec.get("tables", ()):
        source_column = _source_column(table)
        factor = float(table["factor"])
        offset = float(table.get("offset", 0.0))
        value_path = write_transformed_vread_column_file(
            source_path,
            source_name,
            source_column,
            rows,
            factor=factor,
            offset=offset,
        )
        fname, ext = _tread_file_args(value_path)
        lines.extend(
            [
                f"*DIM,{table['name']},TABLE,{rows},1,1,TIME",
                f"*VFILL,{table['name']}(1,0),RAMP,0,{float(load.dt):.12g}",
                f"*VREAD,{table['name']}(1,1),{fname},{ext.upper()},,,{rows},1",
                "(1E22.14)",
            ]
        )
    return "\n".join(lines)


def _source_table_lines(
    load: TimeHistoryLoad,
    source_name: str,
    rows: int,
    path_var: str,
    source_column_count: int,
) -> list[str]:
    source_path = load.metadata.get("source_path")
    if source_path:
        fname, ext = _tread_file_args(source_path)
        return [
            f"{path_var}='{source_path}'",
            f"*DIM,{source_name},TABLE,{rows},{source_column_count},1,TIME",
            f"*TREAD,{source_name},{fname},{ext}",
        ]
    lines = [
        f"*DIM,{source_name},TABLE,{rows},{source_column_count},1,TIME",
        f"*DO,IROW,1,{rows}",
        f"{source_name}(IROW,0)=(IROW-1)*{load.dt}",
    ]
    if source_column_count == 1:
        lines.append(f"{source_name}(IROW,1)=1.0")
    else:
        lines.extend(
            [
                f"*DO,ICOL,1,{source_column_count}",
                f"{source_name}(IROW,ICOL)=1.0",
                "*ENDDO",
            ]
        )
    lines.append("*ENDDO")
    return lines


def _sample_value(sample) -> float:
    if isinstance(sample, tuple):
        return float(sample[0])
    return float(sample)


def _apdl_factor(value: object) -> str:
    text = str(value).strip()
    if text.startswith("-"):
        return f"({text})"
    return text


def _source_column(table: dict[str, object]) -> int:
    column = int(table.get("source_column", 1))
    if column <= 0:
        raise ValueError("source_column must be a positive 1-based load-data column")
    return column


def _source_column_count(tables) -> int:
    return max((_source_column(dict(table)) for table in tables or ()), default=1)


def _existing_source_path(path) -> Path | None:
    if not path:
        return None
    source = Path(str(path))
    if source.exists() and source.is_file():
        return source.resolve()
    return None


def write_transformed_vread_column_file(
    source_path: Path,
    source_name: str,
    source_column: int,
    rows: int,
    *,
    factor: float,
    offset: float,
) -> Path:
    values = _read_time_history_matrix_column(source_path, source_column)
    if not values:
        raise ValueError(f"ANSYS {source_name} source contains no values: {source_path}")
    if len(values) < rows:
        values.extend([values[-1]] * (rows - len(values)))
    values = [value * factor + offset for value in values[:rows]]
    output_dir = source_path.with_name(f"{source_path.stem}_{source_name.lower()}_ansys_columns")
    output_dir.mkdir(parents=True, exist_ok=True)
    transform_hash = sha256(f"{factor:.17g}|{offset:.17g}".encode("ascii")).hexdigest()[:12]
    output_path = output_dir / f"col_{source_column:04d}_{transform_hash}.txt"
    output_path.write_text(
        "".join(f"{value:22.14E}\n" for value in values),
        encoding="utf-8",
    )
    return output_path


def _read_time_history_matrix_column(source_path: Path, source_column: int) -> list[float]:
    import csv

    matrix: list[list[float]] = []
    with source_path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        for raw_line in handle:
            text = raw_line.strip()
            if not text or text.startswith(("#", "!", "/")):
                continue
            cells = next(csv.reader([text])) if "," in text else text.split()
            try:
                matrix.append([float(cell) for cell in cells if str(cell).strip()])
            except ValueError:
                continue
    if len(matrix) >= 2 and matrix[0] == [float(index) for index in range(1, len(matrix[0]) + 1)]:
        matrix = matrix[1:]
    if not matrix:
        return []
    has_time_column = len(matrix) >= 2 and len(matrix[0]) >= 2 and matrix[1][0] > matrix[0][0]
    column_index = int(source_column) if has_time_column else int(source_column) - 1
    values = []
    for row in matrix:
        if column_index < 0 or column_index >= len(row):
            raise ValueError(
                f"ANSYS load source column {source_column} exceeds matrix width in {source_path}"
            )
        values.append(float(row[column_index]))
    return values


def _inline_table_row_count(load: TimeHistoryLoad) -> int:
    duration_rows = max(1, int(round(float(load.duration) / float(load.dt))) + 1)
    return max(load.sample_count, duration_rows)


def _table_row_count(path, dt: float, duration: float) -> int:
    if path:
        source = Path(str(path))
        if source.exists() and source.is_file():
            rows = sum(
                1
                for line in source.read_text(encoding="utf-8", errors="ignore").splitlines()
                if line.strip() and not line.lstrip().startswith(("#", "!"))
            )
            if rows > 0:
                return rows
    if dt > 0.0:
        return max(1, int(round(float(duration) / float(dt))) + 1)
    return 1


def _tread_file_args(path) -> tuple[str, str]:
    source = Path(str(path))
    ext = source.suffix.lstrip(".") or "txt"
    return source.with_suffix("").as_posix(), ext
