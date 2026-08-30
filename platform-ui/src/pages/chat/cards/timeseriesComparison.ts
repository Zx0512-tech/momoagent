import type { TimeseriesComparisonCase } from "../../../api/agentApi";

export type ComparisonChart = {
  column: string;
  rows: Array<Record<string, number | null>>;
};

/** 将参数案例转成时程曲线的图例，默认速度下限不参与工况区分。 */
export function formatComparisonCaseLabel(
  item: Pick<TimeseriesComparisonCase, "label" | "parameters">
): string {
  const labels: Record<string, string> = { alpha: "α" };
  const orderedNames = ["c", "alpha", "vfloor"];
  const parameterNames = [
    ...orderedNames.filter(name => name in item.parameters),
    ...Object.keys(item.parameters).filter(name => !orderedNames.includes(name)).sort()
  ].filter(name => {
    const value = item.parameters[name];
    const numericValue = Number(value);
    return name !== "vfloor"
      || !Number.isFinite(numericValue)
      || Math.abs(numericValue - 0.001) > Number.EPSILON;
  });

  if (parameterNames.length === 0) return item.label;
  return parameterNames
    .map(name => `${labels[name] ?? name}=${item.parameters[name]}`)
    .join("，");
}

export function buildComparisonChartData(
  cases: Array<Pick<TimeseriesComparisonCase, "caseId" | "series">>,
  columns: string[]
): ComparisonChart[] {
  return columns.map(column => {
    const rowsByTime = new Map<number, Record<string, number | null>>();
    cases.forEach(item => {
      const times = item.series.time ?? [];
      const values = item.series[column] ?? [];
      times.forEach((time, index) => {
        if (time === null || !Number.isFinite(time)) return;
        const row = rowsByTime.get(time) ?? { time };
        row[item.caseId] = values[index] ?? null;
        rowsByTime.set(time, row);
      });
    });
    return {
      column,
      rows: [...rowsByTime.entries()]
        .sort(([left], [right]) => left - right)
        .map(([, row]) => row)
    };
  });
}
