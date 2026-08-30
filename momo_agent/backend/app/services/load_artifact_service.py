from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path, PurePath
from typing import Any

from fastapi import HTTPException, Request

from app.services.load_import_service import MAX_UPLOAD_BYTES, load_import_service
from app.services.platform_store import (
    AGENT_LOAD_TARGET_SETS,
    AGENT_TRAFFIC_FORCE_COMPONENT,
    AGENT_WIND_FORCE_COMPONENT,
    gen_id,
    platform_store,
)


REPO_ROOT = Path(__file__).resolve().parents[4]
BUNDLED_EARTHQUAKE_RELATIVE_PATH = Path(
    'analysis_data/earthquake_inputs/earthquake_acceleration_record.txt'
)
BUNDLED_WIND_RELATIVE_PATH = Path(
    'analysis_data/wind_inputs/wind_vertical_8nodes_10mps_3600s.csv'
)
BUNDLED_WIND_TARGET_SET_ID = 'STBRIDGE_WIND_DECK_NODES'
BUNDLED_WIND_NODE_COLUMN_PREFIX = 'fy_node_'
# 车流是移动荷载：163 个主梁节点各有独立时程，空间分布随时间变化就是它的物理
# 特征，不能像风那样先求和再等权分配。所以标准制品是稠密矩阵（time_s + 163 列），
# 配一份逐节点 mapping 声明列号；两者的 SHA 都要进审批冻结。
BUNDLED_TRAFFIC_RELATIVE_PATH = Path(
    'analysis_data/traffic_inputs/traffic_random_base_3600s.csv'
)
BUNDLED_TRAFFIC_MAPPING_RELATIVE_PATH = Path(
    'analysis_data/traffic_inputs/traffic_random_base_3600s_mappings.json'
)
BUNDLED_TRAFFIC_TARGET_SET_ID = 'STBRIDGE_TRAFFIC_DECK_NODES'


class LoadArtifactService:
    async def read_upload(self, request: Request) -> bytes:
        content_length = request.headers.get('content-length')
        if content_length and content_length.isdigit() and int(content_length) > MAX_UPLOAD_BYTES:
            self._too_large()
        content = bytearray()
        async for chunk in request.stream():
            if len(content) + len(chunk) > MAX_UPLOAD_BYTES:
                self._too_large()
            content.extend(chunk)
        return bytes(content)

    def upload(self, file_name: str, content: bytes) -> dict[str, Any]:
        self._validate_file_name(file_name)
        inspection, _ = load_import_service.inspect(file_name, content)
        upload_id = gen_id('loadup')
        artifact = platform_store.register_artifact(
            kind='RAW_DATA',
            name=file_name,
            path=f'output/platform_store/load_uploads/{upload_id}/{file_name}',
            mime_type=self.mime_type(file_name),
            preview=inspection,
            content=content,
        )
        return {
            'artifactId': artifact.artifact_id,
            'sha256': artifact.sha256,
            'fileName': file_name,
            'inspection': inspection,
        }

    def provision_bundled_earthquake(self, run_id: str) -> dict[str, Any]:
        """把项目内置地震记录标准化并登记到当前智能体运行。"""
        source_path = REPO_ROOT / BUNDLED_EARTHQUAKE_RELATIVE_PATH
        try:
            source_content = source_path.read_bytes()
        except OSError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'BUNDLED_EARTHQUAKE_NOT_AVAILABLE',
                    'message': '项目内置地震荷载文件不可读取',
                },
            ) from exc
        mapping = {
            'version': 2,
            'source': 'BUNDLED_PROJECT_DATA',
            'loadKind': 'EARTHQUAKE',
            'time': {'column': None, 'stepS': 0.01, 'unit': 's'},
            'channels': [{
                'valueColumn': 'column_1',
                'applicationType': 'UNIFORM_EXCITATION',
                'targetType': None,
                'targetId': None,
                'component': 'UX',
                'quantity': 'ACCELERATION',
                'sourceUnit': 'm/s2',
                'scale': 1.0,
            }],
        }
        standardized = load_import_service.standardize(
            file_name=source_path.name,
            content=source_content,
            mapping=mapping,
        )
        report = {
            **standardized.report,
            'source': 'BUNDLED_PROJECT_DATA',
            'sourcePath': BUNDLED_EARTHQUAKE_RELATIVE_PATH.as_posix(),
        }
        standard_artifact = platform_store.register_artifact(
            kind='CSV_TIMESERIES',
            name='earthquake_acceleration_record_momo_standard.csv',
            path=f'output/platform_store/agent_runs/{run_id}/bundled_earthquake_momo_standard.csv',
            mime_type='text/csv; charset=utf-8',
            preview={
                'rows': standardized.content.decode('utf-8').splitlines()[:9],
                **report,
            },
            content=standardized.content,
            run_id=run_id,
        )
        report_content = json.dumps(
            report,
            ensure_ascii=False,
            separators=(',', ':'),
            allow_nan=False,
        ).encode('utf-8')
        report_artifact = platform_store.register_artifact(
            kind='JSON_SUMMARY',
            name='bundled_earthquake_load_report.json',
            path=f'output/platform_store/agent_runs/{run_id}/bundled_earthquake_load_report.json',
            mime_type='application/json',
            preview=report,
            content=report_content,
            run_id=run_id,
        )
        return {
            'mapping': mapping,
            'standardArtifactId': standard_artifact.artifact_id,
            'standardSha256': standard_artifact.sha256,
            'artifactIds': [standard_artifact.artifact_id, report_artifact.artifact_id],
        }

    def provision_bundled_wind(self, run_id: str) -> dict[str, Any]:
        """把 momo 生成的默认风荷载记录标准化并登记到当前智能体运行。

        源文件由 analysis_data/wind_inputs/generate_wind_nodal_force.py 生成：
        3600 s、dt=1 s，逐节点独立的竖向节点力，列顺序与登记目标集节点顺序一致。
        每个节点单独成一个通道，空间相关性由生成阶段的相干矩阵刻画，不做求和折叠。
        """
        source_path = REPO_ROOT / BUNDLED_WIND_RELATIVE_PATH
        try:
            source_content = source_path.read_bytes()
        except OSError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'BUNDLED_WIND_NOT_AVAILABLE',
                    'message': '项目内置风荷载文件不可读取',
                },
            ) from exc
        value_columns = bundled_wind_node_columns(source_content)
        mapping = {
            'version': 2,
            'source': 'BUNDLED_PROJECT_DATA',
            'loadKind': 'WIND',
            'time': {'column': 'time', 'unit': 's'},
            'channels': [
                {
                    'valueColumn': column,
                    'applicationType': 'NODAL_FORCE',
                    'targetType': 'NODE_GROUP',
                    'targetId': BUNDLED_WIND_TARGET_SET_ID,
                    'component': AGENT_WIND_FORCE_COMPONENT,
                    'quantity': 'FORCE',
                    'sourceUnit': 'N',
                    'scale': 1.0,
                }
                for column in value_columns
            ],
        }
        standardized = load_import_service.standardize(
            file_name=source_path.name,
            content=source_content,
            mapping=mapping,
        )
        report = {
            **standardized.report,
            'source': 'BUNDLED_PROJECT_DATA',
            'sourcePath': BUNDLED_WIND_RELATIVE_PATH.as_posix(),
            'sourceSha256': sha256(source_content).hexdigest(),
            'aggregation': 'PER_NODE_INDEPENDENT_FORCE_COLUMNS',
            'nodeColumns': list(value_columns),
            'targetSetId': BUNDLED_WIND_TARGET_SET_ID,
        }
        standard_artifact = platform_store.register_artifact(
            kind='CSV_TIMESERIES',
            name='wind_vertical_8nodes_10mps_3600s_momo_standard.csv',
            path=f'output/platform_store/agent_runs/{run_id}/bundled_wind_momo_standard.csv',
            mime_type='text/csv; charset=utf-8',
            preview={
                'rows': standardized.content.decode('utf-8').splitlines()[:9],
                **report,
            },
            content=standardized.content,
            run_id=run_id,
        )
        report_content = json.dumps(
            report,
            ensure_ascii=False,
            separators=(',', ':'),
            allow_nan=False,
        ).encode('utf-8')
        report_artifact = platform_store.register_artifact(
            kind='JSON_SUMMARY',
            name='bundled_wind_load_report.json',
            path=f'output/platform_store/agent_runs/{run_id}/bundled_wind_load_report.json',
            mime_type='application/json',
            preview=report,
            content=report_content,
            run_id=run_id,
        )
        return {
            'mapping': mapping,
            'standardArtifactId': standard_artifact.artifact_id,
            'standardSha256': standard_artifact.sha256,
            'artifactIds': [standard_artifact.artifact_id, report_artifact.artifact_id],
        }

    def provision_bundled_traffic(self, run_id: str) -> dict[str, Any]:
        """把项目内置随机车流荷载登记到当前智能体运行。

        与风路径的关键差异：车流是移动荷载，163 个主梁节点各有独立时程，
        不能先对空间列求和再等权分配（那样会丢掉荷载沿桥移动这一物理特征）。
        因此标准制品直接是稠密矩阵（`time_s` + 163 列节点力），另配一份逐节点
        mapping 制品声明「节点 → 列号」，求解侧按列取数、不做任何再分配。
        两个制品的 SHA 都由审批冻结，缺任一个必须失败关闭。
        """
        source_path = REPO_ROOT / BUNDLED_TRAFFIC_RELATIVE_PATH
        mapping_path = REPO_ROOT / BUNDLED_TRAFFIC_MAPPING_RELATIVE_PATH
        try:
            source_content = source_path.read_bytes()
            mapping_content = mapping_path.read_bytes()
        except OSError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'BUNDLED_TRAFFIC_NOT_AVAILABLE',
                    'message': '项目内置车流荷载文件不可读取',
                },
            ) from exc
        node_mappings = json.loads(mapping_content.decode('utf-8'))
        header, sample_count, time_step_s, duration_s = inspect_traffic_matrix_csv(source_content)
        node_columns = header[1:]
        if len(node_mappings) != len(node_columns):
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'BUNDLED_TRAFFIC_INVALID',
                    'message': '内置车流 mapping 条目数与矩阵列数不一致',
                },
            )
        # mapping 的 source_column 必须与矩阵列顺序严格双射，否则节点与时程会错位，
        # 而错位在量纲上完全看不出来。这里在登记阶段就钉死。
        expected_columns = list(range(1, len(node_columns) + 1))
        if [int(item['source_column']) for item in node_mappings] != expected_columns:
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'BUNDLED_TRAFFIC_INVALID',
                    'message': '内置车流 mapping 的 source_column 必须与矩阵列号一一对应',
                },
            )
        for item, column_name in zip(node_mappings, node_columns):
            if column_name != f"node_{int(item['fem_node_id'])}_fy_N":
                raise HTTPException(
                    status_code=422,
                    detail={
                        'code': 'BUNDLED_TRAFFIC_INVALID',
                        'message': '内置车流 mapping 的节点顺序与矩阵列名不一致',
                    },
                )
        target_nodes = [int(item['fem_node_id']) for item in node_mappings]
        mapping = {
            'version': 2,
            'source': 'BUNDLED_PROJECT_DATA',
            'loadKind': 'TRAFFIC',
            'time': {'column': 'time_s', 'unit': 's'},
            # 单条通道描述的是整个矩阵：applicationType 用 NODAL_FORCE_MATRIX
            # 与风的单通道 NODAL_FORCE 区分开，审批门按这个值判定走矩阵校验。
            'channels': [{
                'valueColumn': 'node_fy_N_matrix',
                'applicationType': 'NODAL_FORCE_MATRIX',
                'targetType': 'NODE_GROUP',
                'targetId': BUNDLED_TRAFFIC_TARGET_SET_ID,
                'component': AGENT_TRAFFIC_FORCE_COMPONENT,
                'quantity': 'FORCE',
                'sourceUnit': 'N',
                'scale': 1.0,
                'matrixColumnCount': len(node_columns),
            }],
        }
        report = {
            'schemaVersion': 2,
            'source': 'BUNDLED_PROJECT_DATA',
            'sourceFileName': source_path.name,
            'sourcePath': BUNDLED_TRAFFIC_RELATIVE_PATH.as_posix(),
            'sourceSha256': sha256(source_content).hexdigest(),
            'mappingPath': BUNDLED_TRAFFIC_MAPPING_RELATIVE_PATH.as_posix(),
            'mappingSha256': sha256(mapping_content).hexdigest(),
            'loadKind': 'TRAFFIC',
            'applicationType': 'NODAL_FORCE_MATRIX',
            'sampleCount': sample_count,
            'timeStepS': time_step_s,
            'durationS': duration_s,
            'nodeCount': len(target_nodes),
            'targetSetId': BUNDLED_TRAFFIC_TARGET_SET_ID,
            'targetNodes': target_nodes,
            'unit': 'N',
            'component': AGENT_TRAFFIC_FORCE_COMPONENT,
            # 逐节点独立时程，求解侧不得再做等权分配或求和。
            'distribution': 'PER_NODE_INDEPENDENT_TIME_HISTORY',
        }
        standard_artifact = platform_store.register_artifact(
            kind='CSV_TIMESERIES',
            name='traffic_random_base_3600s_momo_standard.csv',
            path=f'output/platform_store/agent_runs/{run_id}/bundled_traffic_momo_standard.csv',
            mime_type='text/csv; charset=utf-8',
            preview={
                'rows': source_content.decode('utf-8-sig').splitlines()[:5],
                **report,
            },
            content=source_content,
            run_id=run_id,
        )
        mapping_artifact = platform_store.register_artifact(
            kind='JSON_SUMMARY',
            name='traffic_random_base_3600s_node_mappings.json',
            path=f'output/platform_store/agent_runs/{run_id}/bundled_traffic_node_mappings.json',
            mime_type='application/json',
            preview={'nodeCount': len(target_nodes), 'mappings': node_mappings[:8]},
            content=mapping_content,
            run_id=run_id,
        )
        # mapping 制品的 ID/SHA 要跟着 mapping 字典走：审批门只接收 mapping 与
        # 标准制品两个入参，把它挂在这里就能一路流到 frozen_action，
        # 不必给四个 Agent 的 prepare_approval 都加一个新形参。
        mapping['pointMappingArtifactId'] = mapping_artifact.artifact_id
        mapping['pointMappingSha256'] = mapping_artifact.sha256
        report_content = json.dumps(
            report,
            ensure_ascii=False,
            separators=(',', ':'),
            allow_nan=False,
        ).encode('utf-8')
        report_artifact = platform_store.register_artifact(
            kind='JSON_SUMMARY',
            name='bundled_traffic_load_report.json',
            path=f'output/platform_store/agent_runs/{run_id}/bundled_traffic_load_report.json',
            mime_type='application/json',
            preview=report,
            content=report_content,
            run_id=run_id,
        )
        return {
            'mapping': mapping,
            'standardArtifactId': standard_artifact.artifact_id,
            'standardSha256': standard_artifact.sha256,
            'pointMappingArtifactId': mapping_artifact.artifact_id,
            'pointMappingSha256': mapping_artifact.sha256,
            'artifactIds': [
                standard_artifact.artifact_id,
                mapping_artifact.artifact_id,
                report_artifact.artifact_id,
            ],
        }

    @staticmethod
    def mime_type(file_name: str) -> str:
        # PEER NGA 记录（.AT1/.AT2）是纯文本，按 text/plain 登记。缺了这两个
        # 后缀会在上传时 KeyError，而 inspect 已经能解析它们。
        return {
            '.csv': 'text/csv',
            '.txt': 'text/plain',
            '.dat': 'text/plain',
            '.at1': 'text/plain',
            '.at2': 'text/plain',
            '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        }[Path(file_name).suffix.lower()]

    @staticmethod
    def _validate_file_name(file_name: str) -> None:
        if not file_name or PurePath(file_name).name != file_name or '/' in file_name or '\\' in file_name:
            raise HTTPException(status_code=422, detail={'code': 'INVALID_FILE_NAME', 'message': '文件名不合法'})

    @staticmethod
    def _too_large() -> None:
        raise HTTPException(
            status_code=422,
            detail={'code': 'FILE_TOO_LARGE', 'message': 'MVP 单文件上限为 20 MB'},
        )


def bundled_wind_node_columns(content: bytes) -> tuple[str, ...]:
    """按登记目标集的节点顺序取出内置风荷载的逐节点力列名。

    列顺序决定了后续每个通道落到哪个节点，一旦与目标集错位就是"求解成功但
    荷载装错节点"，量纲上看不出来。因此这里不接受任意列序：表头必须是
    `fy_node_<节点号>`，且节点号序列与 AGENT_LOAD_TARGET_SETS 登记顺序完全一致。
    """
    header_line = next(
        (line for line in content.decode('utf-8-sig').splitlines() if line.strip()),
        '',
    )
    if not header_line:
        raise HTTPException(
            status_code=422,
            detail={'code': 'BUNDLED_WIND_INVALID', 'message': '内置风荷载文件为空'},
        )
    cells = [cell.strip() for cell in header_line.split(',')]
    columns = tuple(cells[1:])
    expected_nodes = list(AGENT_LOAD_TARGET_SETS[BUNDLED_WIND_TARGET_SET_ID])
    expected_columns = tuple(
        f'{BUNDLED_WIND_NODE_COLUMN_PREFIX}{node}' for node in expected_nodes
    )
    if columns != expected_columns:
        raise HTTPException(
            status_code=422,
            detail={
                'code': 'BUNDLED_WIND_INVALID',
                'message': (
                    f'内置风荷载的力列必须按目标集 {BUNDLED_WIND_TARGET_SET_ID} '
                    f'的节点顺序命名为 {list(expected_columns)}，实际为 {list(columns)}'
                ),
            },
        )
    return columns


def inspect_traffic_matrix_csv(content: bytes) -> tuple[list[str], int, float, float]:
    """校验车流稠密矩阵并返回 (表头, 采样点数, 时间步长, 持续时间)。

    与风的 collapse 不同，这里不改写任何数值：矩阵按列直接交给求解器，
    本函数只负责在登记阶段把结构性错误挡住（列数漂移、非数值、非等步长），
    因为一旦进了求解配置，节点与列错位在量纲上完全看不出来。
    """
    text = content.decode('utf-8-sig')
    reader = csv.reader(line for line in text.splitlines() if line.strip())
    try:
        header = [cell.strip() for cell in next(reader)]
    except StopIteration as exc:
        raise HTTPException(
            status_code=422,
            detail={'code': 'BUNDLED_TRAFFIC_INVALID', 'message': '内置车流荷载文件为空'},
        ) from exc
    if len(header) < 2 or header[0] != 'time_s':
        raise HTTPException(
            status_code=422,
            detail={
                'code': 'BUNDLED_TRAFFIC_INVALID',
                'message': '内置车流矩阵首列必须是 time_s，且至少有一个节点力列',
            },
        )
    times: list[float] = []
    for line_number, cells in enumerate(reader, start=2):
        if len(cells) != len(header):
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'BUNDLED_TRAFFIC_INVALID',
                    'message': f'内置车流矩阵第 {line_number} 行列数与表头不一致',
                },
            )
        try:
            times.append(float(cells[0]))
            for cell in cells[1:]:
                float(cell)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    'code': 'BUNDLED_TRAFFIC_INVALID',
                    'message': f'内置车流矩阵第 {line_number} 行包含非数值',
                },
            ) from exc
    if len(times) < 2:
        raise HTTPException(
            status_code=422,
            detail={
                'code': 'BUNDLED_TRAFFIC_INVALID',
                'message': '内置车流矩阵至少需要两个时间点',
            },
        )
    if any(current <= previous for previous, current in zip(times, times[1:])):
        raise HTTPException(
            status_code=422,
            detail={
                'code': 'BUNDLED_TRAFFIC_INVALID',
                'message': '内置车流矩阵的时间列必须严格递增',
            },
        )
    time_step_s = times[1] - times[0]
    # 容差口径与 _apply_agent_standard_*_load 一致，避免登记放行、执行侧再拒。
    tolerance = max(abs(time_step_s), 1.0) * 1.0e-9
    if any(
        abs((current - previous) - time_step_s) > tolerance
        for previous, current in zip(times, times[1:])
    ):
        raise HTTPException(
            status_code=422,
            detail={
                'code': 'BUNDLED_TRAFFIC_INVALID',
                'message': '内置车流矩阵要求等时间步',
            },
        )
    return header, len(times), time_step_s, times[-1] - times[0]


load_artifact_service = LoadArtifactService()
