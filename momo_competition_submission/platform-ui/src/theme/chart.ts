/**
 * 图表配色集中入口。
 *
 * recharts 的 stroke/fill 需要真实色值，不能可靠地吃 CSS 变量，因此这里把
 * index.css 的 --chart-* token 以常量形式再声明一次。改配色时两边要一起改。
 */

/** 坐标轴与网格线，对应 --chart-grid / --chart-axis。 */
export const CHART_GRID = "#e5e2d9";
export const CHART_AXIS = "#6b6862";

/** 序列色，对应 --chart-1 … --chart-5，顺序即取用顺序。 */
export const CHART_COLORS = ["#c15f3c", "#8a5c00", "#197a3d", "#6b4fbb", "#b0446a"];

/** 图表内高亮描边（选中/推荐点），浅底上用深墨而非白色。 */
export const CHART_HIGHLIGHT_STROKE = "#1f1e1c";
