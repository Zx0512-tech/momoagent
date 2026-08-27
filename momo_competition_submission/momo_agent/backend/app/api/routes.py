from urllib.parse import quote

from fastapi import APIRouter, Response
from fastapi.encoders import jsonable_encoder

from app.api.schemas import WindRequest
from app.services.exporter import (
    build_coherence_csv_bytes,
    build_excel_bytes,
    build_girder_all_points_csv_bytes,
    build_girder_csv_bytes,
    build_spectrum_csv_bytes,
    build_tower_all_points_csv_bytes,
    build_tower_csv_bytes,
    build_unified_wind_csv_bytes,
)
from app.services.workflow import run_full_workflow

router = APIRouter()



def build_download_headers(filename: str) -> dict[str, str]:
    return {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
    }


@router.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/workflow")
def run_workflow(payload: WindRequest) -> dict:
    return jsonable_encoder(run_full_workflow(payload))


@router.post("/export/excel")
def export_excel(payload: WindRequest) -> Response:
    result = run_full_workflow(payload)
    filename = f"{payload.project.name or 'bridge-wind'}-wind-results.xlsx"
    return Response(
        content=build_excel_bytes(result),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=build_download_headers(filename),
    )


@router.post("/export/girder-csv")
def export_girder_csv(payload: WindRequest) -> Response:
    result = run_full_workflow(payload)
    filename = f"{payload.project.name or 'bridge-wind'}-girder-velocity.csv"
    return Response(
        content=build_girder_csv_bytes(result),
        media_type="text/csv; charset=utf-8",
        headers=build_download_headers(filename),
    )


@router.post("/export/unified-wind-csv")
def export_unified_wind_csv(payload: WindRequest) -> Response:
    result = run_full_workflow(payload)
    filename = f"{payload.project.name or 'bridge-wind'}-unified-wind-load.csv"
    return Response(
        content=build_unified_wind_csv_bytes(result),
        media_type="text/csv; charset=utf-8",
        headers=build_download_headers(filename),
    )


@router.post("/export/tower-csv")
def export_tower_csv(payload: WindRequest) -> Response:
    result = run_full_workflow(payload)
    filename = f"{payload.project.name or 'bridge-wind'}-tower-load.csv"
    return Response(
        content=build_tower_csv_bytes(result),
        media_type="text/csv; charset=utf-8",
        headers=build_download_headers(filename),
    )


@router.post("/export/girder-all-points-csv")
def export_girder_all_points_csv(payload: WindRequest) -> Response:
    result = run_full_workflow(payload)
    filename = f"{payload.project.name or 'bridge-wind'}-girder-all-points.csv"
    return Response(
        content=build_girder_all_points_csv_bytes(result),
        media_type="text/csv; charset=utf-8",
        headers=build_download_headers(filename),
    )


@router.post("/export/tower-all-points-csv")
def export_tower_all_points_csv(payload: WindRequest) -> Response:
    result = run_full_workflow(payload)
    filename = f"{payload.project.name or 'bridge-wind'}-tower-all-points.csv"
    return Response(
        content=build_tower_all_points_csv_bytes(result),
        media_type="text/csv; charset=utf-8",
        headers=build_download_headers(filename),
    )


@router.post("/export/spectrum-csv")
def export_spectrum_csv(payload: WindRequest) -> Response:
    result = run_full_workflow(payload)
    filename = f"{payload.project.name or 'bridge-wind'}-spectrum.csv"
    return Response(
        content=build_spectrum_csv_bytes(result),
        media_type="text/csv; charset=utf-8",
        headers=build_download_headers(filename),
    )


@router.post("/export/coherence-csv")
def export_coherence_csv(payload: WindRequest) -> Response:
    result = run_full_workflow(payload)
    filename = f"{payload.project.name or 'bridge-wind'}-coherence.csv"
    return Response(
        content=build_coherence_csv_bytes(result),
        media_type="text/csv; charset=utf-8",
        headers=build_download_headers(filename),
    )
