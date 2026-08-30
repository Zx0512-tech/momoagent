from __future__ import annotations

import json
from typing import Any


TRAFFIC_LIBRARY_WIM_2021_01: dict[str, Any] = {
    'id': 'vehicle_library_wim_2021_01',
    'label': '2021-01 WIM 实测车流库',
    'description': '由 2021.1.txt 一车一行监测记录派生，日均约 89521 辆，默认仅加载主梁并采用双向车流。',
    'totalVehicles24h': 89521,
    'heavyVehicleRatio': 0.2092,
    'heavyVehicleDefinition': 'axleCount >= 3 OR grossWeightKg >= 10000',
    'dayCount': 31,
    'sourceTotalRows': 2775163,
    'appliedStructure': 'MAIN_GIRDER',
    'laneMode': 'MAIN_GIRDER_BIDIRECTIONAL',
    'hourlyFlow': [
        {'hour': 0, 'vehiclesPerHour': 1582, 'heavyVehicleRatio': 0.4252},
        {'hour': 1, 'vehiclesPerHour': 1271, 'heavyVehicleRatio': 0.4954},
        {'hour': 2, 'vehiclesPerHour': 1058, 'heavyVehicleRatio': 0.5108},
        {'hour': 3, 'vehiclesPerHour': 1041, 'heavyVehicleRatio': 0.5394},
        {'hour': 4, 'vehiclesPerHour': 1168, 'heavyVehicleRatio': 0.4827},
        {'hour': 5, 'vehiclesPerHour': 1555, 'heavyVehicleRatio': 0.4087},
        {'hour': 6, 'vehiclesPerHour': 2503, 'heavyVehicleRatio': 0.3361},
        {'hour': 7, 'vehiclesPerHour': 3559, 'heavyVehicleRatio': 0.2362},
        {'hour': 8, 'vehiclesPerHour': 4523, 'heavyVehicleRatio': 0.168},
        {'hour': 9, 'vehiclesPerHour': 5285, 'heavyVehicleRatio': 0.1443},
        {'hour': 10, 'vehiclesPerHour': 5393, 'heavyVehicleRatio': 0.1414},
        {'hour': 11, 'vehiclesPerHour': 5214, 'heavyVehicleRatio': 0.159},
        {'hour': 12, 'vehiclesPerHour': 5153, 'heavyVehicleRatio': 0.1572},
        {'hour': 13, 'vehiclesPerHour': 5748, 'heavyVehicleRatio': 0.1474},
        {'hour': 14, 'vehiclesPerHour': 6031, 'heavyVehicleRatio': 0.147},
        {'hour': 15, 'vehiclesPerHour': 6166, 'heavyVehicleRatio': 0.1472},
        {'hour': 16, 'vehiclesPerHour': 5965, 'heavyVehicleRatio': 0.1497},
        {'hour': 17, 'vehiclesPerHour': 5665, 'heavyVehicleRatio': 0.1623},
        {'hour': 18, 'vehiclesPerHour': 4693, 'heavyVehicleRatio': 0.1899},
        {'hour': 19, 'vehiclesPerHour': 4107, 'heavyVehicleRatio': 0.2114},
        {'hour': 20, 'vehiclesPerHour': 3881, 'heavyVehicleRatio': 0.2306},
        {'hour': 21, 'vehiclesPerHour': 3348, 'heavyVehicleRatio': 0.2589},
        {'hour': 22, 'vehiclesPerHour': 2655, 'heavyVehicleRatio': 0.3107},
        {'hour': 23, 'vehiclesPerHour': 1957, 'heavyVehicleRatio': 0.3647},
    ],
    'laneDistribution': {'1': 0.2289, '2': 0.1835, '3': 0.099, '4': 0.2119, '5': 0.1752, '6': 0.1015},
    'axleDistribution': {'1': 0.0001, '2': 0.8359, '3': 0.1393, '4': 0.0144, '5': 0.0041, '6': 0.0063},
    'weightBands': {'lt3t': 1940316, '3t_10t': 278406, '10t_20t': 267884, '20t_40t': 191363, 'ge40t': 97194},
}


def traffic_vehicle_libraries() -> list[dict[str, Any]]:
    return [TRAFFIC_LIBRARY_WIM_2021_01]


def get_traffic_vehicle_library(library_id: str | None) -> dict[str, Any]:
    library = TRAFFIC_LIBRARY_WIM_2021_01
    if library_id in (None, '', library['id']):
        return library
    raise ValueError(f'Unsupported vehicleLibraryId: {library_id}')


def build_random_traffic_payload(params: dict[str, Any]) -> dict[str, Any]:
    library = get_traffic_vehicle_library(params.get('vehicleLibraryId'))
    traffic_scale = max(float(params.get('trafficScale', 1.0)), 0.0)
    heavy_vehicle_scale = max(float(params.get('heavyVehicleScale', 1.0)), 0.0)
    effective_heavy_ratio = min(float(library['heavyVehicleRatio']) * heavy_vehicle_scale, 1.0)
    effective_total = int(round(float(library['totalVehicles24h']) * traffic_scale))
    hourly_flow = [
        {
            'hour': item['hour'],
            'vehiclesPerHour': int(round(float(item['vehiclesPerHour']) * traffic_scale)),
            'heavyVehicleRatio': min(float(item['heavyVehicleRatio']) * heavy_vehicle_scale, 1.0),
        }
        for item in library['hourlyFlow']
    ]
    preview = {
        'headers': ['hour', 'vehicles_per_hour', 'heavy_vehicle_ratio', 'applied_structure', 'lane_mode'],
        'previewRows': [
            [
                item['hour'],
                item['vehiclesPerHour'],
                f"{item['heavyVehicleRatio']:.4f}",
                library['appliedStructure'],
                library['laneMode'],
            ]
            for item in hourly_flow
        ],
        'totalRows': len(hourly_flow),
    }
    summary = {
        'vehicleLibraryId': library['id'],
        'vehicleLibraryLabel': library['label'],
        'sourceTotalRows': library['sourceTotalRows'],
        'sourceDayCount': library['dayCount'],
        'baseTotalVehicles24h': library['totalVehicles24h'],
        'effectiveTotalVehicles24h': effective_total,
        'baseHeavyVehicleRatio': library['heavyVehicleRatio'],
        'effectiveHeavyVehicleRatio': round(effective_heavy_ratio, 4),
        'trafficScale': traffic_scale,
        'heavyVehicleScale': heavy_vehicle_scale,
        'laneMode': library['laneMode'],
        'appliedStructure': library['appliedStructure'],
        'directionMode': 'BIDIRECTIONAL',
        'heavyVehicleDefinition': library['heavyVehicleDefinition'],
        'hourlyFlow': hourly_flow,
        'laneDistribution': library['laneDistribution'],
        'axleDistribution': library['axleDistribution'],
        'weightBands': library['weightBands'],
    }
    return {'summary': summary, 'preview': preview, 'csvBytes': _csv_bytes(preview), 'jsonBytes': _json_bytes(summary)}


def build_existing_traffic_payload(params: dict[str, Any], source_artifact: dict[str, Any]) -> dict[str, Any]:
    traffic_scale = max(float(params.get('trafficScale', 1.0)), 0.0)
    heavy_vehicle_scale = max(float(params.get('heavyVehicleScale', 1.0)), 0.0)
    hourly_flow = _scaled_hourly_flow(params.get('hourlyFlowProfile') or [], traffic_scale, heavy_vehicle_scale)
    source_preview = source_artifact.get('preview') or {}
    source_total_rows = int(source_preview.get('totalRows') or 0) if isinstance(source_preview, dict) else 0
    effective_total = sum(item['vehiclesPerHour'] for item in hourly_flow) if hourly_flow else int(round(source_total_rows * traffic_scale))
    effective_heavy_ratio = _weighted_heavy_ratio(hourly_flow)
    preview = {
        'headers': ['hour', 'vehicles_per_hour', 'heavy_vehicle_ratio', 'source_artifact_id', 'applied_structure', 'lane_mode'],
        'previewRows': [
            [
                item['hour'],
                item['vehiclesPerHour'],
                f"{item['heavyVehicleRatio']:.4f}",
                source_artifact['artifactId'],
                TRAFFIC_LIBRARY_WIM_2021_01['appliedStructure'],
                TRAFFIC_LIBRARY_WIM_2021_01['laneMode'],
            ]
            for item in hourly_flow
        ],
        'totalRows': len(hourly_flow) if hourly_flow else source_total_rows,
        'sourceArtifactId': source_artifact['artifactId'],
        'sourceArtifactName': source_artifact['name'],
    }
    summary = {
        'sourceMode': 'LOAD_EXISTING',
        'existingVehicleDataArtifactId': source_artifact['artifactId'],
        'sourceArtifactName': source_artifact['name'],
        'sourceArtifactKind': source_artifact['kind'],
        'sourceArtifactTotalRows': source_total_rows,
        'effectiveTotalVehicles24h': effective_total,
        'effectiveHeavyVehicleRatio': round(effective_heavy_ratio, 4),
        'trafficScale': traffic_scale,
        'heavyVehicleScale': heavy_vehicle_scale,
        'laneMode': TRAFFIC_LIBRARY_WIM_2021_01['laneMode'],
        'appliedStructure': TRAFFIC_LIBRARY_WIM_2021_01['appliedStructure'],
        'directionMode': 'BIDIRECTIONAL',
        'hourlyFlow': hourly_flow,
    }
    return {'summary': summary, 'preview': preview, 'csvBytes': _csv_bytes(preview), 'jsonBytes': _json_bytes(summary)}


def _scaled_hourly_flow(hourly_profile: list[dict[str, Any]], traffic_scale: float, heavy_vehicle_scale: float) -> list[dict[str, Any]]:
    return [
        {
            'hour': int(item.get('hour', index)),
            'vehiclesPerHour': int(round(float(item.get('vehiclesPerHour', 0)) * traffic_scale)),
            'heavyVehicleRatio': min(max(float(item.get('heavyVehicleRatio', 0.0)) * heavy_vehicle_scale, 0.0), 1.0),
        }
        for index, item in enumerate(hourly_profile)
    ]


def _weighted_heavy_ratio(hourly_flow: list[dict[str, Any]]) -> float:
    total = sum(float(item['vehiclesPerHour']) for item in hourly_flow)
    if total <= 0:
        return 0.0
    heavy = sum(float(item['vehiclesPerHour']) * float(item['heavyVehicleRatio']) for item in hourly_flow)
    return heavy / total


def _csv_bytes(preview: dict[str, Any]) -> bytes:
    rows = [','.join(preview['headers'])]
    rows.extend(','.join(map(str, row)) for row in preview['previewRows'])
    return ('\n'.join(rows) + '\n').encode('utf-8-sig')


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8')
