from __future__ import annotations

import re

class LLMUnavailableError(RuntimeError):
    """LLM 不可用；理解路径没有替代实现。"""

    STAGES = (
        'ROUTING',
        'INTENT',
        'CLARIFICATION',
        'CONVERSATION',
        'INQUIRY_PLANNING',
        'HARNESS',
        'CONTEXT_COMPRESSION',
        'FIGURE_PLANNING',
    )
    REASONS = (
        'LLM_NOT_CONFIGURED',
        'LLM_TIMEOUT',
        'LLM_CONNECTION_FAILED',
        'LLM_AUTH_FAILED',
        'LLM_SERVER_ERROR',
        'LLM_INVALID_RESPONSE',
        'LLM_BUDGET_EXHAUSTED',
    )

    def __init__(self, stage: str, reason: str, *, detail: str | None = None) -> None:
        super().__init__(f'{stage} 阶段需要大模型，当前不可用：{reason}')
        self.stage = stage
        self.reason = reason
        self.detail = detail

    @staticmethod
    def _safe_detail(detail: str | None) -> str:
        """给用户展示有限诊断，过滤认证信息和多行响应。"""

        text = str(detail or '').replace('\r', ' ').replace('\n', ' ').strip()[:320]
        text = re.sub(
            r'(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+',
            r'\1<redacted>',
            text,
        )
        text = re.sub(
            r'(?i)(api[_-]?key\s*[:=]\s*)[^\s,;]+',
            r'\1<redacted>',
            text,
        )
        return text

    @property
    def user_message(self) -> str:
        stage_label = {
            'ROUTING': '识别你的意图',
            'INTENT': '解析工程参数',
            'CLARIFICATION': '合并你补充的信息',
            'CONVERSATION': '生成对话回复',
            'INQUIRY_PLANNING': '规划结果查询',
            # harness 超时时工具往往已经返回了结果，失败的只是叙述那一步；
            # 落到"理解你的输入"会让用户以为提问没被读懂。
            'HARNESS': '生成结果说明',
            'CONTEXT_COMPRESSION': '压缩对话上下文',
            'FIGURE_PLANNING': '规划图表',
        }.get(self.stage, '理解你的输入')
        advice = {
            'LLM_NOT_CONFIGURED': '请在 .env 中配置 MOMO_LLM_BASE_URL、MOMO_LLM_MODEL 和 MOMO_LLM_API_KEY 后重启服务。',
            'LLM_TIMEOUT': f'模型服务在 {self.detail or "超时时间"} 内未响应，请稍后重试。',
            'LLM_CONNECTION_FAILED': '无法连接模型服务，请检查 MOMO_LLM_BASE_URL 是否可达。',
            'LLM_AUTH_FAILED': '模型服务拒绝了认证，请检查 MOMO_LLM_API_KEY。',
            'LLM_SERVER_ERROR': '模型服务返回错误，请稍后重试。',
            'LLM_INVALID_RESPONSE': '模型返回的内容不符合约定格式，请重试；若持续出现，请检查所用模型是否支持 JSON 输出。',
            # 与单次调用超时不是一回事：这条消息累计的多次模型调用把总预算用完了。
            'LLM_BUDGET_EXHAUSTED': (
                f'这条消息累计处理时间超过 {self.detail or "总预算"}，已停止重试；'
                '可以拆成更小的问题重问，或调大 MOMO_AGENT_MESSAGE_BUDGET_S。'
            ),
        }.get(self.reason, '请稍后重试。')
        if self.reason in {'LLM_CONNECTION_FAILED', 'LLM_SERVER_ERROR'} and self.detail:
            advice = f'{advice}（诊断：{self._safe_detail(self.detail)}）'
        return f'无法{stage_label}：{advice}'
