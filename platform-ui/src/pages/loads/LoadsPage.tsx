import React, { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from "recharts";
import { CheckCircle2, Download, PlayCircle, RotateCcw, Save, Zap } from "lucide-react";
import {
  EARTHQUAKE_CODE_OPTIONS,
  EARTHQUAKE_SITE_CLASS_OPTIONS,
  LOAD_CURVE_EXPORT_FORMAT_OPTIONS,
  PEER_RECORD_OPTIONS,
  VEHICLE_LIBRARY_OPTIONS,
  WIND_START_TIME_OPTIONS
} from "../../api/engineeringOptions";
import type { EngineeringLoadConfig, EngineeringLoadSourceMode, LoadCurveExportFormat } from "../../api/types";
import { api, IS_MOCK_MODE } from "../../api/client";
import { VehicleLibraryDetail } from "../../components/engineering/VehicleLibraryDetail";
import { useEngineeringConfigStore } from "../../stores/engineeringConfigStore";
import { useJobStore } from "../../stores/jobStore";
import { CHART_AXIS, CHART_GRID } from "../../theme/chart";
import { ErrorPanel } from "../../components/feedback/ErrorPanel";
import { errorMessage } from "../../utils/errors";

type LoadTab = "earthquake" | "wind" | "traffic";

const TAB_ITEMS: Array<{ id: LoadTab; label: string }> = [
  { id: "earthquake", label: "地震荷载" },
  { id: "wind", label: "风荷载" },
  { id: "traffic", label: "车流荷载" }
];

const SOURCE_OPTIONS: Array<{ id: EngineeringLoadSourceMode; label: string }> = [
  { id: "INPUT_FILE", label: "输入文件" },
  { id: "GENERATED", label: "程序生成" }
];

interface LoadPreview {
  tab: LoadTab;
  title: string;
  dataKey: "acc" | "force" | "vehicles";
  unit: string;
  summary: string[];
  data: Array<{
    time: number;
    acc?: number;
    force?: number;
    vehicles?: number;
  }>;
}

const createTimeSamples = (durationS: number, timeStepS: number) => {
  const duration = Math.max(durationS || 1, 1);
  const rawCount = Math.floor(duration / Math.max(timeStepS || 0.01, 0.001)) + 1;
  const count = Math.min(180, Math.max(40, rawCount));
  const interval = count > 1 ? duration / (count - 1) : duration;
  return Array.from({ length: count }, (_, index) => Number((index * interval).toFixed(3)));
};

const clamp01 = (value: number) => Math.min(Math.max(value, 0), 1);

const createLoadPreview = (
  tab: LoadTab,
  config: EngineeringLoadConfig,
  vehicleLibrary?: typeof VEHICLE_LIBRARY_OPTIONS[number]
): LoadPreview => {
  if (tab === "earthquake") {
    const earthquake = config.earthquake;
    const durationS = earthquake.sourceMode === "INPUT_FILE" ? earthquake.fileInput.durationS : earthquake.codeGeneration.durationS;
    const timeStepS = earthquake.sourceMode === "INPUT_FILE" ? earthquake.fileInput.timeStepS : earthquake.codeGeneration.timeStepS;
    const scale = earthquake.sourceMode === "INPUT_FILE" ? 0.28 : 0.32 * earthquake.codeGeneration.pgaScaleFactor;
    const samples = createTimeSamples(durationS, timeStepS);
    return {
      tab,
      title: "加速度时程预览",
      dataKey: "acc",
      unit: earthquake.sourceMode === "INPUT_FILE" ? earthquake.fileInput.unit : "g",
      summary: [
        `持续时间 ${durationS}s`,
        `dt ${timeStepS}s`,
        `预览点 ${samples.length}`,
        `PGA 约 ${scale.toFixed(2)}g`
      ],
      data: samples.map(time => {
        const ratio = time / Math.max(durationS, 1);
        const envelope = Math.sin(Math.PI * Math.min(ratio, 1)) * Math.exp(-ratio * 0.35);
        const acc = scale * envelope * (Math.sin(time * 5.4) + 0.35 * Math.sin(time * 13.2));
        return { time, acc: Number(acc.toFixed(4)) };
      })
    };
  }

  if (tab === "wind") {
    const wind = config.wind;
    const durationS = wind.sourceMode === "INPUT_FILE" ? wind.fileInput.durationS : wind.generated.durationS;
    const timeStepS = wind.sourceMode === "INPUT_FILE" ? wind.fileInput.timeStepS : wind.generated.timeStepS;
    const meanWindSpeed = wind.sourceMode === "INPUT_FILE" ? 28 : wind.generated.meanWindSpeed;
    const turbulence = wind.sourceMode === "INPUT_FILE" ? 0.12 : wind.generated.turbulenceIntensity;
    const attackFactor = wind.sourceMode === "INPUT_FILE" ? 1 : 1 + Math.abs(wind.generated.attackAngleDeg) / 20;
    const baseForce = 0.12 * meanWindSpeed * meanWindSpeed * attackFactor;
    const samples = createTimeSamples(durationS, timeStepS);
    return {
      tab,
      title: "主梁竖向力时程预览",
      dataKey: "force",
      unit: "kN",
      summary: [
        `持续时间 ${durationS}s`,
        `dt ${timeStepS}s`,
        `平均风速 ${meanWindSpeed}m/s`,
        `湍流强度 ${turbulence}`
      ],
      data: samples.map(time => {
        const fluctuation = Math.sin(time * 0.42) + 0.45 * Math.sin(time * 1.15) + 0.2 * Math.cos(time * 2.1);
        const force = baseForce * (1 + turbulence * fluctuation);
        return { time, force: Number(force.toFixed(2)) };
      })
    };
  }

  const traffic = config.traffic;
  const durationS = traffic.sourceMode === "INPUT_FILE" ? traffic.fileInput.durationS : traffic.generated.durationS;
  const timeStepS = traffic.sourceMode === "INPUT_FILE" ? traffic.fileInput.timeStepS : traffic.generated.timeStepS;
  const laneCount = traffic.sourceMode === "INPUT_FILE" ? 4 : traffic.generated.laneCount;
  const trafficScale = traffic.sourceMode === "INPUT_FILE" ? 1 : Math.max(traffic.generated.trafficScale, 0);
  const heavyVehicleScale = traffic.sourceMode === "INPUT_FILE" ? 1 : Math.max(traffic.generated.heavyVehicleScale, 0);
  const baseTotalVehicles = vehicleLibrary?.totalVehicles24h ?? 0;
  const effectiveTotalVehicles = Math.round(baseTotalVehicles * trafficScale);
  const effectiveHeavyVehicleRatio = clamp01((vehicleLibrary?.heavyVehicleRatio ?? 0) * heavyVehicleScale);
  const samples = createTimeSamples(durationS, Math.max(timeStepS, durationS / 120));
  const previewInterval = samples.length > 1 ? samples[1] - samples[0] : timeStepS;
  return {
    tab,
    title: "车辆到达时间轴预览",
    dataKey: "vehicles",
    unit: "辆/预览步",
    summary: [
      `持续时间 ${durationS}s`,
      `dt ${timeStepS}s`,
      `车道 ${laneCount} 条`,
      `24h 总流量 ${effectiveTotalVehicles} 辆`,
      `重车比例 ${(effectiveHeavyVehicleRatio * 100).toFixed(1)}%`,
      "主梁双向加载"
    ],
    data: samples.map(time => {
      const hour = Math.floor((time / 3600) % 24);
      const libraryFlow = vehicleLibrary?.hourlyFlow[hour]?.vehiclesPerHour;
      const flowPerHour = traffic.sourceMode === "INPUT_FILE" ? 600 : (libraryFlow ?? 0) * trafficScale;
      const wave = 1 + 0.12 * Math.sin(time / 900) + 0.06 * Math.cos(time / 370);
      const vehicles = Math.max(0, Math.round((flowPerHour / 3600) * previewInterval * wave));
      return { time, vehicles };
    })
  };
};

const getLoadKind = (tab: LoadTab) => (
  tab === "earthquake" ? "EARTHQUAKE" : tab === "wind" ? "WIND" : "TRAFFIC"
);

const buildLoadGenerationRequest = (
  tab: LoadTab,
  config: EngineeringLoadConfig,
  vehicleLibrary?: typeof VEHICLE_LIBRARY_OPTIONS[number]
) => {
  if (tab === "earthquake") {
    const earthquake = config.earthquake;
    return {
      bridgeId: "stbridge",
      scenarioName: earthquake.sourceMode === "INPUT_FILE" ? earthquake.fileInput.fileName : "earthquake_code_spectrum",
      source: earthquake.sourceMode === "INPUT_FILE" ? "LOCAL_FILE" : "CODE_SPECTRUM",
      direction: earthquake.sourceMode === "INPUT_FILE" ? earthquake.fileInput.direction : "X",
      timeStepS: earthquake.sourceMode === "INPUT_FILE" ? earthquake.fileInput.timeStepS : earthquake.codeGeneration.timeStepS,
      durationS: earthquake.sourceMode === "INPUT_FILE" ? earthquake.fileInput.durationS : earthquake.codeGeneration.durationS,
      inputArtifactId: earthquake.sourceMode === "INPUT_FILE" ? earthquake.fileInput.artifactId : undefined,
      loadMapping: earthquake.sourceMode === "INPUT_FILE" ? {
        timeColumn: earthquake.fileInput.timeColumn,
        timeUnit: "s",
        valueColumn: earthquake.fileInput.valueColumn,
        sourceUnit: earthquake.fileInput.unit,
        quantity: "ACCELERATION",
        targetType: "GROUND",
        targetId: earthquake.fileInput.nodeMapping,
        component: earthquake.fileInput.direction.replace(/^U/, ""),
        consistentExcitation: earthquake.fileInput.consistentExcitation
      } : undefined,
      codeSpectrum: {
        code: earthquake.codeGeneration.code,
        siteClass: earthquake.codeGeneration.siteClass,
        dampingRatio: earthquake.codeGeneration.dampingRatio,
        peakGroundAcceleration: earthquake.codeGeneration.pgaScaleFactor
      },
      scaling: {
        method: earthquake.codeGeneration.spectrumMatch ? "SPECTRUM_MATCH" : "PGA",
        targetPeriodS: 1,
        periodRangeS: [0.2, 3] as [number, number],
        scaleFactor: earthquake.codeGeneration.pgaScaleFactor
      }
    };
  }

  if (tab === "wind") {
    const wind = config.wind;
    return {
      bridgeId: "stbridge",
      scenarioName: wind.sourceMode === "INPUT_FILE" ? wind.fileInput.fileName : "vertical_wind_generated",
      source: wind.sourceMode === "INPUT_FILE" ? "LOCAL_FILE" : "WIND_MODULE",
      appliedComponent: "VERTICAL" as const,
      inputArtifactId: wind.sourceMode === "INPUT_FILE" ? wind.fileInput.artifactId : undefined,
      loadMapping: wind.sourceMode === "INPUT_FILE" ? {
        timeColumn: wind.fileInput.timeColumn,
        timeUnit: "s",
        valueColumn: wind.fileInput.valueColumn,
        sourceUnit: wind.fileInput.unit,
        quantity: "FORCE",
        targetType: "NODE_GROUP",
        targetId: wind.fileInput.nodeMapping,
        component: wind.fileInput.direction
      } : undefined,
      windRequest: {
        sourceMode: wind.sourceMode,
        generated: wind.generated,
        fileInput: wind.fileInput
      },
      timeHistory: {
        startTime: wind.sourceMode === "INPUT_FILE" ? "2026-07-01T00:00:00+08:00" : wind.generated.startTime,
        durationS: wind.sourceMode === "INPUT_FILE" ? wind.fileInput.durationS : wind.generated.durationS,
        timeStepS: wind.sourceMode === "INPUT_FILE" ? wind.fileInput.timeStepS : wind.generated.timeStepS,
        seed: 20260702
      }
    };
  }

  const traffic = config.traffic;
  return {
    bridgeId: "stbridge",
    scenarioName: traffic.sourceMode === "INPUT_FILE" ? traffic.fileInput.fileName : "traffic_wim_2021_01",
    sourceMode: traffic.sourceMode === "INPUT_FILE" ? "LOAD_EXISTING" : "GENERATE_RANDOM",
    durationS: traffic.sourceMode === "INPUT_FILE" ? traffic.fileInput.durationS : traffic.generated.durationS,
    timeStepS: traffic.sourceMode === "INPUT_FILE" ? traffic.fileInput.timeStepS : traffic.generated.timeStepS,
    seed: traffic.sourceMode === "INPUT_FILE" ? 20260702 : traffic.generated.seed,
    trafficModel: "RANDOM_FLOW",
    vehicleLibraryId: traffic.generated.vehicleLibraryId || vehicleLibrary?.id,
    inputArtifactId: traffic.sourceMode === "INPUT_FILE" ? traffic.fileInput.artifactId : undefined,
    loadMapping: traffic.sourceMode === "INPUT_FILE" ? {
      timeColumn: traffic.fileInput.timeColumn,
      timeUnit: "s",
      valueColumn: traffic.fileInput.valueColumn,
      sourceUnit: traffic.fileInput.unit,
      quantity: "FORCE",
      targetType: "LANE_GROUP",
      targetId: traffic.fileInput.nodeMapping,
      component: traffic.fileInput.direction
    } : undefined,
    trafficScale: traffic.sourceMode === "INPUT_FILE" ? 1 : traffic.generated.trafficScale,
    heavyVehicleScale: traffic.sourceMode === "INPUT_FILE" ? 1 : traffic.generated.heavyVehicleScale,
    laneMode: "MAIN_GIRDER_BIDIRECTIONAL" as const,
    appliedStructure: "MAIN_GIRDER" as const,
    exportFormats: ["CSV", "JSON"]
  };
};

export const LoadsPage: React.FC = () => {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const {
    loadConfig,
    updateLoadConfig,
    validateModule
  } = useEngineeringConfigStore();
  const { submitJob, error: jobError, reportError } = useJobStore();
  const activeTab = (searchParams.get("tab") as LoadTab) || "earthquake";
  const [exportFormats, setExportFormats] = useState<LoadCurveExportFormat[]>(["PNG", "SVG"]);
  const [previewByTab, setPreviewByTab] = useState<Record<LoadTab, LoadPreview | null>>({
    earthquake: null,
    wind: null,
    traffic: null
  });

  const selectedVehicleLibrary = useMemo(() => (
    VEHICLE_LIBRARY_OPTIONS.find(item => item.id === loadConfig.traffic.generated.vehicleLibraryId)
  ), [loadConfig.traffic.generated.vehicleLibraryId]);
  const activePreview = previewByTab[activeTab];

  const setTab = (tab: LoadTab) => setSearchParams({ tab });

  const updateEarthquake = (patch: Partial<EngineeringLoadConfig["earthquake"]>) => {
    updateLoadConfig({ earthquake: { ...loadConfig.earthquake, ...patch, configured: true, validated: false } });
  };

  const updateWind = (patch: Partial<EngineeringLoadConfig["wind"]>) => {
    updateLoadConfig({ wind: { ...loadConfig.wind, ...patch, configured: true, validated: false } });
  };

  const updateTraffic = (patch: Partial<EngineeringLoadConfig["traffic"]>) => {
    updateLoadConfig({ traffic: { ...loadConfig.traffic, ...patch, configured: true, validated: false } });
  };

  const handleExportCurve = async () => {
    if (exportFormats.length === 0) return;
    try {
      const loadJobType = activeTab === "earthquake"
        ? "LOAD_EARTHQUAKE"
        : activeTab === "wind"
          ? "LOAD_WIND_VERTICAL"
          : "LOAD_TRAFFIC_RANDOM";
      const loadJob = await submitJob(
        loadJobType,
        buildLoadGenerationRequest(activeTab, loadConfig, selectedVehicleLibrary)
      );
      const sourceArtifact = loadJob.artifacts.find(artifact => artifact.kind === "LOAD_CASE") || loadJob.artifacts[0];
      if (!sourceArtifact) {
        throw new Error("荷载生成任务未返回可导出的制品");
      }
      await submitJob("LOAD_CURVE_EXPORT", {
        sourceArtifactId: sourceArtifact.artifactId,
        loadKind: getLoadKind(activeTab),
        curveComponent: activeTab === "wind" ? "girder_vertical_force" : activeTab,
        formats: exportFormats,
        stylePreset: "PROJECT_NATURE"
      });
    } catch (error) {
      reportError(errorMessage(error, "导出荷载曲线失败"));
    }
  };

  const handlePreviewCurve = () => {
    setPreviewByTab(prev => ({
      ...prev,
      [activeTab]: createLoadPreview(activeTab, loadConfig, selectedVehicleLibrary)
    }));
  };

  return (
    <div className="page-container">
      <div style={styles.header}>
        <div>
          <h1 style={styles.title}>荷载配置</h1>
          <p style={styles.subtitle}>地震、风、车流荷载均支持输入文件或程序生成；配置结果供命令流、批处理和结果提取复用。</p>
        </div>
        <div style={styles.headerActions}>
          <button className="btn btn-secondary" onClick={() => navigate("/")}>
            <RotateCcw size={14} />
            <span>返回主面板</span>
          </button>
          <button className="btn btn-primary" onClick={() => validateModule("LOADS")}>
            <CheckCircle2 size={14} />
            <span>校验荷载配置</span>
          </button>
        </div>
      </div>

      <div style={styles.tabs}>
        {TAB_ITEMS.map(tab => (
          <button
            key={tab.id}
            className={activeTab === tab.id ? "btn btn-primary" : "btn btn-secondary"}
            onClick={() => setTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div style={styles.mainGrid}>
        {jobError && <ErrorPanel message={jobError} />}
        <div className="panel" style={{ flex: 3 }}>
          <div className="panel-header">
            <span style={styles.panelTitle}>
              <Zap size={16} color="var(--primary-color)" />
              <span>{TAB_ITEMS.find(tab => tab.id === activeTab)?.label}配置</span>
            </span>
            <span className="badge badge-secondary">输入文件 / 程序生成</span>
          </div>

          {activeTab === "earthquake" && (
            <EarthquakePanel config={loadConfig.earthquake} onChange={updateEarthquake} />
          )}
          {activeTab === "wind" && (
            <WindPanel config={loadConfig.wind} onChange={updateWind} />
          )}
          {activeTab === "traffic" && (
            <TrafficPanel config={loadConfig.traffic} onChange={updateTraffic} />
          )}

          <div style={styles.footerActions}>
            <button
              className="btn btn-primary"
              onClick={() => {
                if (activeTab === "earthquake") updateEarthquake({ configured: true });
                if (activeTab === "wind") updateWind({ configured: true });
                if (activeTab === "traffic") updateTraffic({ configured: true });
              }}
            >
              <Save size={14} />
              <span>保存当前荷载配置</span>
            </button>
          </div>
        </div>

        <div style={styles.sideColumn}>
          <div className="panel">
            <div className="panel-header">
              <span style={{ fontWeight: 600 }}>时程预览</span>
              <button className="btn btn-secondary" style={styles.previewBtn} onClick={handlePreviewCurve}>
                <PlayCircle size={14} />
                <span>预览当前配置</span>
              </button>
            </div>
            {activePreview ? (
              <>
                <div style={styles.previewMeta}>
                  <span>{activePreview.title}</span>
                  <span>{activePreview.unit}</span>
                </div>
                <div style={styles.chartBox}>
                  <ResponsiveContainer width="100%" height={250}>
                    {activePreview.tab === "traffic" ? (
                      <AreaChart data={activePreview.data}>
                        <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} />
                        <XAxis dataKey="time" stroke={CHART_AXIS} />
                        <YAxis stroke={CHART_AXIS} />
                        <Tooltip />
                        <Area type="monotone" dataKey={activePreview.dataKey} name={activePreview.title} stroke="var(--primary-color)" fill="var(--primary-color)" fillOpacity={0.25} />
                      </AreaChart>
                    ) : (
                      <LineChart data={activePreview.data}>
                        <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} />
                        <XAxis dataKey="time" stroke={CHART_AXIS} />
                        <YAxis stroke={CHART_AXIS} />
                        <Tooltip />
                        <Line
                          type="monotone"
                          dataKey={activePreview.dataKey}
                          name={activePreview.title}
                          stroke="var(--primary-color)"
                          dot={false}
                          strokeWidth={1.5}
                        />
                      </LineChart>
                    )}
                  </ResponsiveContainer>
                </div>
                <StatsRow items={activePreview.summary} />
              </>
            ) : (
              <div style={styles.previewEmpty}>
                修改参数后点击“预览当前配置”，这里会按当前表单生成时程曲线。
              </div>
            )}
          </div>

          <div className="panel">
            <div className="panel-header">
              <span style={{ fontWeight: 600 }}>荷载曲线导出</span>
            </div>
            <div style={styles.exportGrid}>
              {LOAD_CURVE_EXPORT_FORMAT_OPTIONS.map(format => (
                <label key={format.id} style={styles.checkItem}>
                  <input
                    type="checkbox"
                    checked={exportFormats.includes(format.id)}
                    onChange={event => setExportFormats(prev => (
                      event.target.checked ? [...prev, format.id] : prev.filter(item => item !== format.id)
                    ))}
                  />
                  <span>{format.label}</span>
                </label>
              ))}
            </div>
            <button className="btn btn-secondary" style={{ marginTop: 12 }} onClick={handleExportCurve}>
              <Download size={14} />
              <span>导出曲线图</span>
            </button>
          </div>

          {activeTab === "traffic" && (
            <VehicleLibraryDetail library={selectedVehicleLibrary} />
          )}
        </div>
      </div>
    </div>
  );
};

const EarthquakePanel: React.FC<{
  config: EngineeringLoadConfig["earthquake"];
  onChange: (patch: Partial<EngineeringLoadConfig["earthquake"]>) => void;
}> = ({ config, onChange }) => (
  <>
    <SourceSelector value={config.sourceMode} onChange={sourceMode => onChange({ sourceMode })} />
    {config.sourceMode === "INPUT_FILE" ? (
      <FileInputFields kind="earthquake" config={config.fileInput} onChange={fileInput => onChange({ fileInput })} />
    ) : (
      <div style={styles.formGrid}>
        <Field label="规范类型">
          <select className="form-control" value={config.codeGeneration.code} onChange={event => onChange({ codeGeneration: { ...config.codeGeneration, code: event.target.value } })}>
            {EARTHQUAKE_CODE_OPTIONS.map(option => <option key={option.id} value={option.id}>{option.label}</option>)}
          </select>
        </Field>
        <Field label="设防烈度">
          <select className="form-control" value={config.codeGeneration.intensity} onChange={event => onChange({ codeGeneration: { ...config.codeGeneration, intensity: event.target.value } })}>
            <option value="VII">VII 度</option>
            <option value="VIII">VIII 度</option>
            <option value="IX">IX 度</option>
          </select>
        </Field>
        <Field label="场地类别">
          <select className="form-control" value={config.codeGeneration.siteClass} onChange={event => onChange({ codeGeneration: { ...config.codeGeneration, siteClass: event.target.value } })}>
            {EARTHQUAKE_SITE_CLASS_OPTIONS.map(option => <option key={option.id} value={option.id}>{option.label}</option>)}
          </select>
        </Field>
        <Field label="设计地震分组">
          <select className="form-control" value={config.codeGeneration.designGroup} onChange={event => onChange({ codeGeneration: { ...config.codeGeneration, designGroup: event.target.value } })}>
            <option value="第一组">第一组</option>
            <option value="第二组">第二组</option>
            <option value="第三组">第三组</option>
          </select>
        </Field>
        <Field label="阻尼比">
          <input className="form-control" type="number" step="0.01" value={config.codeGeneration.dampingRatio} onChange={event => onChange({ codeGeneration: { ...config.codeGeneration, dampingRatio: Number(event.target.value) } })} />
        </Field>
        <Field label="PGA 缩放系数">
          <input className="form-control" type="number" step="0.1" value={config.codeGeneration.pgaScaleFactor} onChange={event => onChange({ codeGeneration: { ...config.codeGeneration, pgaScaleFactor: Number(event.target.value) } })} />
        </Field>
        <Field label="PEER 地震波库">
          <select className="form-control">
            {PEER_RECORD_OPTIONS.map(option => <option key={option.id} value={option.id}>{option.label}</option>)}
          </select>
        </Field>
        <TimeWindowFields
          startTime={config.codeGeneration.startTime ?? "2026-07-01T00:00:00+08:00"}
          durationS={config.codeGeneration.durationS ?? 40}
          timeStepS={config.codeGeneration.timeStepS ?? 0.02}
          onChange={patch => onChange({ codeGeneration: { ...config.codeGeneration, ...patch } })}
        />
        <label style={styles.checkItem}>
          <input type="checkbox" checked={config.codeGeneration.spectrumMatch} onChange={event => onChange({ codeGeneration: { ...config.codeGeneration, spectrumMatch: event.target.checked } })} />
          <span>进行反应谱匹配</span>
        </label>
      </div>
    )}
    <StatsRow items={["PGA 0.32g", "PGV 0.42m/s", "PGD 0.11m", `dt ${config.codeGeneration.timeStepS ?? 0.02}s`, `duration ${config.codeGeneration.durationS ?? 40}s`, "points 2000"]} />
  </>
);

const WindPanel: React.FC<{
  config: EngineeringLoadConfig["wind"];
  onChange: (patch: Partial<EngineeringLoadConfig["wind"]>) => void;
}> = ({ config, onChange }) => (
  <>
    <SourceSelector value={config.sourceMode} onChange={sourceMode => onChange({ sourceMode })} />
    {config.sourceMode === "INPUT_FILE" ? (
      <FileInputFields kind="wind" config={config.fileInput} onChange={fileInput => onChange({ fileInput })} />
    ) : (
      <div style={styles.formGrid}>
        <Field label="平均风速 m/s">
          <input className="form-control" type="number" value={config.generated.meanWindSpeed} onChange={event => onChange({ generated: { ...config.generated, meanWindSpeed: Number(event.target.value) } })} />
        </Field>
        <Field label="风攻角 deg">
          <input className="form-control" type="number" value={config.generated.attackAngleDeg} onChange={event => onChange({ generated: { ...config.generated, attackAngleDeg: Number(event.target.value) } })} />
        </Field>
        <Field label="湍流强度">
          <input className="form-control" type="number" step="0.01" value={config.generated.turbulenceIntensity} onChange={event => onChange({ generated: { ...config.generated, turbulenceIntensity: Number(event.target.value) } })} />
        </Field>
        <Field label="风谱模型">
          <select className="form-control" value={config.generated.spectrumModel} onChange={event => onChange({ generated: { ...config.generated, spectrumModel: event.target.value as typeof config.generated.spectrumModel } })}>
            <option value="Davenport">Davenport</option>
            <option value="Kaimal">Kaimal</option>
          </select>
        </Field>
        <Field label="空间相关性参数">
          <input className="form-control" type="number" step="0.01" value={config.generated.spatialCorrelation} onChange={event => onChange({ generated: { ...config.generated, spatialCorrelation: Number(event.target.value) } })} />
        </Field>
        <Field label="生成时间">
          <select className="form-control" value={config.generated.startTime} onChange={event => onChange({ generated: { ...config.generated, startTime: event.target.value } })}>
            {WIND_START_TIME_OPTIONS.map(option => <option key={option.id} value={option.id}>{option.label}</option>)}
          </select>
        </Field>
        <Field label="持续时间 s">
          <input className="form-control" type="number" min={1} value={config.generated.durationS} onChange={event => onChange({ generated: { ...config.generated, durationS: Number(event.target.value) } })} />
        </Field>
        <Field label="时间步 dt s">
          <input className="form-control" type="number" min={0.001} step="0.01" value={config.generated.timeStepS} onChange={event => onChange({ generated: { ...config.generated, timeStepS: Number(event.target.value) } })} />
        </Field>
      </div>
    )}
    <div style={styles.notice}>当前 FEM 风荷载施加仅采用主梁竖向力；主梁横向力、扭矩和主塔阻力作为预览/扩展字段保留。</div>
    <StatsRow items={["主梁升力", "主梁阻力", "主梁扭矩", "主塔阻力", "对象：主梁/主塔/拉索"]} />
  </>
);

const TrafficPanel: React.FC<{
  config: EngineeringLoadConfig["traffic"];
  onChange: (patch: Partial<EngineeringLoadConfig["traffic"]>) => void;
}> = ({ config, onChange }) => (
  <>
    <SourceSelector value={config.sourceMode} onChange={sourceMode => onChange({ sourceMode })} />
    {config.sourceMode === "INPUT_FILE" ? (
      <FileInputFields kind="traffic" config={config.fileInput} onChange={fileInput => onChange({ fileInput })} />
    ) : (
      <div style={styles.formGrid}>
        <Field label="车道数量">
          <input className="form-control" type="number" value={config.generated.laneCount} onChange={event => onChange({ generated: { ...config.generated, laneCount: Number(event.target.value) } })} />
        </Field>
        <Field label="车辆库">
          <select className="form-control" value={config.generated.vehicleLibraryId} onChange={event => onChange({ generated: { ...config.generated, vehicleLibraryId: event.target.value } })}>
            {VEHICLE_LIBRARY_OPTIONS.map(option => <option key={option.id} value={option.id}>{option.label}</option>)}
          </select>
        </Field>
        <Field label="车速分布">
          <select className="form-control" value={config.generated.speedDistribution} onChange={event => onChange({ generated: { ...config.generated, speedDistribution: event.target.value } })}>
            <option value="正态分布">正态分布</option>
            <option value="对数正态分布">对数正态分布</option>
            <option value="固定速度">固定速度</option>
          </select>
        </Field>
        <Field label="车距分布">
          <select className="form-control" value={config.generated.headwayDistribution} onChange={event => onChange({ generated: { ...config.generated, headwayDistribution: event.target.value } })}>
            <option value="负指数分布">负指数分布</option>
            <option value="Erlang 分布">Erlang 分布</option>
            <option value="已有 24h 分时流量">已有 24h 分时流量</option>
          </select>
        </Field>
        <Field label="流量 scale">
          <input className="form-control" type="number" min={0} step="0.01" value={config.generated.trafficScale} onChange={event => onChange({ generated: { ...config.generated, trafficScale: Number(event.target.value) } })} />
        </Field>
        <Field label="重车 scale">
          <input className="form-control" type="number" min={0} step="0.01" value={config.generated.heavyVehicleScale} onChange={event => onChange({ generated: { ...config.generated, heavyVehicleScale: Number(event.target.value) } })} />
        </Field>
        <TimeWindowFields
          startTime={config.generated.startTime ?? "2026-07-01T00:00:00+08:00"}
          durationS={config.generated.durationS ?? 86400}
          timeStepS={config.generated.timeStepS ?? 1}
          onChange={patch => onChange({ generated: { ...config.generated, ...patch } })}
        />
        <Field label="随机种子">
          <input className="form-control" type="number" value={config.generated.seed} onChange={event => onChange({ generated: { ...config.generated, seed: Number(event.target.value) } })} />
        </Field>
        <Field label="桥面加载路径">
          <select className="form-control" value={config.generated.laneMapping} onChange={event => onChange({ generated: { ...config.generated, laneMapping: event.target.value } })}>
            <option value="主梁双向加载">主梁双向加载</option>
          </select>
        </Field>
      </div>
    )}
    <StatsRow items={["车辆到达时间轴", "主梁双向加载", "总车辆数", "重车比例", "summary artifact"]} />
  </>
);

const SourceSelector: React.FC<{
  value: EngineeringLoadSourceMode;
  onChange: (value: EngineeringLoadSourceMode) => void;
}> = ({ value, onChange }) => (
  <div style={styles.sourceRow}>
    {SOURCE_OPTIONS.map(option => (
      <button
        key={option.id}
        type="button"
        className={value === option.id ? "btn btn-primary" : "btn btn-secondary"}
        onClick={() => onChange(option.id)}
      >
        {option.label}
      </button>
    ))}
  </div>
);

const FileInputFields: React.FC<{
  kind: LoadTab;
  config: EngineeringLoadConfig["earthquake"]["fileInput"];
  onChange: (config: EngineeringLoadConfig["earthquake"]["fileInput"]) => void;
}> = ({ kind, config, onChange }) => {
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const unitOptions = kind === "earthquake" ? ["m/s2", "g"] : ["N", "kN"];

  const handleFile = async (file?: File) => {
    if (!file) return;
    setUploading(true);
    setUploadError(null);
    try {
      const uploaded = await api.uploadLoadArtifact(file);
      const format = uploaded.inspection.format;
      onChange({
        ...config,
        fileName: uploaded.fileName,
        artifactId: uploaded.artifactId,
        sourceSha256: uploaded.sha256,
        availableColumns: uploaded.inspection.columns.map(column => column.name),
        timeColumn: "",
        valueColumn: "",
        fileType: format === "XLSX" ? "EXCEL" : format === "TXT" ? "TXT" : "CSV"
      });
    } catch (error) {
      setUploadError(error instanceof Error ? error.message : "文件上传失败");
    } finally {
      setUploading(false);
    }
  };

  return <div style={styles.formGrid}>
    <Field label="输入文件">
      <input
        className="form-control"
        type="file"
        accept=".csv,.txt,.xlsx,.at1,.at2,.dat"
        disabled={uploading}
        onChange={event => void handleFile(event.target.files?.[0])}
      />
      <div style={{ fontSize: 11, marginTop: 4, color: uploadError ? "var(--error-color)" : "var(--text-muted)" }}>
        {uploadError || (config.artifactId
          ? `${IS_MOCK_MODE ? "模拟数据 · " : ""}${config.fileName} · Artifact ${config.artifactId}`
          : uploading ? "正在上传并检查列..." : "支持 CSV、TXT、XLSX，最大 20 MB")}
      </div>
    </Field>
    <Field label="文件类型">
      <select className="form-control" value={config.fileType} onChange={event => onChange({ ...config, fileType: event.target.value as typeof config.fileType })}>
        <option value="CSV">CSV</option>
        <option value="TXT">TXT</option>
        <option value="EXCEL">Excel</option>
        <option value="PROJECT_FORMAT">项目已有格式</option>
      </select>
    </Field>
    <Field label="时间列">
      <select className="form-control" value={config.timeColumn} onChange={event => onChange({ ...config, timeColumn: event.target.value })}>
        <option value="">请选择时间列</option>
        {config.availableColumns.map(column => <option key={column} value={column}>{column}</option>)}
      </select>
    </Field>
    <Field label="数值列">
      <select className="form-control" value={config.valueColumn} onChange={event => onChange({ ...config, valueColumn: event.target.value })}>
        <option value="">请选择数值列</option>
        {config.availableColumns.map(column => <option key={column} value={column}>{column}</option>)}
      </select>
    </Field>
    <Field label="时间步 dt">
      <input className="form-control" type="number" step="0.01" value={config.timeStepS} onChange={event => onChange({ ...config, timeStepS: Number(event.target.value) })} />
    </Field>
    <Field label="持续时间">
      <input className="form-control" type="number" value={config.durationS} onChange={event => onChange({ ...config, durationS: Number(event.target.value) })} />
    </Field>
    <Field label="单位">
      <select className="form-control" value={config.unit} onChange={event => onChange({ ...config, unit: event.target.value })}>
        {unitOptions.map(unit => <option key={unit} value={unit}>{unit}</option>)}
      </select>
    </Field>
    <Field label="方向">
      <select className="form-control" value={config.direction} onChange={event => onChange({ ...config, direction: event.target.value as typeof config.direction })}>
        <option value="UX">UX</option>
        <option value="UY">UY</option>
        <option value="UZ">UZ</option>
        <option value="X">X</option>
        <option value="Y">Y</option>
        <option value="Z">Z</option>
      </select>
    </Field>
    <Field label="节点映射关系">
      <select className="form-control" value={config.nodeMapping} onChange={event => onChange({ ...config, nodeMapping: event.target.value })}>
        <option value={config.nodeMapping}>{config.nodeMapping}</option>
        <option value="base_nodes">base_nodes</option>
        <option value="girder_vertical_nodes">girder_vertical_nodes</option>
        <option value="lane_to_girder_nodes">lane_to_girder_nodes</option>
      </select>
    </Field>
    {kind === "earthquake" && (
      <label style={styles.checkItem}>
        <input
          type="checkbox"
          checked={config.consistentExcitation}
          onChange={event => onChange({ ...config, consistentExcitation: event.target.checked })}
        />
        <span>一致激励（地基节点）</span>
      </label>
    )}
  </div>
};

const TimeWindowFields: React.FC<{
  startTime: string;
  durationS: number;
  timeStepS: number;
  onChange: (patch: { startTime?: string; durationS?: number; timeStepS?: number }) => void;
}> = ({ startTime, durationS, timeStepS, onChange }) => (
  <>
    <Field label="生成时间">
      <select className="form-control" value={startTime} onChange={event => onChange({ startTime: event.target.value })}>
        {WIND_START_TIME_OPTIONS.map(option => <option key={option.id} value={option.id}>{option.label}</option>)}
      </select>
    </Field>
    <Field label="持续时间 s">
      <input className="form-control" type="number" min={1} value={durationS} onChange={event => onChange({ durationS: Number(event.target.value) })} />
    </Field>
    <Field label="时间步 dt s">
      <input className="form-control" type="number" min={0.001} step="0.01" value={timeStepS} onChange={event => onChange({ timeStepS: Number(event.target.value) })} />
    </Field>
  </>
);

const Field: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <div className="form-group">
    <label className="form-label">{label}</label>
    {children}
  </div>
);

const StatsRow: React.FC<{ items: string[] }> = ({ items }) => (
  <div style={styles.statsRow}>
    {items.map(item => <span key={item} className="badge badge-secondary">{item}</span>)}
  </div>
);

const styles: Record<string, React.CSSProperties> = {
  header: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    borderBottom: "1px solid var(--border-color)",
    paddingBottom: 14
  },
  title: {
    fontSize: 18,
    fontWeight: 700,
    color: "var(--text-primary)"
  },
  subtitle: {
    fontSize: 12,
    color: "var(--text-secondary)",
    marginTop: 4
  },
  headerActions: {
    display: "flex",
    gap: 8
  },
  tabs: {
    display: "flex",
    gap: 8
  },
  mainGrid: {
    display: "flex",
    gap: 16
  },
  panelTitle: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    fontWeight: 600
  },
  sourceRow: {
    display: "flex",
    gap: 8,
    marginBottom: 16
  },
  formGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
    gap: 12
  },
  checkItem: {
    display: "flex",
    alignItems: "center",
    gap: 6,
    fontSize: 12,
    color: "var(--text-secondary)"
  },
  notice: {
    marginTop: 14,
    border: "1px solid var(--border-color)",
    borderRadius: 4,
    padding: "10px 12px",
    backgroundColor: "var(--bg-primary)",
    color: "var(--text-secondary)",
    fontSize: 12
  },
  statsRow: {
    display: "flex",
    flexWrap: "wrap",
    gap: 8,
    marginTop: 14
  },
  footerActions: {
    display: "flex",
    justifyContent: "flex-end",
    marginTop: 16
  },
  sideColumn: {
    flex: 2,
    display: "flex",
    flexDirection: "column",
    gap: 16
  },
  chartBox: {
    backgroundColor: "var(--bg-primary)",
    border: "1px solid var(--border-color)",
    borderRadius: 4,
    padding: "10px 8px 0 0"
  },
  previewBtn: {
    padding: "4px 10px",
    fontSize: 12
  },
  previewMeta: {
    display: "flex",
    justifyContent: "space-between",
    color: "var(--text-secondary)",
    fontSize: 12,
    marginBottom: 8
  },
  previewEmpty: {
    border: "1px dashed var(--border-color)",
    borderRadius: 4,
    backgroundColor: "var(--bg-primary)",
    color: "var(--text-secondary)",
    fontSize: 12,
    padding: "42px 18px",
    textAlign: "center"
  },
  exportGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
    gap: 8
  }
};

export default LoadsPage;
