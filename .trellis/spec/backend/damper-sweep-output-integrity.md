# Damper Sweep Output Integrity

## Scenario: Deep result paths and failure-closed sweep review

### 1. Scope / Trigger

This contract applies to real damper parameter sweeps that persist per-case
summaries through `ResultStore` and then review the registered Job evidence.
It prevents Windows path-length failures after a solver has already produced
raw output, and prevents missing data from appearing as passed evidence.

### 2. Signatures

```python
_temp_path_for(path: Path) -> Path
PlatformStore.reverify_real_damper_parameter_sweep(job_id: str) -> Job
DamperParameterSweepAgent.review(
    job: dict[str, Any], *, workflow_contract: dict[str, Any] | None = None
) -> ReviewOutcome
```

### 3. Contracts

- `ResultStore` temporary files stay in the target file's parent directory so
  `Path.replace()` remains atomic, but their names must not include the full
  target name. A short random name avoids extending an otherwise valid Windows
  result path past the legacy 260-character limit.
- A sweep can pass `allCasesCompleted` or `allCasesVerified` only after the
  expected non-empty case set is registered.
- `realArtifactEvidence` requires at least one registered Artifact.
- `resultCatalog` always delegates to `result_catalog_passed`; an absent catalog
  is failed evidence, not an optional pass.
- Solver execution mode is read first from `metadata.solver_design.execution_mode`;
  the legacy top-level `metadata.execution_mode` is only a compatibility fallback.
  A completed case is verified only when that mode is `run`,
  `is_verified_solver_output` is true, and no dry-run marker is present.
- If the original work directory has been cleaned, a completed real sweep may be
  reverified from the registered command stream, per-case time-series CSV, and
  original output manifest only when every content hash and manifest entry still
  match. Recovery must not invoke the solver, must retain the old artifacts for
  audit, and must replace their Job attachments with the recovered summaries.

### 4. Validation & Error Matrix

| Condition | Required behavior |
| --- | --- |
| Deep case summary path is valid but the old descriptive temp name exceeds the Windows limit | Persist through a short same-directory temp name and atomically replace the target. |
| Job has no registered `caseResults` | `allCasesPresent`, `allCasesCompleted`, and `allCasesVerified` are all false. |
| Job has no Artifacts | `artifactHashes` and `realArtifactEvidence` are false. |
| Job has no result catalog ID | `resultCatalog` is false. |
| Raw work directory is gone but registered command/CSV content and manifest hashes all match | Rebuild only the attached case/sweep evidence from those registered artifacts; do not re-run the solver. |
| Nested solver-design mode is missing or is not `run` | `allCasesVerified` is false even when the case is otherwise completed. |

### 5. Good / Base / Bad Cases

- Good: A deep real-case directory writes `summary.json` and `index.csv`, then
  registers the case, result catalog, and output manifest.
- Base: A partially completed sweep with all expected registered cases still
  reports the specific incomplete or unverified case gate as false.
- Bad: `all([])` marks an empty failed Job as completed and verified, or an
  absent result catalog is displayed as passed evidence.

### 6. Tests Required

- Construct a Windows-style 233-character `summary.json` path and assert its
  generated temporary path is shorter than 260 characters and has the same
  parent.
- Save an `AnalysisResult` under a deeply nested temporary directory and assert
  both the summary and `index.csv` exist.
- Review a failed sweep with no result and no Artifacts, and assert every
  evidence check is false.
- Reverify a real sweep from registered artifacts, assert case verification is
  true, and assert the original source command/CSV bytes are unchanged.

### 7. Wrong vs Correct

```python
# Wrong: embeds the target filename and a 32-character UUID in the temp name.
path.with_name(f".{path.name}.{uuid4().hex}.tmp")

# Correct: preserves the parent directory while keeping the temp name short.
path.with_name(f".tmp-{uuid4().hex[:12]}")
```

---

## Scenario: Read-only multi-case time-history comparison

### 1. Scope / Trigger

This contract applies when a completed `DAMPER_PARAMETER_SWEEP` is visualized as
multiple parameter-case time-history curves. It exposes only registered,
verified CSV output and never starts a solver.

### 2. Signatures

```python
AgentService.get_run_timeseries_comparison(
    run_id: str,
    *,
    columns: list[str] | None = None,
    case_ids: list[str] | None = None,
    max_points: int = 1000,
    owner: str | None = None,
) -> dict[str, Any]

GET /api/v1/agent/runs/{run_id}/timeseries/compare
```

### 3. Contracts

- The response includes `runId`, common `availableColumns`, selected `columns`,
  and one `cases` item per verified parameter case. Each item carries `caseId`,
  original `parameters`, a display `label`, downsampled `series`, and per-column
  `peaks`.
- A source CSV is valid only when it is the unique registered `timeseries.csv`
  entry under the selected case path and it shares `time` plus the requested
  response column with every selected case.
- `vfloor=0.001` remains in the returned solver `parameters` but is omitted from
  the display `label`; a non-default value remains visible.

### 4. Validation & Error Matrix

| Condition | Required behavior |
| --- | --- |
| Run is not `DAMPER_PARAMETER_SWEEP` | `422 TIMESERIES_COMPARISON_NOT_AVAILABLE` |
| No verified cases are registered | `404 TIMESERIES_COMPARISON_NOT_AVAILABLE` |
| Request contains an unknown or unverified case ID | `422 TIMESERIES_CASE_NOT_AVAILABLE` |
| A case has zero or multiple registered `timeseries.csv` sources | `422 TIMESERIES_CASE_SOURCE_INVALID` |
| Selected cases lack a shared time/response column | `422 TIMESERIES_COMMON_COLUMN_MISSING` |

### 5. Good / Base / Bad Cases

- Good: Four verified cases select `displacement` and return four independently
  labeled curve series without any new solve.
- Base: Selecting one verified case returns only that case while retaining the
  same response schema.
- Bad: An unverified case or a CSV outside the result catalog cannot be returned.

### 6. Tests Required

- Build two registered case CSV artifacts and assert each returned series is read
  from its own artifact.
- Assert a default `vfloor` is absent from the label and a non-default value is
  retained.
- Assert requested case filtering and unavailable-case rejection.

### 7. Wrong vs Correct

```python
# Wrong: use an arbitrary case file path or silently return one legacy curve.
series = Path(case["timeseriesPath"]).read_bytes()

# Correct: resolve exactly one registered catalog entry for every verified case.
source = registered_catalog_entry(case_id, name="timeseries.csv")
```

---

## Scenario: Viscous damping-coefficient unit boundary

### 1. Scope / Trigger

This contract applies when a `DAMPER_PARAMETER_SWEEP` accepts user-visible
viscous `parameters.c`. The UI and frozen sweep contract use engineering units
(`kN*s/m`); ANSYS and OpenSeesPy solver commands use `N*s/m`.

### 2. Signatures

```python
ENGINEERING_VISCOUS_C_SCALE_BY_SOLVER: dict[str, float]
PlatformStore._generate_real_damper_parameter_sweep_artifacts(
    params: dict[str, Any], *, job_id: str | None = None
) -> list[Artifact]
```

The generated case config carries the conversion through
`solver_kwargs["damper_c_scale"]`.

### 3. Contracts

- A user-supplied sweep `parameters.c` remains in engineering units in the
  frozen request, case summary, and UI. It must not be mutated to solver units.
- Before creating a real sweep case config, the store selects the authoritative
  scale for its solver: `ANSYS_DAMPER_C_SCALE` or
  `OPENSEES_DAMPER_C_SCALE`. Both are currently `1000.0`.
- Consequently, `c=1000 kN*s/m` is exported as `1_000_000 N*s/m`; physical
  damper splitting happens afterwards and must not change the total conversion.
- `REAL_DAMPER_COMPARISON` is deliberately different: its viscous `c` is
  derived from an `N`-based force-cap formula and is already in solver units.
  Its preview and execution configs keep `damper_c_scale=1.0`.

### 4. Validation & Error Matrix

| Condition | Required behavior |
| --- | --- |
| User viscous sweep with ANSYS | Write `damper_c_scale=1000.0` into the case config. |
| User viscous sweep with OpenSeesPy | Write `damper_c_scale=1000.0` into the case config. |
| Registered comparison case derived from `forceCapN` | Keep scale `1.0`; do not apply the UI conversion twice. |
| Unsupported sweep solver | Reject through the existing request/capability validation before a config is produced. |

### 5. Good / Base / Bad Cases

- Good: A four-case user sweep displays `c=1000` while its generated solver
  metadata records `damper_c_scale=1000.0`.
- Base: An optimization template already carrying the same scale remains
  unchanged.
- Bad: A sweep writer overwrites a template/default with `1.0`, causing every
  user-entered viscous coefficient to be 1000 times too small.

### 6. Tests Required

- Capture generated ANSYS and OpenSeesPy sweep configs and assert their
  `solver_kwargs.damper_c_scale` values are `1000.0`.
- Build a force-derived comparison command stream and assert the exported
  viscous `C` equals the internally derived value, not that value times 1000.
- Search all explicit `damper_c_scale=1.0` overrides during review; each must
  be documented as a solver-unit input path.

### 7. Wrong vs Correct

```python
# Wrong: overwrites the solver's engineering-unit conversion for user input.
solver_kwargs["damper_c_scale"] = 1.0

# Correct: preserve the UI value and convert only at the solver boundary.
solver_kwargs["damper_c_scale"] = ENGINEERING_VISCOUS_C_SCALE_BY_SOLVER[solver]
```
