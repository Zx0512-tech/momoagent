# Report and Refresh Safety

## Scenario: Agent report persistence and terminal-job refresh

### 1. Scope / Trigger

This contract applies whenever `AgentService` serializes an agent report or refreshes a run after its platform Job reaches a terminal state. It exists because Python's default JSON encoder accepts non-finite floats, concurrent polls can otherwise overwrite newly registered artifacts, and report/review failures must not be replayed on every `get_run()` poll.

### 2. Signatures

```python
def _coerce_floats(value: Any) -> Any: ...

def _json_artifact_payload(value: Any) -> tuple[Any, bytes]: ...

def _refresh_agent_run(
    repository: AgentRepository,
    run: dict[str, Any],
) -> dict[str, Any]: ...

def _recover_persisted_agent_report(
    repository: AgentRepository,
    run: dict[str, Any],
) -> dict[str, Any] | None: ...

@contextmanager
def PlatformStore.state_transaction() -> Iterator[None]: ...

def PlatformStore.find_artifact_for_run(
    run_id: str,
    name: str,
) -> ArtifactRecord | None: ...
```
### 3. Contracts

- `_coerce_floats` recursively preserves finite floats and converts `nan`, `inf`, and `-inf` to `None` in dictionaries, lists, and tuples.
- `_json_artifact_payload` returns the same sanitized value used for both `ArtifactRecord.preview` and UTF-8 JSON bytes. Serialization must use `allow_nan=False`.
- Platform JSON previews, generated JSON files, and SQLite JSON columns use
  `app.core.json_safety.strict_json_dumps` so the same non-finite normalization
  applies outside Agent reports.
- A refresh failure is recorded as:

  ```json
  {
    "status": "FAILED",
    "currentStage": "FAILED",
    "currentStep": "FAILED",
    "workflowGateError": {
      "code": "REPORT_GENERATION_ERROR",
      "message": "结果验收或报告生成失败，运行已安全终止。",
      "details": {"stage": "REPORT_GENERATION", "exceptionType": "..."}
    }
  }
  ```

- Existing `completedSteps`, Job references, and Artifact references remain unchanged when refresh fails.
- Terminal refresh, report registration, Artifact lookup, and run association execute inside one `PlatformStore.state_transaction()` guarded by a reentrant lock. A concurrent `refresh()` cannot replace the in-memory Artifact collection between those operations.
- A run with `workflowGateError.code == REPORT_GENERATION_ERROR` must never replay Job review, report build, or report registration. A subsequent read may perform only an idempotent reconciliation against an already registered report Artifact with the exact run-specific name.
- Reconciliation is allowed only when the Artifact preview matches `agentRunId`, `taskType`, and `jobId`, declares a terminal `jobStatus`, has `isFinalResult == true`, contains a `checks` object, and agrees with the current Job status. A matching report is associated with the original run and advances that run to its real terminal workflow state.
- If no exact matching report exists, or any validation fails, the saved `FAILED` run is returned unchanged.
- The exception message and stack trace are never returned in the run payload. The stack trace is written only to the server logger.
- If `repository.save_run()` fails while persisting the failure, that infrastructure error is allowed to propagate to the API error boundary; the service must not claim that the failure was persisted.

### 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| Finite float in a report | Preserve the original value in preview and content |
| `nan`, `inf`, or `-inf` | Store JSON `null` in preview and content |
| Review/build/register raises | Persist `FAILED` with `REPORT_GENERATION_ERROR` |
| Already marked `REPORT_GENERATION_ERROR`, exact valid report exists | Reconcile the existing Artifact and original run without replaying report generation |
| Already marked `REPORT_GENERATION_ERROR`, report missing or invalid | Return the saved failure without replaying any side effect |
| Two terminal polls overlap | Serialize the PlatformStore terminal transition; both reads converge on one registered report |
| Failure details contain a local path or secret | Reject that detail; expose only stage and exception type |
| Failure persistence raises | Propagate the persistence exception |

### 5. Good / Base / Bad Cases

- Good: `{"drift": float("nan")}` is registered as preview `{"drift": None}` and strict JSON `{"drift": null}`.
- Good: the report was durably registered before a transient association lookup failed; the next read validates and links that same Artifact to the original run.
- Base: a normal finite report is byte-for-byte represented by the same sanitized object used in the preview.
- Bad: calling `json.dumps(report)` with the default `allow_nan=True`, putting `str(exc)` into `workflowGateError`, or regenerating a report after `REPORT_GENERATION_ERROR` creates invalid output, leaks details, or duplicates side effects.

### 6. Tests Required

- Serialize nested non-finite values and assert Python strict parsing plus real Node `JSON.parse` succeed.
- Assert preview and downloaded content both contain `None`/`null`, while finite numbers, strings, booleans, integers, and list order are unchanged.
- Parameterize review, report-build, and artifact-register failures; assert `get_run()` returns `FAILED`, preserves progress, and does not leak exception text.
- Read the same failed run twice and assert the injected side effect is called once.
- Hold `state_transaction()` in one thread and assert a concurrent `refresh()` cannot complete until the transaction exits.
- Persist a run-specific report, inject a post-registration lookup failure, then assert the next read recovers the original run and reuses the same `reportArtifactId` without rebuilding or registering a second report.
- Persist an absent or mismatched report and assert the saved `REPORT_GENERATION_ERROR` remains unchanged.
- Assert a persistence failure from `save_run()` is still raised.

### 7. Wrong vs Correct

#### Wrong

```python
content = json.dumps(report).encode("utf-8")
run["workflowGateError"] = {"message": str(exc)}
```

#### Correct

```python
with platform_store.state_transaction():
    safe_report, content = _json_artifact_payload(report)
    artifact = register_artifact(preview=safe_report, content=content)
    run["reportArtifactId"] = artifact.artifact_id

# 已失败的读取只允许校验并关联既有制品，不重放报告生成。
recovered = _recover_persisted_agent_report(repository, run)
return recovered or run
```
