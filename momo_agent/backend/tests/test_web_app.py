from fastapi.testclient import TestClient

from app.main import PLATFORM_UI_INSTALLED, app


SAMPLE_PAYLOAD = {
    'project': {'name': '示例桥梁', 'note': '测试导出'},
    'site': {
        'u10': 30.0,
        'riskCoefficient': 1.0,
        'surfaceClass': 'B',
        'terrainCoefficient': 1.0,
        'airDensity': 1.25,
        'girderReferenceHeight': 50.0,
        'towerHeights': [10.0, 40.0, 80.0],
    },
    'girder': {
        'length': 1200.0,
        'segmentCount': 8,
        'width': 35.0,
        'depth': 4.5,
        'ch': 1.2,
        'cv': 0.8,
        'cm': 0.15,
    },
    'tower': {'height': 120.0, 'segmentCount': 6, 'width': 8.0, 'cd': 1.1},
    'timeHistory': {
        'duration': 60.0,
        'timeStep': 1.0,
        'frequencyCount': 64,
        'seed': 7,
        'spectrumModel': 'davenport',
        'verticalSpectrumModel': 'panofsky',
        'simulationMethod': 'harmonic',
        'arOrder': 4,
    },
    'overrides': {'enabled': False},
}


def test_root_serves_platform_build_or_reports_api_only_service() -> None:
    client = TestClient(app)
    response = client.get('/')

    assert response.status_code == 200
    if PLATFORM_UI_INSTALLED:
        assert response.headers['content-type'].startswith('text/html')
        assert '<div id="root"></div>' in response.text
        return
    assert response.json() == {
        'status': 'ok',
        'service': 'MOMO Bridge Analysis And Optimization Platform',
        'mode': 'api-only',
        'api_prefix': '/api',
        'frontend_contract': 'docs/frontend_api_contract.md',
        'platform_scope': 'docs/platform_capability_scope.md',
    }


def test_export_excel_returns_xlsx_bytes() -> None:
    client = TestClient(app)
    response = client.post('/api/export/excel', json=SAMPLE_PAYLOAD)

    assert response.status_code == 200
    assert response.headers['content-type'].startswith(
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    assert response.content[:2] == b'PK'


def test_export_girder_csv_returns_csv_content() -> None:
    client = TestClient(app)
    response = client.post('/api/export/girder-csv', json=SAMPLE_PAYLOAD)

    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/csv')
    assert '时间' in response.content.decode('utf-8-sig')


def test_export_unified_wind_csv_returns_time_history_contract() -> None:
    client = TestClient(app)
    response = client.post('/api/export/unified-wind-csv', json=SAMPLE_PAYLOAD)

    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/csv')
    text = response.content.decode('utf-8')
    assert '# time_history_load=' in text
    assert '"kind": "wind"' in text
    assert 'time_s,value' in text


def test_export_girder_all_points_csv_returns_csv_content() -> None:
    client = TestClient(app)
    response = client.post('/api/export/girder-all-points-csv', json=SAMPLE_PAYLOAD)

    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/csv')
    text = response.content.decode('utf-8-sig')
    assert '控制点,时间,风速,水平力,竖向力,力矩' in text
    assert len(text.splitlines()) > SAMPLE_PAYLOAD['timeHistory']['duration']


def test_export_tower_all_points_csv_returns_csv_content() -> None:
    client = TestClient(app)
    response = client.post('/api/export/tower-all-points-csv', json=SAMPLE_PAYLOAD)

    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/csv')
    text = response.content.decode('utf-8-sig')
    assert '高度,时间,风速,拖曳力' in text
    assert len(text.splitlines()) > SAMPLE_PAYLOAD['timeHistory']['duration']


def test_export_spectrum_csv_returns_csv_content() -> None:
    client = TestClient(app)
    response = client.post('/api/export/spectrum-csv', json=SAMPLE_PAYLOAD)

    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/csv')
    text = response.content.decode('utf-8-sig')
    assert '序列,频率,谱值' in text
    assert '主梁顺风目标谱' in text


def test_export_coherence_csv_returns_csv_content() -> None:
    client = TestClient(app)
    response = client.post('/api/export/coherence-csv', json=SAMPLE_PAYLOAD)

    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/csv')
    text = response.content.decode('utf-8-sig')
    assert '序列,频率,相干值' in text
    assert '主梁相干' in text
