# Real Execution Capability Catalog

## Scenario: Shared live/mock capability gating

### 1. Scope / Trigger

This contract applies when Agent workflows, platform Job APIs, or the Live frontend decide whether a solver, DOE, surrogate, active-learning, or optimization operation may run. The catalog is the single source for status and unlock requirements; a UI label must never be treated as execution authorization.

### 2. Signatures

```python
real_execution_registry.catalog() -> CapabilityCatalog
real_execution_registry.resolve(
    job_type: str,
    params: Mapping[str, Any] | None = None,
) -> CapabilityDescriptor
real_execution_registry.is_live(
    job_type: str,
    params: Mapping[str, Any] | None = None,
) -> bool
```
HTTP:

```text
GET /api/v1/capabilities -> CapabilityCatalog
```

### 3. Contracts

- `CapabilityDescriptor` fields are `jobType`, `mode`, `status`, `handler`, `solvers`, `scenarios`, `solverScenarios`, `inputArtifacts`, `outputArtifacts`, `supportsCancel`, `supportsResume`, `reason`, and `unlockRequirements`.
- `scenarios` is the union of advertised load kinds. `solverScenarios` is the per-solver truth: a combination is advertised only when `CapabilityDescriptor.supports(solver=..., scenario=...)` is true. An empty `solverScenarios` map means every listed solver shares the same `scenarios` tuple.
- `status=LIVE` means the corresponding handler is allowed to create/execute production work. `MOCK_ONLY` means only an explicitly marked mock client may demonstrate it. `DISABLED` means the API must fail closed before creating a Job or Artifact. A `LIVE` entry must not advertise a solver × scenario that cannot execute end to end.
- Controlled Agent `ANALYSIS` and `SOLVER_BATCH` advertise `EARTHQUAKE + WIND + TRAFFIC` on both `ANSYS` and `OPENSEESPY_INPROC`: each combination has a registered baseline template. Combined WIND+TRAFFIC stays hidden and fails closed.
- Traffic is a moving load and does not reuse the wind single-channel contract. Wind collapses a spatial field into one total force column and splits it by equal weight over 8 girder nodes; traffic keeps 163 per-node time histories, because the load's spatial distribution changing over time *is* its physical characteristic. So a traffic load artifact is a pair: a dense matrix (`time_s` + 163 node columns, `applicationType=NODAL_FORCE_MATRIX`) plus a per-node mapping artifact declaring `node_id -> source_column`. Both SHAs are approval-frozen; a matrix without its mapping fails closed on `TRAFFIC_LOAD_POINT_MAPPING_REQUIRED`, because the solver would otherwise have to guess which column belongs to which node — and a column mismatch is invisible dimensionally.
- Traffic templates must not declare a named load component. `ansys_load_rendering` returns early on the component branch, rendering a single TABLE for the whole component and silently discarding every per-node mapping. The STbridge APDL model also defines no such component.
- Controlled Agent `DAMPER_OPTIMIZATION` / `MULTI_OBJECTIVE_OPTIMIZATION` advertise `EARTHQUAKE + WIND + TRAFFIC`, gated per solver by the registered baseline-first workflow templates in `OPTIMIZATION_WORKFLOW_CONFIGS_BY_LOAD_KIND`: earthquake and wind on both solvers, traffic on `ANSYS` only (no OpenSeesPy traffic optimization template is registered). `FULL_OPTIMIZATION` stays `ANSYS: EARTHQUAKE` only, matching `is_supported_full_optimization_intent`.
- Controlled Agent `DAMPER_COMPARISON` advertises `EARTHQUAKE + WIND + TRAFFIC` on both `ANSYS` and `OPENSEESPY_INPROC`. Comparison gates on per-damper-type USER300 calibration evidence, and each solver registers its own catalog — `user300_three_damper_calibration.json` for ANSYS, `opensees_user300_three_damper_calibration.json` for OpenSeesPy — so a profile is never labeled with the other solver's evidence. The two catalogs are equally strong in coverage (all three damper types VERIFIED by SHA) but differ in kind, which each profile declares in `evidenceClass`: ANSYS is `USER300_ELEMENT_ACCEPTANCE` (MAPDL element acceptance runs), OpenSeesPy is `RUNTIME_FORMULA_CONFORMANCE` (runtime materials sampled against the declared closed-form force laws, zero error). Cross-solver ANSYS↔OpenSees agreement is established only for the viscous type; eddy-current and friction would need an ANSYS MAPDL run to claim it, so nothing beyond conformance is claimed for them. Damper modules come per solver from `DAMPER_SOLVER_MODULES` / `DAMPER_TYPES.solverModules`, never from a hardcoded ANSYS name. Pre-execution command-stream previews are ANSYS-only (`ansys_damper_commands` renders APDL and has no OpenSees counterpart), so the `executedCommandStreams` evidence check applies only when the result’s solver is ANSYS. Wind and traffic comparison reuse the `ANALYSIS` infrastructure for their load kind (registered template, frozen target set, the same channel contract) rather than defining a second path, so all chains fail closed on the same reason codes.
- Damper-chain gating is registered independently of `ANALYSIS`, and comparison, parameter sweep, and optimization each keep their own table (`AGENT_COMPARISON_CONFIGS_BY_LOAD_KIND`, `AGENT_SWEEP_CONFIGS_BY_LOAD_KIND`, `OPTIMIZATION_WORKFLOW_CONFIGS_BY_LOAD_KIND`). Sharing one table means registering a load kind for `ANALYSIS` silently opens chains that have no implementation and no acceptance evidence.
- Controlled Agent `DAMPER_PARAMETER_SWEEP` advertises `EARTHQUAKE + WIND + TRAFFIC` on both solvers: the sweep reuses each load kind’s `ANALYSIS` baseline template and overrides damper parameters per case.
- Every chain that binds a nodal-force load must freeze `loadTargetSetId` in its approval action, because the execution side fails closed without it rather than falling back to solver defaults. Traffic additionally freezes `loadPointMappingArtifactId` / `loadPointMappingSha256`. The optimization chain originally froze neither: its wind tests passed params straight to the store, bypassing the agent, so the gap never surfaced.
- Traffic optimization is single-objective: beam-end cumulative displacement only. Tower base shear and moment are deliberately excluded — traffic is a vertical moving load while the dampers work along X, so those are not the quantities this chain controls. Cumulative displacement is read at node 1, the larger of the two beam ends (node 1 travels 1.247928 m vs node 72 at 1.241940 m in the momo `traffic_base` run; peak displacement ranks the other way, but peak is not the objective).
- The USER300 calibration profile is always read from `ansys_run_joint_baseline_workflow_template.json`, never from the load-kind template. Wind and earthquake ANALYSIS templates declare `omit_dampers: true` and carry no `damper_calibration` block, so sourcing the profile from them makes `solver_version_profile_passed(require_user300=True)` fail closed. USER300 calibration is a damper-element property and is independent of load kind.
- Controlled Agent paths are resolved by frozen `runMode` (`REAL_AGENT_ANALYSIS`, `REAL_DAMPER_COMPARISON`, or `REAL_BASELINE_OPTIMIZATION`); the same JobType without that mode remains a separate platform capability.
- Catalog entries are unique by `(jobType, mode)` and use camelCase aliases in API responses. The underlying Pydantic models reject unknown fields.
- A platform capability that is not yet registered resolves to `DISABLED` with `handler=unregistered` and an explicit Phase 0 unlock requirement.

### 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| Controlled real Agent mode | Resolve `LIVE` and preserve the existing approval path |
| Standalone high-risk Job without a live mode | Return 501 `CAPABILITY_NOT_IMPLEMENTED` before Job/Artifact creation |
| `MOCK_ONLY` capability in Live API | Reject with the same structured 501 gate |
| Unknown `jobType` | Resolve a disabled unregistered descriptor |
| Duplicate `(jobType, mode)` registration | Raise during registry construction |
| Unknown request field | Pydantic validation error; do not drop or normalize it |

### 5. Good / Base / Bad Cases

- Good: `SOLVER_BATCH` with `runMode=REAL_AGENT_ANALYSIS` resolves the controlled Agent handler, while standalone `SOLVER_BATCH` remains disabled.
- Base: Mock mode can query the catalog and render its own simulated data without changing backend capability status.
- Bad: A frontend hard-codes a Solver page as available or a generic `/jobs` route bypasses the registry and creates a disabled Job.

### 6. Tests Required

- Validate strict `RealJobRequest` aliasing and unknown-field rejection.
- Assert catalog uniqueness, controlled live resolution, standalone disabled resolution, handler names, and unlock requirements.
- `GET /api/v1/capabilities` must expose all current entries and statuses, including `solverScenarios` for any capability whose solvers do not share one scenario set.
- Assert both `supports(solver='ANSYS', scenario='WIND')` and `supports(solver='OPENSEESPY_INPROC', scenario='WIND')` are true for controlled `ANALYSIS` / `SOLVER_BATCH` / `DAMPER_COMPARISON` / `DAMPER_OPTIMIZATION` / `MULTI_OBJECTIVE_OPTIMIZATION` — all four solver × scenario cells have a registered template on each of those chains. `FULL_OPTIMIZATION` stays `ANSYS` + `EARTHQUAKE` only, matching `is_supported_full_optimization_intent`.
- Any new solver × scenario advertised by a `LIVE` controlled capability needs a matching `docs/examples/real_agent_baseline_manifest.json` case, enforced by `test_real_agent_baseline_manifest_covers_registry_live_combinations`. Register the template SHA as the LF-normalized hash and update the pinned case count in the same commit.
- Every high-risk disabled platform Job must fail before records are created; controlled Agent paths must retain their existing behavior.
- Frontend capability types/client method must compile and preserve `LIVE`/`MOCK_ONLY`/`DISABLED` values without local casts.

### 7. Wrong vs Correct

#### Wrong

```python
if job_type == 'SOLVER_BATCH':
    return platform_store.create_job(job_type, params)
```

#### Correct

```python
capability = real_execution_registry.resolve(job_type, params)
if capability.status != 'LIVE':
    raise HTTPException(status_code=501, detail={
        'code': 'CAPABILITY_NOT_IMPLEMENTED',
        'details': {'unlockRequirements': list(capability.unlock_requirements)},
})
```

## Scenario: Standalone mock demos must stay labeled

### 1. Scope / Trigger

This contract applies when `PlatformStore` still returns a standalone Job in `MOMO_PLATFORM_MODE=MOCK` for types that are not yet LIVE. Unlocking a 501 later does not authorize unlabeled placeholder success.

### 2. Signatures

```python
platform_execution_mode() -> str
mock_demo_fields() -> dict[str, Any]
PlatformStore._mark_standalone_mock_demo(job_type, params, result) -> dict[str, Any]
PlatformStore._label_standalone_mock_artifacts(job_type, params, artifacts) -> None
```

### 3. Contracts

- Live mode must fail closed with 501 `CAPABILITY_NOT_IMPLEMENTED` before creating an unimplemented standalone Job. This task does not change that gate.
- A Mock standalone success payload must include `executionMode=MOCK` and `simulation=true`.
- Success results and Artifact previews must not keep `PENDING_REPLACEMENT`, `realExecution`, `realSolverExecution`, or `realFemExecution`.
- Registered model bytes must not be `surrogate-model-placeholder` or `doe-surrogate-model-placeholder`. Mock model content is `b'mock-surrogate-model'` with preview `status=MOCK_MODEL`.
- Controlled Agent `REAL_*` Jobs stay LIVE and must not be relabeled as simulation.

### 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| Live `SURROGATE_TRAINING` / `SOLVER_BATCH` / `ACTIVE_LEARNING` without a LIVE handler | 501 before Job/Artifact creation |
| Mock `EXPERIMENT_DESIGN` succeeds | Job result and Artifact previews carry `executionMode=MOCK` and `simulation=true` |
| Leftover placeholder model bytes are registered | Rewrite to `b'mock-surrogate-model'` and keep the simulation labels |
| Controlled `runMode=REAL_AGENT_ANALYSIS` | Do not attach mock demo fields |

### 5. Good / Base / Bad Cases

- Good: Mock DOE seed artifacts download as `mock-surrogate-model` and the preview says `MOCK_MODEL`.
- Base: Live independent `SURROGATE_TRAINING` still 501s; no placeholder `.pkl` is stored.
- Bad: Returning `realSolverExecution: PENDING_REPLACEMENT` or `surrogate-model-placeholder` as a successful engineering result.

### 6. Tests Required

- Mock standalone Job results must assert `executionMode=MOCK`, `simulation=true`, and the absence of `PENDING_REPLACEMENT` and placeholder bytes.
- Live create of unimplemented standalone types must still raise 501 `CAPABILITY_NOT_IMPLEMENTED`.
- Controlled Agent LIVE resolution tests must keep passing.

### 7. Wrong vs Correct

```python
# Wrong: a demo path claims a real solver replacement is pending.
preview = {'realSolverExecution': 'PENDING_REPLACEMENT'}
content = b'surrogate-model-placeholder'

# Correct: demo paths are explicitly simulated, and Live stays fail-closed.
preview = {**mock_demo_fields(), 'status': 'MOCK_MODEL'}
content = b'mock-surrogate-model'
```

## Scenario: Shared solver/result execution evidence

### 1. Scope / Trigger

Use `RealSolverExecutor` for a frozen batch request whenever a real solver case is
run outside the already-verified atomic Agent wrapper. The executor does not make
approval decisions; it consumes an approved request and reports only persisted
solver results.

### 2. Signatures

```python
PlatformStore._earthquake_baseline_config(
    config: dict[str, Any], config_dir: Path, output_dir: Path, solver: str
) -> dict[str, Any]

build_solver_version_profile(workflow_config_path: Path, *, solver: str) -> dict[str, Any]
solver_version_profile_passed(profile: dict[str, Any], *, require_user300: bool) -> bool
```

### 3. Contracts

- `SolverExecutionRequest` requires a solver, bridge model, at least one load case,
  at least one damper design and a bounded output directory.
- Every result must be `completed`, finite, and persisted by `ResultStore` before it
  is included in `SolverExecutionResult`.
- `outputManifest` hashes every output file under the result directory; the result
  catalog records objective/column names, units, source summary SHA-256 and
  `verified=true` only after those checks pass.
- Cancellation and timeout are checked at case boundaries; the caller supplies
  heartbeat callbacks and may resume safely through the existing ResultStore cache.
- DOE and active-learning helpers are deterministic and never exceed the frozen
  5–24 initial count or two rounds of two infill points.
- Existing configuration-driven Agent handlers must enter through
  `ConfigExecutionRequest`/`RealSolverExecutor.execute_config`. The shared
  boundary passes the frozen `execution_timeout_s` transparently to the
  numerical adapter, checks cancellation/timeout before and after the call,
  rejects non-dict or explicitly failed runner payloads, and reports elapsed
  usage plus a completion heartbeat without rewriting the runner payload.
- Real Agent runs also register `result_catalog.json` beside their CSV outputs.
  Each entry contains the relative source path, columns, engineering units,
  source SHA-256, and `verified=true`; the Agent result references that
  catalog so evidence review can reject an absent or malformed directory.
- Every configuration-driven single analysis binds the solver adapter's
  `output_dir` to `<agent-run-dir>/solver_outputs` for both ANSYS and
  OpenSeesPy. Relative adapter defaults are forbidden because the worker's
  current directory is not the Artifact collection boundary. DOE execution
  may keep using `BatchAnalyzer`, which already binds adapter output beneath
  its approved workflow output directory.
- OpenSees solver evidence prefers the executable runtime's `ops.version()`.
  Distribution evidence checks `openseespy` first and `openseespywin` second;
  a project-bundled runtime without `dist-info` is recorded explicitly as
  `bundled-openseespy`. Solver versions `UNKNOWN`, `NOT_INSTALLED`, empty, or
  absent fail the evidence gate; they must never be accepted as usable version
  evidence.
- Result inquiry treats that catalog as the source of truth whenever it is
  present: only CSV artifacts listed by the catalog and already attached to the
  run may be queried. The inquiry layer rechecks the catalog JSON hash, source
  path, CSV kind, header order, units, and source SHA before exposing columns
  or derived values. Legacy runs without a catalog retain a read-only CSV
  whitelist for compatibility.

### 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| Single ANSYS/OpenSees run prepares a config | Set adapter `output_dir` to the current run's `solver_outputs` directory |
| Solver CSV is outside the current run tree | Do not publish a verified result catalog |
| Primary OpenSees distribution is absent but `openseespywin` exists | Record the `openseespywin` package name and version |
| Bundled OpenSees runtime executes but has no package metadata | Record `ops.version()`, `versionSource=SOLVER_RUNTIME`, and `package=bundled-openseespy` |
| Solver version is `NOT_INSTALLED`, `UNKNOWN`, empty, or absent | Fail preflight/evidence review closed |

### 5. Good / Base / Bad Cases

- Good: an OpenSees single analysis writes case CSV files below the Agent run,
  and recursive Artifact registration publishes a non-empty verified catalog.
- Base: ANSYS and OpenSees use different adapter configuration shapes but the
  same bounded `solver_outputs` directory contract.
- Bad: leaving the adapter default as `output/openseespy_inproc`, which resolves
  relative to the worker process and produces an empty Agent result catalog.

### 6. Tests Required

- Parameterize ANSYS and OpenSees baseline configuration and assert each
  effective adapter `output_dir` equals `<run-dir>/solver_outputs` without
  mutating the source template object.
- Assert all missing-version sentinels fail `solver_version_profile_passed`.
- Simulate an absent `openseespy` distribution with an installed
  `openseespywin` distribution and assert both package name and version.
- Simulate missing distribution metadata with a working bundled runtime and
  assert the executable runtime version and its explicit source label.

### 7. Wrong vs Correct

```python
# Wrong: adapter output escapes the Agent Artifact tree through a cwd-relative default.
solver_config['type'] = 'openseespy_inproc'

# Correct: the generated effective config owns a run-scoped output boundary.
solver_config['type'] = 'openseespy_inproc'
solver_config['output_dir'] = str(run_dir / 'solver_outputs')
```

## Scenario: Optimization decision and training evidence integrity

### 1. Scope / Trigger

This contract applies when a completed real optimization publishes its TOPSIS
candidate response or its reloadable training dataset. It prevents derived
status fields and sample counts from claiming evidence that is not present in
the registered optimization summary.

### 2. Signatures

```python
PlatformStore.real_topsis_result(optimization_run_id: str) -> dict[str, Any] | None
PlatformStore._optimization_execution_evidence(
    *, optimization_summary_path: Path, prepared_workflow: PreparedEarthquakeWorkflow
) -> dict[str, Any]
PlatformStore._register_real_optimization_datasets(
    *, run_dir: Path, optimization_summary: dict[str, Any],
    execution_evidence: dict[str, Any], prepared_workflow: PreparedEarthquakeWorkflow
) -> tuple[Artifact, Artifact]
```

### 3. Contracts

- `constraintsPassed` is recomputed for every real Pareto candidate from its
  `objective_values`, the summary `objective_limits`, and
  `objective_limit_relative_tolerance`; it is never hard-coded from selection
  or FEM-review status.
- `objectiveWeights` remains the canonical name and `entropyWeights` remains a
  compatibility alias with the same values for existing frontend consumers.
- `actualInitialDoeCount` is exactly the number of persisted `doe_designs`; an
  absent or empty summary reports zero and never falls back to the requested
  budget.
- `real_training_dataset.json.rows` contains both initial DOE records and
  `active_learning_records`. Each row has `sampleSource=INITIAL_DOE` or
  `ACTIVE_LEARNING`; `trainingSampleCount` and `trainingDatasetSha256` cover the
  same combined rows.
- The initial DOE count check continues to compare only `doe_designs` with the
  approved initial count. Active-learning additions are separately checked
  against `active_learning_status.record_count`.

### 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| Candidate exceeds an objective limit plus tolerance | Return `constraintsPassed=false` |
| No objective limit applies to a candidate | Return `constraintsPassed=true` |
| Constraint limit or tolerance is non-finite/invalid | Return 422 `OPTIMIZATION_CONSTRAINT_EVIDENCE_INVALID` |
| Initial DOE count differs from the frozen request | Return 422 `REAL_DOE_DATASET_INCOMPLETE` |
| Active-learning record count differs from its status | Return 422 `REAL_ACTIVE_LEARNING_DATASET_INCOMPLETE` |
| Any initial or active-learning sample lacks a completed case ID | Return 422 `REAL_DOE_DATASET_UNVERIFIED` |
| Optimization summary is absent | Report actual initial/training counts as zero |

### 5. Good / Base / Bad Cases

- Good: 15 initial DOE rows plus four active-learning rows publish a 19-row
  training dataset and a hash computed from those same 19 rows.
- Base: No active learning keeps the historical initial-only row count while
  adding `sampleSource=INITIAL_DOE` as a compatible field.
- Bad: Publishing `constraintsPassed=true` for every candidate or reporting the
  requested DOE count when no DOE result exists.

### 6. Tests Required

- Use two Pareto candidates on opposite sides of a registered objective limit
  and assert their returned constraint states differ.
- Add an active-learning record to a valid initial DOE summary and assert row
  source order, combined sample count, and dataset SHA consistency.
- Use a missing summary and assert both actual initial count and training sample
  count are zero.
- Preserve the no-active-learning regression with the configured initial count.

### 7. Wrong vs Correct

#### Wrong

```python
candidate['constraintsPassed'] = True
actual_initial = len(doe_records) or requested_doe_count
training_rows = rows_from(doe_records)
```

#### Correct

```python
candidate['constraintsPassed'] = objectives_within_registered_limits(candidate)
actual_initial = len(doe_records)
training_rows = rows_from(doe_records, active_learning_records)
```

## Scenario: Cross-process solver progress and ANSYS output probing

### 1. Scope / Trigger

- Trigger: a real solver runs in an executor process while the platform worker and API
  expose progress from separate processes.
- Progress is observability only. Missing, malformed, stale, or unreadable progress must
  never fail, cancel, or change the numerical result.

### 2. Signatures

```python
register_ansys_output_probe(progress_dir, case_id, *, output_path, dt, duration) -> None
refresh_ansys_output_probes(progress_dir) -> None
PlatformStore.record_case_progress(job_id) -> Job
```

### 3. Contracts

- OpenSees writes atomic per-case JSON snapshots from its transient loop.
- ANSYS registers a read-only probe before MAPDL starts. The probe reads only the last
  256 KiB of `ansys.out`, accepts the latest numeric `TIME=...` value (including `D`
  exponents), and derives `step=round(time/dt)` with a `[0,totalSteps]` clamp.
- The ANSYS probe does not modify APDL, solver output, case fingerprints, or archived
  design metadata.
- `JobProgress.percent` is the numerical solve percentage: for a batch it is
  `(completed cases + fractional active cases) / total cases`; for a case-only stream it
  is the mean active-case percentage. It falls back to the stage percentage only when no
  numerical progress exists.
- In baseline-first earthquake optimization, the baseline and DOE share one aggregated
  progress channel, but write independent component snapshots (`baseline` and `doe`).
  The reader sums component totals, so the displayed total remains one baseline case
  plus the requested DOE cases while both can run concurrently. DOE is constrained only
  by its frozen parameter bounds; baseline-derived objective limits are applied only to
  optimization post-processing after both solve components complete.
- Worker heartbeat cadence remains independent from the solver write cadence.

### 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| Per-case JSON has invalid types or out-of-range percent | Log and skip that record |
| Batch JSON has invalid counts | Log and omit batch counts |
| Progress aggregation raises unexpectedly | Worker logs the exception and continues supervising the executor |
| `ansys.out` is absent, partial, unreadable, or has no numeric `TIME` | Keep heartbeat/batch progress; do not raise |
| Probe `dt`/`duration` is invalid, non-finite, or non-positive | Do not register a probe |
| Parsed ANSYS time is non-finite or beyond the target duration | Ignore non-finite values; clamp derived step to the approved total |

### 5. Good / Base / Bad Cases

- Good: `ansys.out` ends with `TIME=5.0`, `dt=0.02`, and `duration=10`; the API exposes
  step 250/500 and 50 percent.
- Base: MAPDL has started but `ansys.out` has no completed numeric `TIME`; the UI shows
  worker heartbeat and `0/N` batch progress.
- Bad: a malformed progress record raises from the worker loop or the primary progress
  bar remains at the worker-start value while a case advances.

### 6. Tests Required

- Progress sink tests cover numeric `TIME`, partial output, invalid probe configuration,
  atomic round trips, and corrupt JSON.
- Store tests assert invalid case records are skipped and batch/single-case percentages
  update the primary percentage.
- Worker tests force `record_case_progress` to raise and assert supervision continues to
  the normal executor terminal handling.
- Solver/config tests assert ANSYS accepts `progress_dir`, declares
  `ansys_output_time_probe`, and keeps fingerprints/metadata unchanged.
- The vertical test asserts solver snapshot -> SQLite -> AgentRun preserves the same
  percentage and case fields.

### 7. Wrong vs Correct

#### Wrong

```python
job.progress.percent = job.progress.percent
heartbeat_store.record_case_progress(job_id)  # exception terminates worker
```

#### Correct

```python
job.progress.percent = numerical_progress(completed, total, active_cases)
try:
    heartbeat_store.record_case_progress(job_id)
except Exception:
    logger.exception("progress unavailable; solver supervision continues")
```

## Scenario: Production runtime interruption safety

### 1. Scope / Trigger

- Applies to the production `start.ps1` entry, synchronous platform Jobs, solver requests, dispatcher supervision, and solver-specific review gates.
- These boundaries must make execution mode, persistence, and termination observable; a healthy heartbeat alone is not proof that a numerical runner is making progress.

### 2. Signatures

```text
start.ps1: .env -> process environment
GET /api/v1/readiness -> components.platform_mode
validate_job_params(job_type, params) -> persisted request
PlatformStore.create_job(job_type, params) -> Job
PlatformJobDispatcher._inspect_active_process() -> terminal Job or continued supervision
```

### 3. Contracts

- The production launcher loads `MOMO_PLATFORM_MODE` and `MOMO_AGENT_PERSISTENT_LOOP`; absent `MOMO_PLATFORM_MODE` defaults to `LIVE`. Unknown `MOMO_*` keys are rejected with a key-only warning and never leak their values.
- The desktop shortcut resolves to the repository-root `momo.cmd`, whose working
  directory is the repository root. The batch wrapper is ASCII-only with CRLF
  line endings and invokes `%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe`
  explicitly; localized comments or UTF-8 bytes are forbidden because legacy
  `cmd.exe` may split them into executable commands.
- `start.ps1` is UTF-8 with BOM and CRLF. The BOM is part of the runtime contract:
  Windows PowerShell 5.1 otherwise decodes UTF-8 Chinese comments as the local ANSI
  code page and may report unrelated parser errors. Root `.gitattributes` pins both
  launcher paths to `eol=crlf` so a clean checkout preserves the contract.
- When `MOMO_REQUIRE_PLATFORM_UI=1`, readiness requires `components.platform_mode.status=PASS`; `MOCK` must make the report `NOT_READY`.
- A synchronous `create_job` holds `state_transaction()` from insertion through terminal persistence, so concurrent `refresh()` cannot rebind away the new Job.
- Requests containing `SolverResources` always persist `resources.executionTimeoutS`, including the declared 7200-second default omitted by the caller.
- The dispatcher enforces that timeout as wall-clock time from `startedAt`, terminates the worker/solver process tree, and records `FAILED/EXECUTION_TIMEOUT`; worker heartbeats do not disable this deadline.
- USER300 evidence is required only for an ANSYS workflow contract. `OPENSEESPY_INPROC` review still requires a valid solver profile but not USER300 metadata.

### 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| Production launcher has no mode key | Set process mode to `LIVE` |
| Production UI starts in `MOCK` | Readiness `NOT_READY`, blocking component `platform_mode` |
| Unknown `.env` key begins with `MOMO_` | Warn with key name only; do not load it |
| `momo.cmd` contains non-ASCII bytes or bare LF | Fail the launcher regression; do not publish the shortcut target |
| Windows PowerShell 5.1 cannot parse `start.ps1` | Fail the launcher regression before starting Uvicorn |
| Desktop shortcut target or working directory is missing | Treat the shortcut as broken; both paths must resolve before launch |
| Concurrent refresh during synchronous handler | Refresh waits; terminal Job remains queryable after reload |
| Solver caller omits timeout | Persist `resources.executionTimeoutS=7200` |
| Runtime exceeds frozen timeout while heartbeat continues | Kill process tree and fail with `EXECUTION_TIMEOUT` |
| OpenSeesPy result has no USER300 profile | Do not fail the USER300-specific gate |

### 5. Good / Base / Bad Cases

- Good: the desktop shortcut reaches the ASCII/CRLF batch wrapper, Windows
  PowerShell 5.1 parses the BOM-marked script, and readiness reports `LIVE`.
- Base: an explicitly configured `MOCK` developer backend remains usable without production-UI readiness, while the mode component still exposes `FAIL` and `required=false`.
- Bad: save either launcher as UTF-8 without BOM/LF-only and validate it only by
  reading source text with Python; that does not exercise the legacy Windows parsers.

### 6. Tests Required

- Parse the launcher and assert both runtime switches, LIVE default, invalid-mode rejection, and unknown-key warning are present.
- Assert `momo.cmd` is ASCII-only/CRLF, `start.ps1` begins with the UTF-8 BOM,
  `.gitattributes` pins both paths to CRLF, and the Windows PowerShell 5.1 parser
  reports zero syntax errors.
- Start `momo.cmd -NoBrowser`, call `/api/v1/readiness`, and require `status=READY`
  before accepting a desktop-shortcut change.
- Build readiness in LIVE and MOCK with production UI required; assert exact component and blocking state.
- Block a synchronous handler, race `refresh()`, and assert refresh waits plus SQLite reload returns the successful Job.
- Validate an omitted solver timeout and assert the persisted camelCase value is 7200.
- Supervise a fake live process with a stale `startedAt`; assert tree termination, `EXECUTION_TIMEOUT`, and cleared dispatcher slot.
- Review OpenSeesPy and assert `solver_version_profile_passed(..., require_user300=False)`.

### 7. Wrong vs Correct

```python
# Wrong: default disappears and the alive worker can run forever.
stored = validated.model_dump(by_alias=True, exclude_unset=True)
if heartbeat_is_fresh(job):
    continue

# Correct: materialize the operational default and enforce it at the process supervisor.
stored.setdefault('resources', {})['executionTimeoutS'] = resources.execution_timeout_s
if wall_clock_age(job.started_at) > execution_timeout_s:
    terminate_process_tree(worker.pid)
    fail_job(code='EXECUTION_TIMEOUT')
```

```text
Wrong: UTF-8-without-BOM batch/PowerShell files with localized text and implicit
       `powershell` resolution.
Correct: ASCII + CRLF `momo.cmd` -> explicit Windows PowerShell 5.1 ->
         UTF-8-BOM + CRLF `start.ps1`.
```
