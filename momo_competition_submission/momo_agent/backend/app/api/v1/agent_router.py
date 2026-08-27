import json
import os
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from typing import Any, Iterator

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from app.api.v1.agent_schemas import (
    AgentMessageCreateRequest,
    AgentSessionCreateRequest,
    ApprovalDecisionRequest,
    ApprovalUpdateRequest,
    LoadMappingRequest,
)
from app.services.agent_service import agent_service
from app.services.load_artifact_service import load_artifact_service


router = APIRouter()

# 流式消息用有界执行器承载：原先每个请求裸起一个线程，
# 并发轰击时线程数不受控；池满时任务排队而不是无限扩张。
_MESSAGE_STREAM_EXECUTOR = ThreadPoolExecutor(
    max_workers=max(int(os.getenv('MOMO_AGENT_STREAM_WORKERS', '8')), 1),
    thread_name_prefix='agent-message',
)


@router.post('/agent/sessions')
def create_agent_session(payload: AgentSessionCreateRequest) -> dict[str, Any]:
    return agent_service.create_session(payload.title)


@router.get('/agent/sessions')
def list_agent_sessions() -> dict[str, Any]:
    return {'data': agent_service.list_sessions()}


@router.get('/agent/sessions/{session_id}')
def get_agent_session(session_id: str) -> dict[str, Any]:
    return agent_service.get_session(session_id)


@router.delete('/agent/sessions/{session_id}')
def delete_agent_session(session_id: str) -> dict[str, Any]:
    return agent_service.delete_session(session_id)


@router.post('/agent/sessions/{session_id}/messages')
def create_agent_message(session_id: str, payload: AgentMessageCreateRequest) -> dict[str, Any]:
    return agent_service.create_message(session_id, payload.content, payload.file_id, payload.task_type)


def stream_agent_message_events(
    session_id: str,
    payload: AgentMessageCreateRequest,
) -> Iterator[str]:
    """用 NDJSON 渐进返回运行快照；同步消息接口保持兼容。"""
    events: Queue[dict[str, Any] | object] = Queue()
    completed = object()

    def emit(event: dict[str, Any]) -> None:
        events.put(event)

    def execute() -> None:
        try:
            run = agent_service.create_message(
                session_id,
                payload.content,
                payload.file_id,
                payload.task_type,
                event_sink=emit,
            )
            emit({'type': 'complete', 'run': run})
        except Exception as exc:
            emit({
                'type': 'error',
                'error': {
                    'code': 'MESSAGE_STREAM_FAILED',
                    'message': getattr(exc, 'user_message', None) or '结果查询未完成，请稍后重试。',
                },
            })
        finally:
            events.put(completed)

    yield json.dumps({'type': 'accepted', 'sessionId': session_id}, ensure_ascii=False) + '\n'
    _MESSAGE_STREAM_EXECUTOR.submit(execute)
    while True:
        event = events.get()
        if event is completed:
            break
        yield json.dumps(event, ensure_ascii=False, separators=(',', ':')) + '\n'


@router.post('/agent/sessions/{session_id}/messages/stream')
def stream_agent_message(session_id: str, payload: AgentMessageCreateRequest) -> StreamingResponse:
    return StreamingResponse(
        stream_agent_message_events(session_id, payload),
        media_type='application/x-ndjson',
        headers={'X-Accel-Buffering': 'no', 'Cache-Control': 'no-store'},
    )


@router.get('/agent/runs/{run_id}')
def get_agent_run(run_id: str) -> dict[str, Any]:
    return agent_service.get_run(run_id)


@router.get('/agent/runs/{run_id}/timeseries')
def get_agent_run_timeseries(
    run_id: str,
    columns: str = Query(default='', max_length=400),
    max_points: int = Query(default=1000, ge=50, le=5000, alias='maxPoints'),
) -> dict[str, Any]:
    selected_columns = [item.strip() for item in columns.split(',') if item.strip()]
    return agent_service.get_run_timeseries(
        run_id,
        columns=selected_columns,
        max_points=max_points,
    )


@router.get('/agent/runs/{run_id}/timeseries/compare')
def get_agent_run_timeseries_comparison(
    run_id: str,
    columns: str = Query(default='', max_length=400),
    case_ids: str = Query(default='', max_length=400, alias='caseIds'),
    max_points: int = Query(default=1000, ge=50, le=5000, alias='maxPoints'),
) -> dict[str, Any]:
    return agent_service.get_run_timeseries_comparison(
        run_id,
        columns=[item.strip() for item in columns.split(',') if item.strip()],
        case_ids=[item.strip() for item in case_ids.split(',') if item.strip()],
        max_points=max_points,
    )


@router.post('/agent/runs/{run_id}/cancel')
def cancel_agent_run(run_id: str) -> dict[str, Any]:
    return agent_service.cancel_run(run_id)


@router.post('/agent/approvals/{approval_id}/decision')
def decide_agent_approval(approval_id: str, payload: ApprovalDecisionRequest) -> dict[str, Any]:
    return agent_service.decide_approval(approval_id, payload.approved)


@router.patch('/agent/runs/{run_id}/approval')
def update_agent_approval(run_id: str, payload: ApprovalUpdateRequest) -> dict[str, Any]:
    return agent_service.update_approval(run_id, payload.model_dump(exclude_none=True, by_alias=True))


@router.post(
    '/load-files',
    summary='上传智能体载荷文件',
    description=(
        '原始字节保持不变并登记 SHA-256；CSV/TXT 按 UTF-8 BOM、UTF-8、GB18030 顺序识别文本编码，'
        '响应 inspection 返回实际编码和分隔符。'
    ),
)
async def upload_load_file(
    request: Request,
    response: Response,
    file_name: str = Query(alias='fileName', min_length=1, max_length=255),
) -> dict[str, Any]:
    content = await load_artifact_service.read_upload(request)
    response.status_code = 201
    return agent_service.upload_file(file_name, content)


@router.get('/load-imports/{import_id}')
def get_load_import(import_id: str) -> dict[str, Any]:
    return agent_service.get_import(import_id)


@router.post('/load-imports/{import_id}/mapping')
def set_load_mapping(import_id: str, payload: LoadMappingRequest) -> dict[str, Any]:
    return agent_service.set_mapping(import_id, payload.to_mapping())
