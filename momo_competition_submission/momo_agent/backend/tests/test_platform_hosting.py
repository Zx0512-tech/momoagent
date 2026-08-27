from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.platform_hosting import install_platform_ui_routes


def test_platform_ui_routes_serve_spa_without_swallowing_api_404(tmp_path: Path) -> None:
    dist_dir = tmp_path / 'dist'
    dist_dir.mkdir()
    (dist_dir / 'index.html').write_text('<html><body>MOMO Dashboard</body></html>', encoding='utf-8')
    app = FastAPI()
    app.get('/api/health')(lambda: {'status': 'OK'})

    assert install_platform_ui_routes(app, dist_dir) is True

    client = TestClient(app)
    assert 'MOMO Dashboard' in client.get('/').text
    assert 'MOMO Dashboard' in client.get('/dashboard').text
    assert client.get('/api/health').json() == {'status': 'OK'}
    assert client.get('/api/missing').status_code == 404


def test_platform_ui_routes_keep_api_only_fallback_without_build(tmp_path: Path) -> None:
    app = FastAPI()

    assert install_platform_ui_routes(app, tmp_path / 'missing-dist') is False
    assert TestClient(app).get('/').status_code == 404
