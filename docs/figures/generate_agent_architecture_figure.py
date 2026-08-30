"""生成 MOMO 智能体整体功能架构图。"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch


# 保持 SVG 文字可编辑。
plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["font.sans-serif"] = ["Arial", "DejaVu Sans", "Liberation Sans"]
plt.rcParams["svg.fonttype"] = "none"
plt.rcParams["pdf.fonttype"] = 42
plt.rcParams["axes.unicode_minus"] = False


ROOT = Path(__file__).resolve().parent
OUTPUT_BASE = ROOT / "momo_agent_overall_architecture"

COLORS = {
    "ink": "#1E2A3A",
    "muted": "#59697A",
    "line": "#91A4B7",
    "blue": "#0F4D92",
    "blue_soft": "#EAF2FB",
    "teal": "#187E88",
    "teal_soft": "#E8F6F4",
    "amber": "#C98719",
    "amber_soft": "#FFF4DA",
    "green": "#2F7D58",
    "green_soft": "#EAF5EE",
    "red": "#B64342",
    "red_soft": "#FCEBE9",
    "panel": "#F7F9FC",
    "white": "#FFFFFF",
}


def chinese_font() -> FontProperties:
    """优先使用 Windows 中文字体，保证 Word 中的中文阅读体验。"""
    candidates = (
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\msyhbd.ttc"),
        Path(r"C:\Windows\Fonts\simsun.ttc"),
    )
    for candidate in candidates:
        if candidate.is_file():
            return FontProperties(fname=str(candidate))
    return FontProperties(family="DejaVu Sans")


CN_FONT = chinese_font()


def add_text(ax, x: float, y: float, text: str, *, size: float = 10, weight: str = "normal",
             color: str = COLORS["ink"], ha: str = "center", va: str = "center") -> None:
    """添加使用中文字体的图中文字。"""
    ax.text(
        x,
        y,
        text,
        fontproperties=CN_FONT,
        fontsize=size,
        fontweight=weight,
        color=color,
        ha=ha,
        va=va,
        linespacing=1.35,
        zorder=5,
    )


def rounded_box(ax, x: float, y: float, w: float, h: float, *, face: str, edge: str,
                radius: float = 0.018, linewidth: float = 1.35) -> None:
    """添加统一风格的圆角模块框。"""
    ax.add_patch(
        FancyBboxPatch(
            (x, y),
            w,
            h,
            boxstyle=f"round,pad=0.006,rounding_size={radius}",
            facecolor=face,
            edgecolor=edge,
            linewidth=linewidth,
            zorder=2,
        )
    )


def module_box(ax, x: float, y: float, w: float, h: float, *, title: str, body: str,
               face: str, edge: str, title_color: str | None = None, body_size: float = 8.5) -> None:
    """添加带标题和说明的功能模块。"""
    rounded_box(ax, x, y, w, h, face=face, edge=edge)
    add_text(
        ax,
        x + w / 2,
        y + h - 0.036,
        title,
        size=11,
        weight="bold",
        color=title_color or edge,
    )
    add_text(ax, x + w / 2, y + h / 2 - 0.025, body, size=body_size, color=COLORS["muted"])


def arrow(ax, start: tuple[float, float], end: tuple[float, float], *, color: str = COLORS["line"],
          width: float = 1.55, style: str = "solid", connection: str = "arc3,rad=0") -> None:
    """添加方向清晰的流程箭头。"""
    ax.add_patch(
        FancyArrowPatch(
            start,
            end,
            arrowstyle="-|>",
            mutation_scale=12,
            linewidth=width,
            color=color,
            linestyle=style,
            connectionstyle=connection,
            shrinkA=2,
            shrinkB=2,
            zorder=3,
        )
    )


def stage(ax, x: float, title: str, detail: str, number: int, *, color: str) -> None:
    """添加用户使用流程中的单个阶段。"""
    rounded_box(ax, x, 0.022, 0.205, 0.074, face=COLORS["white"], edge="#D9E2EC", radius=0.014, linewidth=1.0)
    ax.add_patch(plt.Circle((x + 0.026, 0.059), 0.015, facecolor=color, edgecolor="none", zorder=4))
    add_text(ax, x + 0.026, 0.059, str(number), size=7.6, weight="bold", color=COLORS["white"])
    add_text(ax, x + 0.123, 0.07, title, size=8.5, weight="bold", color=COLORS["ink"])
    add_text(ax, x + 0.123, 0.042, detail, size=6.6, color=COLORS["muted"])


def build_figure() -> plt.Figure:
    """构建突出智能体决策闭环的一页架构图。"""
    fig = plt.figure(figsize=(11.69, 8.27), facecolor="white")
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    # 标题区
    add_text(
        ax,
        0.045,
        0.954,
        "MOMO 工程智能体：从意图理解到证据回复的闭环逻辑",
        size=19,
        weight="bold",
        color=COLORS["blue"],
        ha="left",
    )
    add_text(
        ax,
        0.045,
        0.918,
        "核心不是单次计算，而是让智能体在受控工作流中持续理解、决策、调用工具并解释真实结果",
        size=9.3,
        color=COLORS["muted"],
        ha="left",
    )
    ax.plot([0.045, 0.955], [0.893, 0.893], color="#D5DFEA", linewidth=1.0, zorder=1)

    # 用户输入与可解释输出只占两侧，视觉中心保留给智能体闭环。
    module_box(
        ax,
        0.045,
        0.545,
        0.17,
        0.18,
        title="工程师 / 研究者",
        body="自然语言目标\n模型 / 荷载资料\n结果追问与确认",
        face=COLORS["blue_soft"],
        edge=COLORS["blue"],
        body_size=8.7,
    )
    module_box(
        ax,
        0.79,
        0.327,
        0.165,
        0.18,
        title="用户可见反馈",
        body="计划与参数澄清\n审批请求与实时进度\n报告、图表与证据",
        face=COLORS["green_soft"],
        edge=COLORS["green"],
        body_size=8.15,
    )

    # 主体：智能体认知、决策、调用与解释闭环。
    rounded_box(ax, 0.255, 0.305, 0.50, 0.50, face="#F7FAFE", edge="#B8CCE2", radius=0.026, linewidth=1.35)
    add_text(ax, 0.28, 0.777, "MOMO 工程智能体  ·  受控工作流运行时", size=13.8, weight="bold", color=COLORS["blue"], ha="left")
    add_text(ax, 0.28, 0.748, "会话记忆 · Run 状态 · 工程上下文 · 结构化工具调用", size=8.1, color=COLORS["muted"], ha="left")

    module_box(
        ax,
        0.285,
        0.60,
        0.19,
        0.10,
        title="① 理解工程意图",
        body="识别任务类型 · 提取参数\n发现缺失信息并追问",
        face=COLORS["white"],
        edge=COLORS["blue"],
        body_size=7.8,
    )
    module_box(
        ax,
        0.53,
        0.60,
        0.19,
        0.10,
        title="② 生成受控计划",
        body="选择已登记工作流\n冻结步骤、条件与资源预算",
        face=COLORS["white"],
        edge=COLORS["blue"],
        body_size=7.8,
    )

    rounded_box(ax, 0.285, 0.512, 0.435, 0.057, face=COLORS["red_soft"], edge=COLORS["red"], radius=0.012, linewidth=1.15)
    add_text(ax, 0.5025, 0.548, "智能体决策护栏：白名单工具 · 前置条件 · 资源上限 · 幂等键 · 高风险动作需审批", size=7.6, weight="bold", color=COLORS["red"])
    add_text(ax, 0.5025, 0.524, "任何一步不满足门槛，智能体回到澄清、预检或人工确认，而不是直接执行", size=6.8, color=COLORS["muted"])

    module_box(
        ax,
        0.285,
        0.365,
        0.19,
        0.105,
        title="③ 调用受控工具",
        body="依据当前步骤选择工具\n登记任务并观测状态流",
        face=COLORS["white"],
        edge=COLORS["teal"],
        body_size=7.8,
    )
    module_box(
        ax,
        0.53,
        0.365,
        0.19,
        0.105,
        title="④ 基于证据回复",
        body="提取结果 · 审查证据\n组织解释并支持继续追问",
        face=COLORS["white"],
        edge=COLORS["green"],
        body_size=7.8,
    )

    # 采用单一、分段的主流程，避免跨区弧线干扰阅读。
    arrow(ax, (0.215, 0.65), (0.285, 0.65), color=COLORS["blue"], width=1.9)
    add_text(ax, 0.25, 0.677, "需求 / 附件", size=7.2, color=COLORS["blue"])
    arrow(ax, (0.475, 0.65), (0.53, 0.65), color=COLORS["blue"], width=1.6)
    arrow(ax, (0.625, 0.60), (0.625, 0.569), color=COLORS["red"], width=1.6)
    arrow(ax, (0.38, 0.512), (0.38, 0.47), color=COLORS["teal"], width=1.8)
    arrow(ax, (0.475, 0.415), (0.53, 0.415), color=COLORS["teal"], width=1.65)
    arrow(ax, (0.72, 0.415), (0.79, 0.415), color=COLORS["green"], width=1.8)

    # 底部工具层服务于智能体决策，而不是图的主角。
    rounded_box(ax, 0.045, 0.09, 0.91, 0.15, face=COLORS["teal_soft"], edge="#A8D9D4", radius=0.02, linewidth=1.15)
    add_text(ax, 0.065, 0.21, "由智能体按步骤调用的受控工具与证据来源", size=10.2, weight="bold", color=COLORS["teal"], ha="left")
    tool_items = [
        ("输入检查与映射", "模型、荷载、目标校验", 0.19, COLORS["blue"]),
        ("真实有限元求解", "OpenSeesPy · 任务队列", 0.40, COLORS["teal"]),
        ("专项分析与优化", "比较、扫描、DOE、Pareto/TOPSIS", 0.61, COLORS["teal"]),
        ("结果与证据查询", "时程、报告、制品、SHA256", 0.82, COLORS["green"]),
    ]
    for title, detail, center_x, edge in tool_items:
        rounded_box(ax, center_x - 0.09, 0.112, 0.18, 0.07, face=COLORS["white"], edge=edge, radius=0.012, linewidth=1.0)
        add_text(ax, center_x, 0.154, title, size=8.1, weight="bold", color=edge)
        add_text(ax, center_x, 0.128, detail, size=6.5, color=COLORS["muted"])

    arrow(ax, (0.38, 0.365), (0.38, 0.24), color=COLORS["teal"], width=1.55)
    arrow(ax, (0.625, 0.24), (0.625, 0.365), color=COLORS["green"], width=1.55)

    # 图例帮助评审快速识别主从关系。
    add_text(ax, 0.045, 0.048, "蓝色：智能体理解与计划   |   红色：决策护栏   |   青绿：受控工具调用   |   绿色：证据与解释闭环", size=7.1, color=COLORS["muted"], ha="left")

    return fig


def main() -> None:
    """导出 Word 适配的矢量与高清位图版本。"""
    ROOT.mkdir(parents=True, exist_ok=True)
    fig = build_figure()
    svg_path = OUTPUT_BASE.with_suffix(".svg")
    fig.savefig(svg_path, bbox_inches="tight", pad_inches=0.08)
    # Matplotlib 的路径折行末尾可能有空格；移除它们以保持版本控制检查整洁。
    svg_path.write_text(
        "\n".join(line.rstrip() for line in svg_path.read_text(encoding="utf-8").splitlines()) + "\n",
        encoding="utf-8",
    )
    fig.savefig(OUTPUT_BASE.with_suffix(".pdf"), bbox_inches="tight", pad_inches=0.08)
    fig.savefig(OUTPUT_BASE.with_suffix(".png"), dpi=450, bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)


if __name__ == "__main__":
    main()
