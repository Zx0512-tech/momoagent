# Session Context and History Lifecycle

## 1. Scope / Trigger

Use this contract when changing Agent message persistence, LLM history projection, session switching, or conversation-history deletion. A session is the isolation boundary for LLM context; engineering runs and their evidence are retained independently for audit.

## 2. Signatures

```python
AgentRepository.list_messages(session_id: str) -> list[dict[str, Any]]
WorkflowHarnessMixin._history_for_harness_turn(repository, session_id: str) -> list[dict[str, Any]]
AgentService.delete_session(session_id: str) -> dict[str, Any]
AgentRepository.delete_session_history(session_id: str) -> bool
```

```http
DELETE /api/v1/agent/sessions/{sessionId}
```

```typescript
agentApi.deleteSession(sessionId: string): Promise<AgentSessionDeleteResult>
useChatStore.getState().deleteSession(sessionId: string): Promise<void>
```

## 3. Contracts

- Every Harness LLM turn loads messages only for the current `sessionId`, then applies the bounded-history projection before calling the planner.
- Native assistant `tool_calls` and matching `tool` results remain persisted for cross-request and process-restart continuity, but are hidden from the ordinary chat transcript.
- History projection is bounded and must not leave orphan `tool` messages; large tool results are compacted rather than copied verbatim.
- Deletion removes the `agent_sessions` row and that session's `agent_messages` only. `agent_runs`, approvals, steps, tool calls, Jobs, and Artifacts remain available as engineering audit evidence.
- Deleting a visible session first cancels every non-terminal run, then removes the session/messages. The terminal set is `SUCCEEDED`, `COMPLETED_DIAGNOSTIC`, `FAILED`, `CANCELLED`, and `UNSUPPORTED`; unknown statuses are treated as non-terminal and must be cancelled before deletion.
- A stale retained run may reference a Job that no longer exists. A 404 while cancelling that Job must not keep the visible session undeletable; the run is still moved to `CANCELLED` and retained for audit. Other cancellation failures fail closed and leave the visible session intact.
- Deleting the active UI session clears its visible messages and selected run. Deleting another session must preserve the active session and all of its visible context.
- FastAPI builds its route table when the process starts. After adding or changing an HTTP method, restart the running backend and verify the live `/openapi.json`; rebuilding the frontend alone cannot activate a backend route.
- The response is JSON because the shared frontend request helper parses every successful response body:

```json
{"sessionId":"ags_...","deleted":true,"retainedRunCount":1,"cancelledRunCount":1}
```

## 4. Validation & Error Matrix

| Condition | Required behavior |
| --- | --- |
| Session does not exist | 404 `SESSION_NOT_FOUND` |
| Session has a non-terminal or unknown-status run | Cancel the run, retain its audit evidence, then delete visible session/messages |
| Retained run references a missing Job | Ignore only the Job 404, mark run `CANCELLED`, continue deletion |
| Run/Job cancellation fails for another reason | Propagate failure; keep visible session/messages |
| Terminal or empty session | Delete visible session/messages and return retained/cancelled counts |
| Delete active UI session succeeds | Remove list entry and clear active context |
| Delete inactive UI session succeeds | Remove only that list entry; preserve active context |
| API deletion fails | Keep local session/context and expose `删除会话失败` |
| Live endpoint returns 405 while source declares `DELETE` | Compare live OpenAPI methods; restart the stale backend process before changing application code |

## 5. Good / Base / Bad Cases

- Good: a completed test conversation is deleted from the sidebar while its run and tool-call trace remain queryable by run ID.
- Base: an empty conversation is deleted and returns `retainedRunCount=0`, `cancelledRunCount=0`.
- Bad: deleting a session cascades into solver Jobs or Artifacts, or frontend state removes a session before the server confirms success.
- Bad: treating a stale process's 405 as a client payload error and adding frontend retries or alternate HTTP methods.

## 6. Tests Required

- Repository test: delete a terminal session, assert session/messages are absent and run/tool-call evidence remains.
- Service/API test: active run becomes `CANCELLED` and visible history is deleted; missing session returns 404; empty session returns the exact JSON response including `cancelledRunCount=0`.
- Harness history tests: same-session messages and native tool protocol survive a new request/restart; different sessions never appear in the projected history.
- Frontend store tests: deleting the active session clears context; deleting an inactive session preserves active messages and run.
- Production TypeScript build must cover the sidebar delete button, confirmation copy, and accessible name.
- Runtime smoke: live OpenAPI lists both `GET` and `DELETE`; create an empty temporary session, delete it, then assert a subsequent `GET` returns 404.

## 7. Wrong vs Correct

```python
# Wrong: history is global and deletion destroys engineering evidence.
messages = repository.list_all_messages()
repository.delete_runs(session_id)

# Correct: cancel non-terminal runs, then remove session-scoped visible history; evidence is retained.
messages = repository.list_messages(session_id)
for run in non_terminal_runs:
    service.cancel_run(run['runId'])
repository.delete_session_history(session_id)
```

```powershell
# Wrong: only rebuild the UI while an old backend still owns port 8000.
npm.cmd run build

# Correct: restart through the project launcher, then verify the live route table.
.\start.ps1 -NoBrowser
(Invoke-RestMethod http://127.0.0.1:8000/openapi.json).paths.'/api/v1/agent/sessions/{session_id}'
```
