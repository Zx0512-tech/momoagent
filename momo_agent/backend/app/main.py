from contextlib import asynccontextmanager
import os
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.api.v1.agent_router import router as agent_router
from app.api.v1.router import router as v1_router
from app.agents.tools import ToolExecutionError
from app.core.config import settings
from app.core.exceptions import LLMUnavailableError
from app.core.logging_config import configure_platform_logging, get_platform_logger
from app.platform_hosting import install_platform_ui_routes
from app.services.agent_repository import RunRevisionConflict
from app.services.platform_dispatcher import platform_dispatcher


configure_platform_logging()
request_logger = get_platform_logger("api")


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not os.environ.get('MOMO_LLM_BASE_URL') or not os.environ.get('MOMO_LLM_MODEL'):
        request_logger.warning(
            '未配置 MOMO_LLM_BASE_URL / MOMO_LLM_MODEL，智能体对话功能将不可用；平台页面（制品、任务队列）仍可正常使用。',
            extra={'event': 'llm_not_configured'},
        )
    platform_dispatcher.start()
    try:
        yield
    finally:
        platform_dispatcher.stop()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r'^http://(localhost|127\.0\.0\.1):\d+$',
    allow_methods=['*'],
    allow_headers=['*'],
)
app.include_router(router, prefix=settings.api_prefix)
app.include_router(v1_router, prefix=f"{settings.api_prefix}/v1")
app.include_router(agent_router, prefix=f"{settings.api_prefix}/v1")
PLATFORM_UI_DIST = Path(__file__).resolve().parents[3] / 'platform-ui' / 'dist'


@app.middleware("http")
async def structured_request_log(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid4().hex
    started = perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        request_logger.exception(
            "request failed",
            extra={
                "event": "http_request_failed",
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
            },
        )
        raise
    response.headers["X-Request-ID"] = request_id
    request_logger.info(
        "request completed",
        extra={
            "event": "http_request_completed",
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": round((perf_counter() - started) * 1000, 3),
        },
    )
    return response


def _is_v1_request(request: Request) -> bool:
    return request.url.path.startswith(f"{settings.api_prefix}/v1")


@app.exception_handler(HTTPException)
async def platform_http_exception_handler(request: Request, exc: HTTPException):
    if not _is_v1_request(request):
        return await http_exception_handler(request, exc)
    detail = exc.detail if isinstance(exc.detail, dict) else {'message': str(exc.detail)}
    error = {
        'code': detail.get('code') or ('NOT_FOUND' if exc.status_code == 404 else 'HTTP_ERROR'),
        'message': detail.get('message') or str(exc.detail),
        'details': detail.get('details'),
    }
    return JSONResponse(status_code=exc.status_code, content=jsonable_encoder({'error': error}), headers=exc.headers)


@app.exception_handler(ToolExecutionError)
async def tool_execution_exception_handler(request: Request, exc: ToolExecutionError):
    """工具/流程边界错误统一返回可消费的结构化 422。"""
    if not _is_v1_request(request):
        return JSONResponse(
            status_code=422,
            content=jsonable_encoder({'detail': exc.message}),
        )
    return JSONResponse(
        status_code=422,
        content=jsonable_encoder({
            'error': {
                'code': exc.code,
                'message': exc.message,
                'details': exc.details,
            },
        }),
    )


@app.exception_handler(RequestValidationError)
async def platform_validation_exception_handler(request: Request, exc: RequestValidationError):
    if not _is_v1_request(request):
        return await request_validation_exception_handler(request, exc)
    return JSONResponse(
        status_code=422,
        content=jsonable_encoder({
            'error': {
                'code': 'VALIDATION_ERROR',
                'message': '请求字段校验失败',
                'details': {'errors': exc.errors()},
            }
        }),
    )


@app.exception_handler(RunRevisionConflict)
async def run_revision_conflict_handler(request: Request, exc: RunRevisionConflict):
    """并发读-改-写冲突返回 409，绝不静默覆盖运行状态。"""
    return JSONResponse(
        status_code=409,
        content=jsonable_encoder({
            'error': {
                'code': 'RUN_REVISION_CONFLICT',
                'message': str(exc),
                'details': {
                    'runId': exc.run_id,
                    'expectedRevision': exc.expected,
                    'actualRevision': exc.actual,
                },
            },
        }),
    )


@app.exception_handler(LLMUnavailableError)
async def llm_unavailable_exception_handler(request: Request, exc: LLMUnavailableError):
    return JSONResponse(
        status_code=503,
        content={
            'code': 'LLM_UNAVAILABLE',
            'stage': exc.stage,
            'reason': exc.reason,
            'message': exc.user_message,
        },
    )


PLATFORM_UI_INSTALLED = install_platform_ui_routes(app, PLATFORM_UI_DIST)

if not PLATFORM_UI_INSTALLED:
    @app.get("/", include_in_schema=False)
    def index() -> dict[str, str]:
        return {
            "status": "ok",
            "service": settings.app_name,
            "mode": "api-only",
            "api_prefix": settings.api_prefix,
            "frontend_contract": "docs/frontend_api_contract.md",
            "platform_scope": "docs/platform_capability_scope.md",
        }
