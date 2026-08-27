# Capability Catalog Consumption

## Scenario: Live/Mock feature gating

### 1. Scope / Trigger

This contract applies to frontend pages and API clients that expose solver, DOE, surrogate, active-learning, optimization, or Agent actions. The backend capability catalog is authoritative; local page labels must not imply that a Live operation is executable.

### 2. Signatures

```typescript
api.getCapabilities(): Promise<CapabilityCatalog>
useCapabilityAvailability(jobType: JobType): CapabilityAvailability
buildDashboardExecutionRequest(input: DashboardExecutionInput): DashboardExecutionRequest | null
```
```typescript
interface CapabilityCatalog {
  version: string;
  data: CapabilityDescriptor[];
}
```

### 3. Contracts

- `CapabilityDescriptor.status` is `LIVE`, `MOCK_ONLY`, or `DISABLED`.
- Live pages may render an executable action only for `LIVE`; `MOCK_ONLY` pages must show a visible simulated-data label; `DISABLED` pages are hidden or rendered as unavailable with the backend reason.
- `jobType`, `mode`, handler, solver/scenario support, input/output Artifact kinds, cancel/resume flags, reason, and unlock requirements are read from the response and kept in typed fields.
- `solverScenarios` is the per-solver advertised set. `scenarios` is only the union. Live UI must read `solverScenarios` and never infer a solver × scenario from the flat `scenarios` list: `FULL_OPTIMIZATION` is ANSYS + EARTHQUAKE only, so offering it for any other pairing would render an action the backend fails closed on. The four controlled chains (`ANALYSIS`, `SOLVER_BATCH`, `DAMPER_COMPARISON`, `DAMPER_OPTIMIZATION` / `MULTI_OBJECTIVE_OPTIMIZATION`) do advertise both solvers on both load kinds, but that is a property to be read from the response, never assumed.
- `VITE_API_MODE=mock` may return local mock capability data, but it must never claim a production handler is Live.
- The client must preserve structured 501 errors from the backend rather than silently falling back to mock behavior in Live mode.
- Standalone capability pages use `useCapabilityAvailability`; Live actions stay
  disabled until the matching `PLATFORM_API` entry is loaded and is `LIVE`.
- Dashboard creates only the registered earthquake baseline optimization request.
  Targets that need DOE, case-set, solver-run, or dataset-specific fields return
  `null` and direct the user to their dedicated page instead of sending a generic
  payload that the backend will reject.
- Live Surrogate/Solver pages must not locally synthesize R²/RMSE/CV metrics or
  FEM review queues after a Job succeeds. Mock pages may render
  `buildMockTrainingMetrics` / `buildMockInfillQueue`, but the visible label
  must be `模拟数据` and must not say `真实 FEM 样本复核队列`.

### 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| Backend returns `LIVE` | Enable the matching action after normal form validation |
| Backend returns `MOCK_ONLY` in Mock mode | Keep demo action and show simulated-data marker |
| Backend returns `MOCK_ONLY` in Live mode | Disable action and show the reason |
| Backend returns `DISABLED` | Hide/disable action and show unlock requirements |
| Capability request fails in Live mode | Keep high-risk actions disabled; show structured network error |
| Unknown capability field | Type boundary must not cast it into executable state |

### 5. Good / Base / Bad Cases

- Good: SolverBatchPage fetches capabilities, sees standalone `SOLVER_BATCH` disabled, and does not submit a Job in Live mode.
- Base: Mock mode renders the same form with a “模拟数据” marker and uses `mockDb` only.
- Bad: A page checks only the union type `JobType` and enables submit without reading capability status.

### 6. Tests Required

- TypeScript build must compile the catalog and client method without casts.
- API client tests must assert the Live path calls `/api/v1/capabilities` and the Mock path returns `MOCK_ONLY` descriptors.
- Page tests must cover Live `LIVE`, `MOCK_ONLY`, and `DISABLED` rendering and preserve the visible reason.
- A Live 501 response must not create a local mock Job or Artifact.
- Surrogate mock markup must contain `模拟数据` and must not contain `真实 FEM 样本复核队列`.

### 7. Wrong vs Correct

#### Wrong

```typescript
const canSubmit = type === "SOLVER_BATCH";
```

#### Correct

```typescript
const capability = capabilities.data.find(item => item.jobType === type && item.mode === "PLATFORM_API");
const canSubmit = capability?.status === "LIVE";
```
