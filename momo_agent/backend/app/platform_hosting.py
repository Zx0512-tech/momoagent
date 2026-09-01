from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse


def install_platform_ui_routes(app: FastAPI, dist_dir: Path) -> bool:
    root = dist_dir.resolve()
    index_file = root / 'index.html'
    if not index_file.is_file():
        return False

    @app.get('/', include_in_schema=False)
    def platform_index() -> FileResponse:
        return FileResponse(index_file)

    @app.get('/{full_path:path}', include_in_schema=False)
    def platform_spa(full_path: str) -> FileResponse:
        if full_path == 'api' or full_path.startswith('api/'):
            raise HTTPException(status_code=404, detail='Not Found')
        candidate = (root / full_path).resolve()
        if candidate.is_relative_to(root) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index_file)

    return True
