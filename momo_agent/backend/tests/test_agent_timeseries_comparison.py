from __future__ import annotations

import json
from hashlib import sha256
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.services.agent_conversation as conversation_module
import app.services.agent_service as agent_service_module
from app.main import app
from app.services.agent_service import AgentService


def _record(artifact_id: str, *, name: str, kind: str, path: str, content: bytes):
    return SimpleNamespace(
        artifact=SimpleNamespace(
            artifact_id=artifact_id,
            name=name,
            kind=kind,
            path=path,
            sha256=sha256(content).hexdigest(),
        ),
        content=content,
        preview={},
    )


def test_timeseries_comparison_reads_each_verified_case_from_registered_csv(monkeypatch) -> None:
    case_a = 'case_viscous_c1000_alpha03'
    case_b = 'case_viscous_c2000_alpha05'
    path_a = f'output/real/sweep_1/{case_a}/solver_outputs/a/timeseries.csv'
    path_b = f'output/real/sweep_1/{case_b}/solver_outputs/b/timeseries.csv'
    content_a = b'time,displacement,tower_base_shear\n0,0,10\n1,0.1,12\n'
    content_b = b'time,displacement,tower_base_shear\n0,0,20\n1,0.2,24\n'
    csv_a = _record('art_case_a', name='timeseries.csv', kind='CSV_TIMESERIES', path=path_a, content=content_a)
    csv_b = _record('art_case_b', name='timeseries.csv', kind='CSV_TIMESERIES', path=path_b, content=content_b)
    catalog_content = json.dumps({
        'schemaVersion': '1.0',
        'runId': 'agr_sweep',
        'verified': True,
        'entryCount': 2,
        'entries': [
            {
                'artifactPath': path_a,
                'columns': ['time', 'displacement', 'tower_base_shear'],
                'units': {'time': 's', 'displacement': 'm', 'tower_base_shear': 'N'},
                'sha256': csv_a.artifact.sha256,
                'verified': True,
            },
            {
                'artifactPath': path_b,
                'columns': ['time', 'displacement', 'tower_base_shear'],
                'units': {'time': 's', 'displacement': 'm', 'tower_base_shear': 'N'},
                'sha256': csv_b.artifact.sha256,
                'verified': True,
            },
        ],
    }, ensure_ascii=False).encode('utf-8')
    catalog = _record(
        'art_catalog',
        name='result_catalog.json',
        kind='JSON_SUMMARY',
        path='output/real/sweep_1/result_catalog.json',
        content=catalog_content,
    )
    records = {record.artifact.artifact_id: record for record in (csv_a, csv_b, catalog)}

    class Store:
        def get_artifact(self, artifact_id: str):
            return records[artifact_id]

    class Repository:
        def get_run(self, run_id: str):
            return run if run_id == run['runId'] else None

    run = {
        'runId': 'agr_sweep',
        'taskType': 'DAMPER_PARAMETER_SWEEP',
        'artifactIds': list(records),
        'resultSummary': {
            'caseResults': [
                {
                    'caseId': case_a,
                    'parameters': {'c': 1000.0, 'alpha': 0.3, 'vfloor': 0.001},
                    'isVerifiedSolverOutput': True,
                },
                {
                    'caseId': case_b,
                    'parameters': {'c': 2000.0, 'alpha': 0.5, 'vfloor': 0.001},
                    'isVerifiedSolverOutput': True,
                },
                {
                    'caseId': 'case_unverified',
                    'parameters': {'c': 3000.0, 'alpha': 0.5, 'vfloor': 0.001},
                    'isVerifiedSolverOutput': False,
                },
            ],
        },
    }
    store = Store()
    service = AgentService()
    service.repository = lambda: Repository()
    monkeypatch.setattr(agent_service_module, 'platform_store', store)
    monkeypatch.setattr(conversation_module, 'platform_store', store)

    payload = service.get_run_timeseries_comparison(
        run['runId'],
        columns=['displacement'],
        max_points=1000,
    )

    assert payload['availableColumns'] == ['time', 'displacement', 'tower_base_shear']
    assert payload['columns'] == ['time', 'displacement']
    assert [item['caseId'] for item in payload['cases']] == [case_a, case_b]
    assert payload['cases'][0]['label'] == 'c=1000，α=0.3'
    assert payload['cases'][1]['series']['displacement'] == [0.0, 0.2]
    assert service._sweep_case_label({'c': 1000.0, 'alpha': 0.3, 'vfloor': 0.002}) == 'c=1000，α=0.3，vfloor=0.002'

    selected_payload = service.get_run_timeseries_comparison(
        run['runId'],
        columns=['tower_base_shear'],
        case_ids=[case_b],
        max_points=1000,
    )

    assert selected_payload['columns'] == ['time', 'tower_base_shear']
    assert [item['caseId'] for item in selected_payload['cases']] == [case_b]


@pytest.fixture()
def comparison_route_spy(monkeypatch) -> list[dict]:
    """记录路由解析后传给服务层的参数，锁定 query 别名与拆分规则。"""
    calls: list[dict] = []

    def spy(run_id: str, *, columns, case_ids, max_points):
        calls.append({
            'runId': run_id,
            'columns': columns,
            'caseIds': case_ids,
            'maxPoints': max_points,
        })
        return {'runId': run_id, 'columns': columns, 'cases': []}

    monkeypatch.setattr(agent_service_module.agent_service, 'get_run_timeseries_comparison', spy)
    return calls


def test_comparison_route_parses_camel_case_query_aliases(comparison_route_spy) -> None:
    client = TestClient(app)

    response = client.get(
        '/api/v1/agent/runs/agr_sweep/timeseries/compare',
        params={
            'columns': 'displacement, tower_base_shear ',
            'caseIds': 'case_a, case_b ',
            'maxPoints': 250,
        },
    )

    assert response.status_code == 200
    assert comparison_route_spy == [{
        'runId': 'agr_sweep',
        'columns': ['displacement', 'tower_base_shear'],
        'caseIds': ['case_a', 'case_b'],
        'maxPoints': 250,
    }]


def test_comparison_route_defaults_to_all_cases_and_default_max_points(comparison_route_spy) -> None:
    client = TestClient(app)

    response = client.get('/api/v1/agent/runs/agr_sweep/timeseries/compare')

    assert response.status_code == 200
    assert comparison_route_spy == [{
        'runId': 'agr_sweep',
        'columns': [],
        'caseIds': [],
        'maxPoints': 1000,
    }]


def test_comparison_route_ignores_snake_case_max_points(comparison_route_spy) -> None:
    """别名生效的反证：snake_case 不被接受，回落到默认值。"""
    client = TestClient(app)

    response = client.get(
        '/api/v1/agent/runs/agr_sweep/timeseries/compare',
        params={'max_points': 250, 'case_ids': 'case_a'},
    )

    assert response.status_code == 200
    assert comparison_route_spy[0]['maxPoints'] == 1000
    assert comparison_route_spy[0]['caseIds'] == []


@pytest.mark.parametrize('max_points', [49, 5001, 'abc'])
def test_comparison_route_rejects_out_of_range_max_points(comparison_route_spy, max_points) -> None:
    client = TestClient(app)

    response = client.get(
        '/api/v1/agent/runs/agr_sweep/timeseries/compare',
        params={'maxPoints': max_points},
    )

    assert response.status_code == 422
    assert comparison_route_spy == []
