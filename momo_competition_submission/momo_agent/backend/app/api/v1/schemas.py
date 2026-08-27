from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from app.api.schemas import WindRequest


JobType = Literal[
    'PROJECT_GATE_FAST',
    'USER300_VERIFY',
    'PREFLIGHT_CONFIG',
    'LOAD_TRAFFIC_RANDOM',
    'LOAD_WIND_VERTICAL',
    'LOAD_EARTHQUAKE',
    'LOAD_CURVE_EXPORT',
    'COMMAND_STREAM_ASSEMBLY',
    'SOLVER_BATCH',
    'RESULT_EXTRACTION',
    'SOLVER_PARITY',
    'BASELINE_DOE',
    'EXPERIMENT_DESIGN',
    'SURROGATE_TRAINING',
    'ACTIVE_LEARNING',
    'MULTI_OBJECTIVE_OPTIMIZATION',
    'ENTROPY_TOPSIS_DECISION',
    'OPTIMIZATION_EXPORT',
    'WIND_WORKFLOW',
]

JobStatus = Literal['QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED']
ArtifactKind = Literal[
    'LOAD_CASE',
    'JSON_SUMMARY',
    'CSV_TIMESERIES',
    'CSV_TABLE',
    'RAW_DATA',
    'PARITY_REPORT',
    'SURROGATE_MODEL',
    'OPTIMIZATION_REPORT',
    'DECISION_REPORT',
    'STATUS_REPORT',
    'LOG',
    'COMMAND_STREAM',
    'PLOT',
    'BINARY',
    'FEM_MODEL',
]


class PlatformModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra='allow')


class PlatformRequestModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra='forbid',
        strict=True,
    )


class Artifact(PlatformModel):
    artifact_id: str = Field(alias='artifactId')
    kind: ArtifactKind
    name: str
    path: str
    mime_type: str = Field(alias='mimeType')
    size_bytes: int | None = Field(default=None, alias='sizeBytes')
    sha256: str | None = None
    can_preview: bool = Field(alias='canPreview')
    download_url: str = Field(alias='downloadUrl')
    source: Literal['PLATFORM_JOB', 'REAL_OPTIMIZATION_HISTORY', 'REAL_SOLVER_RESULT'] | None = None
    run_id: str | None = Field(default=None, alias='runId')
    created_at: str | None = Field(default=None, alias='createdAt')


class CaseProgress(PlatformModel):
    case_id: str = Field(alias='caseId')
    percent: int
    step: int | None = None
    total_steps: int | None = Field(default=None, alias='totalSteps')
    phase: str | None = None


class JobProgress(PlatformModel):
    phase: str
    message: str
    percent: int | None = None
    completed_cases: int | None = Field(default=None, alias='completedCases')
    total_cases: int | None = Field(default=None, alias='totalCases')
    active_cases: list[CaseProgress] | None = Field(default=None, alias='activeCases')


class JobError(PlatformModel):
    code: str
    message: str
    details: Any | None = None


class JobWorker(PlatformModel):
    pid: int
    heartbeat_at: str | None = Field(default=None, alias='heartbeatAt')
    exit_code: int | None = Field(default=None, alias='exitCode')


class Job(PlatformModel):
    job_id: str = Field(alias='jobId')
    type: JobType
    status: JobStatus
    title: str
    created_at: str = Field(alias='createdAt')
    started_at: str | None = Field(default=None, alias='startedAt')
    finished_at: str | None = Field(default=None, alias='finishedAt')
    progress: JobProgress | None = None
    request: dict[str, Any]
    result: dict[str, Any] | None = None
    artifacts: list[Artifact] = Field(default_factory=list)
    error: JobError | None = None
    worker: JobWorker | None = None
    queue_version: int | None = Field(default=None, alias='queueVersion')


class PaginatedResponse(PlatformModel):
    data: list[Any]
    pagination: dict[str, int]


class JobCreateRequest(PlatformRequestModel):
    type: JobType
    params: dict[str, Any] = Field(default_factory=dict)


class EngineeringConfigRequest(PlatformRequestModel):
    project_config: dict[str, Any] = Field(default_factory=dict)
    global_task_config: dict[str, Any] = Field(default_factory=dict)
    damper_base_config: dict[str, Any] = Field(default_factory=dict)
    load_config: dict[str, Any] = Field(default_factory=dict)
    doe_config: dict[str, Any] = Field(default_factory=dict)
    solver_batch_config: dict[str, Any] = Field(default_factory=dict)
    result_extraction_config: dict[str, Any] = Field(default_factory=dict)
    surrogate_learning_config: dict[str, Any] = Field(default_factory=dict)
    optimization_decision_config: dict[str, Any] = Field(default_factory=dict)

    def to_platform_config(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_unset=True)


class PreflightRequest(PlatformRequestModel):
    config_path: str = Field(min_length=1)


class SolverResources(PlatformRequestModel):
    process_count: int = Field(default=1, ge=1, alias='processCount')
    cores_per_process: int = Field(default=1, ge=1, alias='coresPerProcess')
    execution_timeout_s: float = Field(default=7200, gt=0, alias='executionTimeoutS')


class LoadColumnMapping(PlatformRequestModel):

    time_column: str = Field(min_length=1, alias='timeColumn')
    time_unit: Literal['s', 'ms'] = Field(default='s', alias='timeUnit')
    value_column: str = Field(min_length=1, alias='valueColumn')
    source_unit: Literal['N', 'kN', 'm/s2', 'm/s²', 'g'] = Field(
        alias='sourceUnit',
        description='m/s² 会规范化为 m/s2；响应 normalization 同时报告原单位和规范化单位。',
    )
    quantity: Literal['FORCE', 'ACCELERATION']
    target_type: Literal['GROUND', 'NODE', 'NODE_GROUP', 'LANE_GROUP'] = Field(alias='targetType')
    target_id: str = Field(min_length=1, alias='targetId')
    component: str = Field(min_length=1)
    consistent_excitation: bool | None = Field(default=None, alias='consistentExcitation')

    @model_validator(mode='after')
    def validate_unit_for_quantity(self) -> 'LoadColumnMapping':
        accepted_units = {
            'FORCE': {'N', 'kN'},
            'ACCELERATION': {'m/s2', 'm/s²', 'g'},
        }
        if self.source_unit not in accepted_units[self.quantity]:
            raise ValueError(f'{self.source_unit} is not valid for {self.quantity}')
        return self


class TrafficLoadRequest(PlatformRequestModel):
    bridge_id: str = Field(default='stbridge', alias='bridgeId')
    scenario_name: str = Field(default='traffic_wim_2021_01', alias='scenarioName')
    source_mode: Literal['GENERATE_RANDOM', 'LOAD_EXISTING'] = Field(default='GENERATE_RANDOM', alias='sourceMode')
    duration_s: float = Field(default=86400, gt=0, alias='durationS')
    time_step_s: float = Field(default=1.0, gt=0, alias='timeStepS')
    seed: int = 20260702
    traffic_model: str = Field(default='RANDOM_FLOW', alias='trafficModel')
    vehicle_library_id: str | None = Field(default='vehicle_library_wim_2021_01', alias='vehicleLibraryId')
    existing_vehicle_data_artifact_id: str | None = Field(default=None, alias='existingVehicleDataArtifactId')
    input_artifact_id: str | None = Field(default=None, alias='inputArtifactId')
    load_mapping: LoadColumnMapping | None = Field(default=None, alias='loadMapping')
    hourly_flow_profile: list[dict[str, Any]] | None = Field(default=None, alias='hourlyFlowProfile')
    traffic_scale: float = Field(default=1.0, ge=0, alias='trafficScale')
    heavy_vehicle_scale: float = Field(default=1.0, ge=0, alias='heavyVehicleScale')
    lane_mode: Literal['MAIN_GIRDER_BIDIRECTIONAL'] = Field(default='MAIN_GIRDER_BIDIRECTIONAL', alias='laneMode')
    applied_structure: Literal['MAIN_GIRDER'] = Field(default='MAIN_GIRDER', alias='appliedStructure')
    export_formats: list[str] = Field(default_factory=lambda: ['CSV', 'JSON'], alias='exportFormats')

    @model_validator(mode='after')
    def validate_source_mode(self) -> 'TrafficLoadRequest':
        if self.source_mode == 'GENERATE_RANDOM' and not self.vehicle_library_id:
            raise ValueError('vehicleLibraryId is required when sourceMode is GENERATE_RANDOM')
        if self.source_mode == 'LOAD_EXISTING' and not (self.existing_vehicle_data_artifact_id or self.input_artifact_id):
            raise ValueError('inputArtifactId or existingVehicleDataArtifactId is required when sourceMode is LOAD_EXISTING')
        if self.input_artifact_id and self.load_mapping is None:
            raise ValueError('loadMapping is required when inputArtifactId is provided')
        if self.load_mapping and self.load_mapping.quantity != 'FORCE':
            raise ValueError('traffic file loadMapping.quantity must be FORCE')
        return self


class WindLoadRequest(PlatformRequestModel):
    bridge_id: str = Field(default='stbridge', alias='bridgeId')
    scenario_name: str = Field(default='vertical_wind', alias='scenarioName')
    source: Literal['WIND_MODULE', 'LOCAL_FILE'] = 'WIND_MODULE'
    applied_component: Literal['VERTICAL'] = Field(default='VERTICAL', alias='appliedComponent')
    input_artifact_id: str | None = Field(default=None, alias='inputArtifactId')
    load_mapping: LoadColumnMapping | None = Field(default=None, alias='loadMapping')
    wind_request: dict[str, Any] | None = Field(default=None, alias='windRequest')
    time_history: dict[str, Any] | None = Field(default=None, alias='timeHistory')

    @model_validator(mode='after')
    def validate_local_file(self) -> 'WindLoadRequest':
        if self.source == 'LOCAL_FILE' and (not self.input_artifact_id or self.load_mapping is None):
            raise ValueError('inputArtifactId and loadMapping are required when source is LOCAL_FILE')
        if self.load_mapping and self.load_mapping.quantity != 'FORCE':
            raise ValueError('wind file loadMapping.quantity must be FORCE')
        return self


class WindExportRequest(PlatformRequestModel):
    format: Literal[
        'EXCEL',
        'GIRDER_CSV',
        'TOWER_CSV',
        'GIRDER_ALL_POINTS_CSV',
        'TOWER_ALL_POINTS_CSV',
        'SPECTRUM_CSV',
        'COHERENCE_CSV',
        'UNIFIED_WIND_CSV',
    ] = 'UNIFIED_WIND_CSV'
    request: WindRequest


class EarthquakeLoadRequest(PlatformRequestModel):
    bridge_id: str = Field(default='stbridge', alias='bridgeId')
    scenario_name: str = Field(default='earthquake_load', alias='scenarioName')
    source: Literal['CODE_SPECTRUM', 'PEER_RECORD', 'LOCAL_FILE', 'TEMPLATE'] = 'TEMPLATE'
    direction: str = 'X'
    time_step_s: float = Field(default=0.02, gt=0, alias='timeStepS')
    duration_s: float = Field(default=40, gt=0, alias='durationS')
    input_artifact_id: str | None = Field(default=None, alias='inputArtifactId')
    load_mapping: LoadColumnMapping | None = Field(default=None, alias='loadMapping')
    code_spectrum: dict[str, Any] | None = Field(default=None, alias='codeSpectrum')
    peer_record: dict[str, Any] | None = Field(default=None, alias='peerRecord')
    scaling: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode='after')
    def validate_local_file(self) -> 'EarthquakeLoadRequest':
        if self.source == 'LOCAL_FILE' and (not self.input_artifact_id or self.load_mapping is None):
            raise ValueError('inputArtifactId and loadMapping are required when source is LOCAL_FILE')
        if self.load_mapping:
            if self.load_mapping.quantity != 'ACCELERATION':
                raise ValueError('earthquake file loadMapping.quantity must be ACCELERATION')
            if self.load_mapping.target_type != 'GROUND' or self.load_mapping.consistent_excitation is not True:
                raise ValueError('earthquake file load requires GROUND target with consistentExcitation=true')
        return self


class LoadCurveExportRequest(PlatformRequestModel):
    source_artifact_id: str = Field(min_length=1, alias='sourceArtifactId')
    load_kind: Literal['TRAFFIC', 'WIND', 'EARTHQUAKE'] = Field(alias='loadKind')
    curve_component: str = Field(default='vehicle_flow', alias='curveComponent')
    formats: list[str] = Field(min_length=1)
    custom_format: str | None = Field(default=None, alias='customFormat')
    style_preset: str = Field(default='PROJECT_NATURE', alias='stylePreset')


class CommandStreamModuleConfig(PlatformRequestModel):
    modules: list[str] = Field(min_length=1)
    damper: dict[str, Any] = Field(default_factory=dict)
    response_targets: list[str] = Field(min_length=1, alias='responseTargets')


class CommandStreamAssembleRequest(PlatformRequestModel):
    solver: Literal['ANSYS', 'OPENSEES', 'OPENSEESPY_INPROC'] = 'ANSYS'
    bridge_id: str = Field(default='stbridge', alias='bridgeId')
    case_set_id: str = Field(default='case_set_local', alias='caseSetId')
    module_config: CommandStreamModuleConfig = Field(alias='moduleConfig')


class SolverRunRequest(PlatformRequestModel):
    solver: Literal['ANSYS', 'OPENSEES', 'OPENSEESPY_INPROC'] = 'ANSYS'
    case_set_id: str = Field(alias='caseSetId')
    resources: SolverResources = Field(default_factory=SolverResources)


class DamperParameterSweepCaseRequest(PlatformRequestModel):
    case_id: str = Field(alias='caseId', min_length=1, max_length=64)
    damper_type: Literal['VISCOUS', 'FRICTION', 'EDDY_CURRENT'] = Field(alias='damperType')
    parameters: dict[str, float] = Field(min_length=1, max_length=8)


class DamperParameterSweepRequest(SolverRunRequest):
    run_mode: Literal['REAL_DAMPER_PARAMETER_SWEEP'] = Field(alias='runMode')
    scenario: Literal['EARTHQUAKE'] = 'EARTHQUAKE'
    cases: list[DamperParameterSweepCaseRequest] = Field(min_length=1, max_length=64)
    selected_layout_id: Literal['ONE_PER_TOWER', 'TWO_PER_TOWER'] = Field(
        default='TWO_PER_TOWER', alias='selectedLayoutId',
    )
    response_ids: list[str] = Field(min_length=1, max_length=7, alias='responseIds')
    max_concurrent_cases: int = Field(default=4, ge=1, le=8, alias='maxConcurrentCases')


class ResultExtractionRequest(PlatformRequestModel):
    solver_run_id: str = Field(alias='solverRunId')
    extractors: list[str] = Field(default_factory=lambda: ['OBJECTIVES'])


class ExperimentDesignRequest(PlatformRequestModel):
    bridge_id: str = Field(default='stbridge', min_length=1, alias='bridgeId')
    design_name: str = Field(default='user300_damper_training_doe', min_length=1, alias='designName')
    solver: Literal['ANSYS', 'OPENSEES', 'OPENSEESPY_INPROC'] = 'ANSYS'
    case_set_prefix: str = Field(default='doe_user300', min_length=1, alias='caseSetPrefix')
    scenario_type: Literal['EARTHQUAKE', 'WIND', 'TRAFFIC', 'OPERATION'] = Field(alias='scenarioType')
    execution_goal: Literal['BATCH_CALCULATION', 'OPTIMIZATION_RECOMMENDATION'] = Field(alias='executionGoal')
    vehicle_library_id: str | None = Field(default=None, alias='vehicleLibraryId')
    damper: dict[str, Any] = Field(default_factory=dict)
    variables: list[dict[str, Any]] = Field(min_length=1)
    sampling: dict[str, Any] = Field(default_factory=dict)
    response_targets: list[str] = Field(min_length=1, alias='responseTargets')
    resources: SolverResources = Field(default_factory=SolverResources)

    @model_validator(mode='after')
    def validate_experiment_design_contract(self) -> 'ExperimentDesignRequest':
        if not any(variable.get('enabled', True) for variable in self.variables):
            raise ValueError('at least one design variable must be enabled')
        if self.scenario_type in ('TRAFFIC', 'OPERATION') and not self.vehicle_library_id:
            raise ValueError('vehicleLibraryId is required for TRAFFIC or OPERATION scenario')
        return self


class ImportedSurrogateDatasetSchema(PlatformRequestModel):
    feature_columns: list[str] = Field(min_length=1, alias='featureColumns')
    target_columns: dict[str, str] = Field(alias='targetColumns')

    @model_validator(mode='after')
    def validate_column_mapping(self) -> 'ImportedSurrogateDatasetSchema':
        if not self.target_columns:
            raise ValueError('targetColumns must include at least one response mapping')
        if any(not str(column).strip() for column in self.feature_columns):
            raise ValueError('featureColumns cannot contain empty names')
        if any(not str(metric_id).strip() or not str(column).strip() for metric_id, column in self.target_columns.items()):
            raise ValueError('targetColumns cannot contain empty metric ids or column names')
        return self


SUPPORTED_SURROGATE_TARGET_METRIC_IDS = frozenset({
    'metric_beam_end_ux_peak',
    'metric_tower_base_shear',
    'metric_tower_base_moment',
    'metric_beam_end_ux_cumulative',
})


class SurrogateTrainingRequest(PlatformRequestModel):
    dataset_source_mode: Literal['DOE_DATASET', 'USER_IMPORTED_ARTIFACT'] = Field(default='DOE_DATASET', alias='datasetSourceMode')
    dataset_id: str | None = Field(default=None, alias='datasetId')
    imported_dataset_artifact_id: str | None = Field(default=None, alias='importedDatasetArtifactId')
    imported_dataset_schema: ImportedSurrogateDatasetSchema | None = Field(default=None, alias='importedDatasetSchema')
    model_families: list[str] = Field(min_length=1, alias='modelFamilies')
    target_metric_ids: list[str] = Field(min_length=1, alias='targetMetricIds')
    targets: list[str] | None = None
    validation: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode='after')
    def validate_dataset_source(self) -> 'SurrogateTrainingRequest':
        unsupported_targets = sorted(
            set(self.target_metric_ids) - SUPPORTED_SURROGATE_TARGET_METRIC_IDS
        )
        if unsupported_targets:
            raise ValueError(
                f'unsupported surrogate targetMetricIds: {", ".join(unsupported_targets)}'
            )
        if self.dataset_source_mode == 'DOE_DATASET' and not self.dataset_id:
            raise ValueError('datasetId is required when datasetSourceMode is DOE_DATASET')
        if self.dataset_source_mode == 'USER_IMPORTED_ARTIFACT':
            if not self.imported_dataset_artifact_id:
                raise ValueError('importedDatasetArtifactId is required when datasetSourceMode is USER_IMPORTED_ARTIFACT')
            if self.imported_dataset_schema is None:
                raise ValueError('importedDatasetSchema is required when datasetSourceMode is USER_IMPORTED_ARTIFACT')
        return self


class ActiveLearningInfillRequest(PlatformRequestModel):
    active_learning_enabled: bool = Field(alias='activeLearningEnabled')
    surrogate_run_id: str | None = Field(default=None, alias='surrogateRunId')
    strategy: str = 'UNCERTAINTY_AND_PARETO'
    batch_size: int = Field(default=0, ge=0, alias='batchSize')
    requires_real_fem_review: bool = Field(default=True, alias='requiresRealFemReview')


class OptimizationObjectiveRequest(PlatformRequestModel):
    name: str = Field(min_length=1)
    direction: Literal['MIN', 'MAX'] = 'MIN'


class OptimizationConstraintRequest(PlatformRequestModel):
    target_id: str = Field(min_length=1, alias='targetId')
    name: str = Field(min_length=1)
    operator: Literal['<=', '>=']
    value: float = Field(ge=0)
    unit: str = Field(min_length=1)
    source: Literal['UNCONTROLLED', 'CUSTOM']


class MultiObjectiveOptimizationRequest(PlatformRequestModel):
    surrogate_run_id: str | None = Field(default=None, alias='surrogateRunId')
    objective_mode: Literal['SEISMIC', 'OPERATION', 'OVERALL', 'CUSTOM'] = Field(default='SEISMIC', alias='objectiveMode')
    objectives: list[OptimizationObjectiveRequest] = Field(min_length=1)
    constraints: list[OptimizationConstraintRequest] = Field(default_factory=list)

    @model_validator(mode='after')
    def validate_fixed_objective_mode(self) -> 'MultiObjectiveOptimizationRequest':
        if 'objective_mode' not in self.model_fields_set:
            return self
        fixed_objectives = {
            'SEISMIC': {
                'beamEndDisplacement',
                'towerBaseShear',
                'towerBaseMoment',
            },
            'OPERATION': {'beamEndCumulativeDisplacement'},
            'OVERALL': {
                'beamEndDisplacement',
                'towerBaseShear',
                'towerBaseMoment',
                'beamEndCumulativeDisplacement',
            },
        }
        expected = fixed_objectives.get(self.objective_mode)
        actual = [objective.name for objective in self.objectives]
        if expected is not None and (set(actual) != expected or len(actual) != len(expected)):
            raise ValueError(
                f'{self.objective_mode} objectives must exactly match {sorted(expected)}'
            )
        return self


class EntropyTopsisDecisionRequest(PlatformRequestModel):
    optimization_run_id: str = Field(alias='optimizationRunId')
    candidate_filter: dict[str, Any] = Field(default_factory=dict, alias='candidateFilter')


class OptimizationExportRequest(PlatformRequestModel):
    optimization_run_id: str = Field(alias='optimizationRunId')
    export_kinds: list[str] = Field(min_length=1, alias='exportKinds')
    formats: list[str] = Field(min_length=1)


class EmptyJobRequest(PlatformRequestModel):
    pass


class SolverParityJobRequest(PlatformRequestModel):
    config_path: str = Field(min_length=1)
    execution_timeout_s: float | None = Field(default=None, gt=0)
    real_gate_timeout_s: float | None = Field(default=None, gt=0)


class WorkflowExecutionJobRequest(PlatformRequestModel):
    project_name: str | None = None
    model_file_name: str | None = None
    solver: Literal['ANSYS', 'OPENSEES', 'OPENSEESPY_INPROC']
    scenario: Literal['EARTHQUAKE', 'WIND', 'TRAFFIC', 'OPERATION']
    execution_target: str = Field(min_length=1)
    required_modules: list[str] = Field(min_length=1)
    run_mode: Literal['REAL_BASELINE_OPTIMIZATION'] | None = None
    workflow_config_path: str | None = None
    execution_timeout_s: float | None = Field(default=None, gt=0)


JOB_REQUEST_MODELS: dict[str, type[PlatformRequestModel]] = {
    'PROJECT_GATE_FAST': EmptyJobRequest,
    'USER300_VERIFY': EmptyJobRequest,
    'PREFLIGHT_CONFIG': PreflightRequest,
    'LOAD_TRAFFIC_RANDOM': TrafficLoadRequest,
    'LOAD_WIND_VERTICAL': WindLoadRequest,
    'LOAD_EARTHQUAKE': EarthquakeLoadRequest,
    'LOAD_CURVE_EXPORT': LoadCurveExportRequest,
    'COMMAND_STREAM_ASSEMBLY': CommandStreamAssembleRequest,
    'SOLVER_BATCH': SolverRunRequest,
    'RESULT_EXTRACTION': ResultExtractionRequest,
    'SOLVER_PARITY': SolverParityJobRequest,
    'BASELINE_DOE': EmptyJobRequest,
    'EXPERIMENT_DESIGN': ExperimentDesignRequest,
    'SURROGATE_TRAINING': SurrogateTrainingRequest,
    'ACTIVE_LEARNING': ActiveLearningInfillRequest,
    'MULTI_OBJECTIVE_OPTIMIZATION': MultiObjectiveOptimizationRequest,
    'ENTROPY_TOPSIS_DECISION': EntropyTopsisDecisionRequest,
    'OPTIMIZATION_EXPORT': OptimizationExportRequest,
    'WIND_WORKFLOW': EmptyJobRequest,
}


def validate_job_params(job_type: JobType, params: dict[str, Any]) -> dict[str, Any]:
    model_type = JOB_REQUEST_MODELS[job_type]
    if job_type == 'SOLVER_BATCH' and params.get('runMode') == 'REAL_DAMPER_PARAMETER_SWEEP':
        model_type = DamperParameterSweepRequest
    if job_type == 'MULTI_OBJECTIVE_OPTIMIZATION' and 'objectives' not in params:
        model_type = WorkflowExecutionJobRequest
    validated = model_type.model_validate(params)
    stored = validated.model_dump(by_alias=True, exclude_unset=True)
    resources = getattr(validated, 'resources', None)
    if isinstance(resources, SolverResources):
        stored.setdefault('resources', {})['executionTimeoutS'] = resources.execution_timeout_s
    return stored
