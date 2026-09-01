from __future__ import annotations

import csv
import io
import json
import math
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import Any, Protocol

import matplotlib

matplotlib.use('Agg')

import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties, fontManager

from app.agents.figure_contracts import (
    ArtifactRef,
    FigureContract,
    FigurePanel,
    FigureRenderInput,
    FigureRenderOutput,
)
from app.agents.inquiry import RESPONSE_COLUMN_ALIASES, RESPONSE_METRIC_SPECS, resolve_column


class FigureArtifactStore(Protocol):
    artifacts: list[Any]

    def get_artifact(self, artifact_id: str) -> Any: ...

    def register_artifact(self, **kwargs: Any) -> Any: ...


class AgentFigureService:
    """从登记 CSV Artifact 生成可追溯的 Python 图件包。"""

    _MIME_TYPES = {
        'PNG': 'image/png',
        'SVG': 'image/svg+xml',
        'PDF': 'application/pdf',
        'TIFF': 'image/tiff',
    }
    _MAX_SOURCE_BYTES = 64 * 1024 * 1024
    _MAX_SOURCE_ROWS = 500_000
    _render_lock = Lock()

    def __init__(self, store: FigureArtifactStore) -> None:
        self.store = store

    def build_render_input(
        self,
        *,
        run_id: str,
        figure_request: Any,
        catalog: dict[str, Any],
    ) -> FigureRenderInput:
        """把 LLM 的受限 FigureRequest 翻译为确定性的 FigureContract。"""
        metrics = list(figure_request.metrics)
        unknown = [metric for metric in metrics if metric not in RESPONSE_METRIC_SPECS]
        if unknown:
            raise ValueError(f'绘图指标不在受控目录中: {unknown}')
        artifact_name = str(figure_request.artifact)
        artifact_info = (catalog.get('artifacts') or {}).get(artifact_name)
        if not isinstance(artifact_info, dict):
            raise ValueError(f'绘图源文件不可用: {artifact_name}')
        artifact_id = artifact_info.get('artifactId')
        columns = list(artifact_info.get('columns') or [])
        if not artifact_id:
            raise ValueError(f'绘图源文件缺少 artifactId: {artifact_name}')
        x_field = str(figure_request.x_field or 'time')
        if x_field not in columns:
            raise ValueError(f'绘图源文件不存在横轴列: {x_field}')
        y_fields: list[str] = []
        labels: list[str] = []
        units: list[str] = []
        for metric_id in metrics:
            spec = RESPONSE_METRIC_SPECS[metric_id]
            column = resolve_column(columns, spec['semantic'], RESPONSE_COLUMN_ALIASES)
            if not column:
                raise ValueError(f'指标 {metric_id} 在 {artifact_name} 中没有可绘制列')
            y_fields.append(column)
            labels.append(str(spec['label']))
            units.append(str(spec['unit']))
        source_record = self.store.get_artifact(str(artifact_id))
        source = source_record.artifact
        if source.kind not in {'CSV_TIMESERIES', 'CSV_TABLE'}:
            raise ValueError('绘图源 Artifact 不是已登记 CSV')
        source_ref = self._artifact_ref(source)
        y_label = ' / '.join(
            f'{label}（{unit}）' if unit else label
            for label, unit in zip(labels, units)
        )
        contract = FigureContract(
            claim=str(figure_request.claim),
            source_artifacts=(source_ref,),
            panels=(FigurePanel(
                panel_id='panel_1',
                evidence_role=str(figure_request.claim),
                x_field=x_field,
                y_fields=tuple(y_fields),
                x_label='时间（s）' if x_field == 'time' else x_field,
                y_label=y_label,
            ),),
            export_formats=tuple(figure_request.export_formats),
        )
        return FigureRenderInput(run_id=run_id, contract=contract)

    def render(self, request: FigureRenderInput) -> FigureRenderOutput:
        if len(request.contract.source_artifacts) != 1:
            raise ValueError('首版 figure.render 每张图只接受一个登记 source Artifact')
        source_ref = request.contract.source_artifacts[0]
        source_record = self.store.get_artifact(source_ref.artifact_id)
        source = source_record.artifact
        if source.kind not in {'CSV_TIMESERIES', 'CSV_TABLE'}:
            raise ValueError('figure.render 只接受已登记 CSV 结果 Artifact')
        if source.kind != source_ref.kind:
            raise ValueError('source Artifact 类型与 figure contract 不一致')
        if source.sha256 != source_ref.sha256:
            raise ValueError('source Artifact SHA256 与 figure contract 不一致')

        content_sha256 = sha256(source_record.content).hexdigest()
        if content_sha256 != source.sha256:
            raise ValueError('source Artifact 内容 SHA256 与登记元数据不一致')

        contract_sha256 = self._contract_sha256(request)
        with self._render_lock:
            existing = self._existing_bundle(request, contract_sha256)
            if existing is not None:
                return FigureRenderOutput(
                    figure_artifacts=existing,
                    source_data_artifact=self._artifact_ref(source),
                    contract_sha256=contract_sha256,
                )

            columns = self._numeric_columns(source_record.content, request)
            figure = self._build_figure(columns, request)
            try:
                figure_artifacts = tuple(
                    self._register_export(figure, request, contract_sha256, format_name)
                    for format_name in request.contract.export_formats
                )
            finally:
                plt.close(figure)
            return FigureRenderOutput(
                figure_artifacts=figure_artifacts,
                source_data_artifact=self._artifact_ref(source),
                contract_sha256=contract_sha256,
            )

    @staticmethod
    def _contract_sha256(request: FigureRenderInput) -> str:
        payload = request.contract.model_dump(mode='json')
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        return sha256(raw.encode('utf-8')).hexdigest()

    def _existing_bundle(
        self,
        request: FigureRenderInput,
        contract_sha256: str,
    ) -> tuple[ArtifactRef, ...] | None:
        prefix = self._output_prefix(request.run_id, contract_sha256)
        records = {
            str(record.artifact.path).removeprefix(prefix).upper(): record
            for record in self.store.artifacts
            if str(record.artifact.path).startswith(prefix)
        }
        expected = tuple(request.contract.export_formats)
        if not all(format_name in records for format_name in expected):
            return None
        return tuple(self._artifact_ref(records[format_name].artifact) for format_name in expected)

    @staticmethod
    def _numeric_columns(content: bytes, request: FigureRenderInput) -> dict[str, list[float]]:
        if len(content) > AgentFigureService._MAX_SOURCE_BYTES:
            raise ValueError('source CSV 超过 figure.render 大小上限')
        required_fields = {
            field
            for panel in request.contract.panels
            for field in (panel.x_field, *panel.y_fields)
        }
        reader = csv.DictReader(io.StringIO(content.decode('utf-8-sig'), newline=''))
        if reader.fieldnames is None:
            raise ValueError('source CSV 缺少表头')
        missing = sorted(required_fields - set(reader.fieldnames))
        if missing:
            raise ValueError(f'source CSV 缺少图件字段: {missing}')
        columns = {field: [] for field in required_fields}
        for row_index, row in enumerate(reader, start=2):
            if row_index > AgentFigureService._MAX_SOURCE_ROWS + 1:
                raise ValueError('source CSV 超过 figure.render 行数上限')
            for field in required_fields:
                try:
                    value = float(row[field])
                except (TypeError, ValueError) as exc:
                    raise ValueError(f'source CSV 第 {row_index} 行字段 {field} 不是数值') from exc
                if not math.isfinite(value):
                    raise ValueError(f'source CSV 第 {row_index} 行字段 {field} 不是有限值')
                columns[field].append(value)
        if not columns or not next(iter(columns.values())):
            raise ValueError('source CSV 没有可绘制数据行')
        return columns

    @staticmethod
    def _font_properties() -> FontProperties:
        for candidate in (
            Path(r'C:\Windows\Fonts\simsun.ttc'),
            Path(r'C:\Windows\Fonts\simhei.ttf'),
            Path(r'C:\Windows\Fonts\msyh.ttc'),
        ):
            if candidate.exists():
                fontManager.addfont(str(candidate))
                return FontProperties(fname=str(candidate))
        return FontProperties(family='DejaVu Sans')

    def _build_figure(self, columns: dict[str, list[float]], request: FigureRenderInput):
        font_prop = self._font_properties()
        panel_count = len(request.contract.panels)
        with plt.rc_context({
            'font.family': 'sans-serif',
            'font.sans-serif': [font_prop.get_name(), 'Arial', 'DejaVu Sans'],
            'axes.unicode_minus': False,
            'svg.fonttype': 'none',
            'pdf.fonttype': 42,
            'font.size': 7,
            'axes.linewidth': 0.8,
            'legend.frameon': False,
        }):
            figure, axes_raw = plt.subplots(
                panel_count,
                1,
                figsize=(183 / 25.4, max(48 * panel_count, 60) / 25.4),
                squeeze=False,
            )
            axes = axes_raw[:, 0]
            line_styles = ('-', (0, (5, 3)), (0, (3, 2, 1, 2)))
            colors = ('#111111', '#5C5C5C', '#8A8A8A')
            for axis, panel in zip(axes, request.contract.panels):
                for index, field in enumerate(panel.y_fields):
                    axis.plot(
                        columns[panel.x_field],
                        columns[field],
                        color=colors[index % len(colors)],
                        linestyle=line_styles[index % len(line_styles)],
                        linewidth=1.0,
                        label=field,
                    )
                for spine in axis.spines.values():
                    spine.set_visible(True)
                    spine.set_color('black')
                    spine.set_linewidth(0.8)
                axis.tick_params(direction='in', top=True, right=True, width=0.8, length=3)
                axis.grid(
                    True,
                    which='major',
                    color='#D0D0D0',
                    linestyle=(0, (4, 4)),
                    linewidth=0.5,
                    alpha=0.85,
                )
                axis.set_axisbelow(True)
                axis.set_xlabel(panel.x_label, fontproperties=font_prop, fontsize=7)
                axis.set_ylabel(panel.y_label, fontproperties=font_prop, fontsize=7)
                axis.text(
                    -0.06,
                    1.02,
                    panel.panel_id,
                    transform=axis.transAxes,
                    fontproperties=font_prop,
                    fontsize=8,
                    fontweight='bold',
                    va='bottom',
                )
                if len(panel.y_fields) > 1:
                    axis.legend(fontsize=6)
            figure.align_ylabels(axes)
            figure.tight_layout(pad=1.0)
            return figure

    def _register_export(
        self,
        figure: Any,
        request: FigureRenderInput,
        contract_sha256: str,
        format_name: str,
    ) -> ArtifactRef:
        extension = format_name.lower()
        buffer = io.BytesIO()
        with plt.rc_context({'svg.fonttype': 'none', 'pdf.fonttype': 42, 'axes.unicode_minus': False}):
            figure.savefig(
                buffer,
                format='tiff' if format_name == 'TIFF' else extension,
                dpi=600 if format_name == 'TIFF' else 300,
                bbox_inches='tight',
            )
        content = buffer.getvalue()
        path = f'{self._output_prefix(request.run_id, contract_sha256)}{extension}'
        artifact = self.store.register_artifact(
            kind='PLOT',
            name=f'analysis_figure.{extension}',
            path=path,
            mime_type=self._MIME_TYPES[format_name],
            preview={
                'runId': request.run_id,
                'claim': request.contract.claim,
                'backend': request.contract.backend,
                'contractSha256': contract_sha256,
                'sourceArtifactIds': [
                    item.artifact_id for item in request.contract.source_artifacts
                ],
                'panels': [panel.model_dump(mode='json') for panel in request.contract.panels],
            },
            content=content,
        )
        return self._artifact_ref(artifact)

    @staticmethod
    def _output_prefix(run_id: str, contract_sha256: str) -> str:
        return f'output/agent_figures/{run_id}/{contract_sha256}/analysis_figure.'

    @staticmethod
    def _artifact_ref(artifact: Any) -> ArtifactRef:
        return ArtifactRef(
            artifact_id=artifact.artifact_id,
            kind=artifact.kind,
            sha256=artifact.sha256,
        )
