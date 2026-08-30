from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app.api.schemas import WindRequest
from app.core.config import settings
from app.api.v1.schemas import (
    ActiveLearningInfillRequest,
    CommandStreamAssembleRequest,
    EarthquakeLoadRequest,
    EngineeringConfigRequest,
    EntropyTopsisDecisionRequest,
    ExperimentDesignRequest,
    JobCreateRequest,
    JobStatus,
    JobType,
    LoadCurveExportRequest,
    MultiObjectiveOptimizationRequest,
    OptimizationExportRequest,
    PreflightRequest,
    ResultExtractionRequest,
    SolverRunRequest,
    SurrogateTrainingRequest,
    TrafficLoadRequest,
    WindExportRequest,
    WindLoadRequest,
    validate_job_params,
)
from app.services.exporter import build_unified_wind_csv_bytes
from app.services.platform_dispatcher import platform_dispatcher
from app.services.platform_readiness import build_readiness_report
from app.services.platform_store import platform_execution_mode, platform_store, utc_now
from app.services.load_artifact_service import load_artifact_service
from app.services.model_import_service import model_import_service
from app.services.real_execution import real_execution_registry
from app.services.workflow import run_full_workflow


router = APIRouter()


@router.get('/health')
def health_check() -> dict[str, str]:
    return {'status': 'OK', 'service': 'momo-platform-api-live', 'version': settings.version, 'now': utc_now()}


@router.get('/readiness')
def readiness_check(response: Response) -> dict[str, Any]:
    report = build_readiness_report(platform_store, platform_dispatcher)
    if report['status'] != 'READY':
        response.status_code = 503
    return report


@router.get('/capabilities')
def get_capabilities() -> dict[str, Any]:
    """返回 Agent、平台 API 和 Live 前端共用的能力目录。"""
    return jsonable_encoder(real_execution_registry.catalog(), by_alias=True)


@router.get('/dashboard/summary')
def dashboard_summary() -> dict[str, Any]:
    return platform_store.dashboard_summary()


@router.get('/templates')
def get_templates(
    solver: str | None = None,
    workflow: str | None = None,
    includeLegacy: bool = False,
) -> dict[str, Any]:
    templates = platform_store.templates()
    if solver:
        templates = [item for item in templates if item['solver'] == solver]
    if workflow:
        templates = [item for item in templates if item['workflow'] == workflow]
    if not includeLegacy:
        templates = [item for item in templates if not item['isLegacy']]
    return {'data': templates, 'pagination': {'page': 1, 'pageSize': 50, 'totalItems': len(templates), 'totalPages': 1}}


@router.get('/templates/{template_id}')
def get_template(template_id: str) -> dict[str, Any]:
    for template in platform_store.templates():
        if template['templateId'] == template_id:
            return template
    from fastapi import HTTPException

    raise HTTPException(status_code=404, detail={'code': 'NOT_FOUND', 'message': f'模板 {template_id} 不存在'})


@router.post('/preflights')
def run_preflight(payload: PreflightRequest) -> dict[str, Any]:
    return platform_store.preflight(payload.config_path)


@router.get('/jobs')
def get_jobs(
    status: JobStatus | None = None,
    type: JobType | None = None,
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1),
) -> dict[str, Any]:
    platform_store.refresh()
    return jsonable_encoder(platform_store.list_jobs(status, type, page, pageSize), by_alias=True)


@router.post('/jobs')
def create_job(payload: JobCreateRequest, response: Response) -> dict[str, Any]:
    try:
        params = validate_job_params(payload.type, payload.params)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc
    job = platform_store.create_job(payload.type, params)
    if job.status == 'QUEUED':
        response.status_code = 202
    return jsonable_encoder(job, by_alias=True)


@router.get('/jobs/{job_id}')
def get_job(job_id: str) -> dict[str, Any]:
    platform_store.refresh()
    return jsonable_encoder(platform_store.get_job(job_id), by_alias=True)


@router.post('/jobs/{job_id}/cancel')
def cancel_job(job_id: str) -> dict[str, bool]:
    return platform_dispatcher.cancel_job(job_id)


@router.get('/artifacts')
def get_artifacts(
    jobId: str | None = None,
    kind: str | None = None,
    source: str | None = None,
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1),
) -> dict[str, Any]:
    platform_store.refresh()
    return jsonable_encoder(platform_store.list_artifacts(jobId, kind, page, pageSize, source=source), by_alias=True)


@router.post(
    '/artifacts/uploads',
    status_code=201,
    summary='上传并检查载荷制品',
    description=(
        '原始字节保持不变并登记 SHA-256；CSV/TXT 按 UTF-8 BOM、UTF-8、GB18030 顺序识别文本编码，'
        'inspection 返回实际编码和分隔符。'
    ),
)
async def upload_load_artifact(
    request: Request,
    file_name: str = Query(alias='fileName', min_length=1, max_length=255),
) -> dict[str, Any]:
    content = await load_artifact_service.read_upload(request)
    return load_artifact_service.upload(file_name, content)


@router.post(
    '/models/uploads',
    status_code=201,
    summary='上传并登记用户有限元模型',
    description=(
        '仅接受 ANSYS APDL 文本模型；上传时做禁用命令安全扫描和结构统计，'
        '原始字节保持不变并登记 SHA-256。登记后可在对话中以制品 ID 引用该模型创建受控分析（仅 ANSYS 求解器）。'
    ),
)
async def upload_fem_model(
    request: Request,
    file_name: str = Query(alias='fileName', min_length=1, max_length=255),
) -> dict[str, Any]:
    content = await _read_model_upload(request)
    return model_import_service.upload(file_name, content)


async def _read_model_upload(request: Request) -> bytes:
    from app.services.model_import_service import MAX_MODEL_BYTES

    content_length = request.headers.get('content-length')
    if content_length and content_length.isdigit() and int(content_length) > MAX_MODEL_BYTES:
        raise HTTPException(status_code=422, detail={'code': 'FILE_TOO_LARGE', 'message': '模型文件上限为 50 MB'})
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > MAX_MODEL_BYTES:
            raise HTTPException(status_code=422, detail={'code': 'FILE_TOO_LARGE', 'message': '模型文件上限为 50 MB'})
        content.extend(chunk)
    return bytes(content)


@router.get('/models', summary='列出已登记的用户有限元模型')
def list_fem_models() -> dict[str, Any]:
    platform_store.refresh()
    return {'models': model_import_service.list_models()}


@router.post('/artifacts/sync-real-optimizations')
def sync_real_optimization_history() -> dict[str, Any]:
    platform_store.refresh()
    return platform_store.sync_real_optimization_history()


@router.get('/artifacts/{artifact_id}')
def get_artifact(artifact_id: str) -> dict[str, Any]:
    return jsonable_encoder(platform_store.get_artifact(artifact_id).artifact, by_alias=True)


@router.get('/artifacts/{artifact_id}/preview')
def get_artifact_preview(artifact_id: str) -> Any:
    return platform_store.get_artifact(artifact_id).preview


@router.get('/artifacts/{artifact_id}/download')
def download_artifact(artifact_id: str) -> Response:
    record = platform_store.get_artifact(artifact_id)
    filename = quote(str(record.artifact.name or artifact_id), safe='')
    return Response(
        content=record.content,
        media_type=record.artifact.mime_type,
        headers={'Content-Disposition': f"attachment; filename*=UTF-8''{filename}"},
    )


@router.get('/reports/parity/latest')
def latest_parity_report() -> dict[str, Any]:
    return platform_store.latest_parity_report()


@router.get('/reports/status')
def status_reports() -> dict[str, Any]:
    return jsonable_encoder(platform_store.list_artifacts(None, 'STATUS_REPORT', 1, 20), by_alias=True)


@router.get('/engineering-config')
def get_engineering_config() -> dict[str, Any] | None:
    return platform_store.get_engineering_config()


@router.put('/engineering-config')
def save_engineering_config(payload: EngineeringConfigRequest) -> dict[str, Any]:
    return platform_store.save_engineering_config(payload.to_platform_config())


@router.post('/engineering-config/validate')
def validate_engineering_config(payload: EngineeringConfigRequest) -> dict[str, Any]:
    return platform_store.validate_engineering_config(payload.to_platform_config())


@router.get('/engineering-config/dampers/registry')
def get_damper_registry() -> list[dict[str, Any]]:
    return platform_store.damper_registry()


@router.get('/engineering-config/result-extraction/default-metrics')
def get_default_result_metrics() -> list[dict[str, Any]]:
    return platform_store.default_result_metrics()


def create_platform_job(job_type: JobType, params: dict[str, Any]) -> dict[str, Any]:
    return jsonable_encoder(platform_store.create_job(job_type, params), by_alias=True)


@router.post('/load-cases/traffic/random')
def traffic_load(payload: TrafficLoadRequest) -> dict[str, Any]:
    return create_platform_job('LOAD_TRAFFIC_RANDOM', payload.model_dump(by_alias=True, exclude_unset=True))


@router.post('/load-cases/wind/vertical')
def wind_load(payload: WindLoadRequest) -> dict[str, Any]:
    return create_platform_job('LOAD_WIND_VERTICAL', payload.model_dump(by_alias=True, exclude_unset=True))


@router.post('/load-cases/earthquake')
def earthquake_load(payload: EarthquakeLoadRequest) -> dict[str, Any]:
    return create_platform_job('LOAD_EARTHQUAKE', payload.model_dump(by_alias=True, exclude_unset=True))


@router.post('/load-curves/export')
def export_load_curve(payload: LoadCurveExportRequest) -> dict[str, Any]:
    return create_platform_job('LOAD_CURVE_EXPORT', payload.model_dump(by_alias=True, exclude_unset=True))


@router.post('/command-streams/assemble')
def assemble_command_stream(payload: CommandStreamAssembleRequest) -> dict[str, Any]:
    return create_platform_job('COMMAND_STREAM_ASSEMBLY', payload.model_dump(by_alias=True, exclude_unset=True))


@router.post('/solver-runs')
def solver_runs(payload: SolverRunRequest, response: Response) -> dict[str, Any]:
    job = platform_store.create_job('SOLVER_BATCH', payload.model_dump(by_alias=True, exclude_unset=True))
    response.status_code = 202
    return jsonable_encoder(job, by_alias=True)


@router.post('/result-extractions')
def result_extractions(payload: ResultExtractionRequest) -> dict[str, Any]:
    return create_platform_job('RESULT_EXTRACTION', payload.model_dump(by_alias=True, exclude_unset=True))


@router.post('/experiment-designs')
def experiment_designs(payload: ExperimentDesignRequest) -> dict[str, Any]:
    return create_platform_job('EXPERIMENT_DESIGN', payload.model_dump(by_alias=True, exclude_unset=True))


@router.post('/surrogates/train')
def train_surrogate(payload: SurrogateTrainingRequest) -> dict[str, Any]:
    return create_platform_job('SURROGATE_TRAINING', payload.model_dump(by_alias=True, exclude_unset=True))


@router.post('/active-learning/infill')
def active_learning(payload: ActiveLearningInfillRequest) -> dict[str, Any]:
    return create_platform_job('ACTIVE_LEARNING', payload.model_dump(by_alias=True, exclude_unset=True))


@router.post('/optimizations/multi-objective')
def multi_objective_optimization(payload: MultiObjectiveOptimizationRequest) -> dict[str, Any]:
    return create_platform_job('MULTI_OBJECTIVE_OPTIMIZATION', payload.model_dump(by_alias=True, exclude_unset=True))


@router.post('/decisions/entropy-topsis')
def entropy_topsis(payload: EntropyTopsisDecisionRequest) -> dict[str, Any]:
    return create_platform_job('ENTROPY_TOPSIS_DECISION', payload.model_dump(by_alias=True, exclude_unset=True))


@router.post('/optimizations/export')
def export_optimization(payload: OptimizationExportRequest) -> dict[str, Any]:
    return create_platform_job('OPTIMIZATION_EXPORT', payload.model_dump(by_alias=True, exclude_unset=True))


@router.get('/optimizations/{optimization_run_id}/topsis')
def get_topsis_result(optimization_run_id: str) -> dict[str, Any]:
    real_result = platform_store.real_topsis_result(optimization_run_id)
    if real_result is not None:
        return real_result
    capability = real_execution_registry.resolve('ENTROPY_TOPSIS_DECISION')
    if platform_execution_mode() == 'LIVE' and capability.status != 'LIVE':
        raise HTTPException(
            status_code=501,
            detail={
                'code': 'CAPABILITY_NOT_IMPLEMENTED',
                'message': capability.reason,
                'details': {
                    'jobType': 'ENTROPY_TOPSIS_DECISION',
                    'status': capability.status,
                    'unlockRequirements': list(capability.unlock_requirements),
                },
            },
        )
    return {
        'optimizationRunId': optimization_run_id,
        'executionMode': 'MOCK',
        'simulation': True,
        **platform_store.topsis_result(),
    }


@router.post('/wind/workflows')
def wind_workflow(payload: WindRequest) -> dict[str, Any]:
    result = run_full_workflow(payload)
    job = platform_store.create_job('LOAD_WIND_VERTICAL', {'source': 'WIND_WORKFLOW'})
    job.result = {'summary': result.get('summary', {}), 'mode': 'wind_workflow'}
    platform_store.persist()
    return jsonable_encoder(job, by_alias=True)


@router.post('/wind/exports')
def wind_export(payload: WindExportRequest) -> dict[str, Any]:
    content = build_unified_wind_csv_bytes(run_full_workflow(payload.request))
    artifact = platform_store.register_artifact(
        kind='LOAD_CASE',
        name=f'wind_export_{payload.format.lower()}.csv',
        path='output/load_cases/wind_export.csv',
        mime_type='text/csv',
        preview={'headers': ['time_s', 'value'], 'previewRows': [['0.0', '0.0']], 'totalRows': 1},
        content=content,
    )
    return jsonable_encoder(artifact, by_alias=True)
