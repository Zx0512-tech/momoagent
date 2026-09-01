from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding='utf-8')
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{path}: expected one match, found {count}: {old[:120]!r}')
    target.write_text(text.replace(old, new, 1), encoding='utf-8')


path = 'momo_agent/backend/app/services/agent_project_context.py'

replace_once(
    path,
    "        # 自定义 FEM 模型当前只在 ANALYSIS 合同中可执行。其他任务仍把模型引用\n"
    "        # 作为 Project 上下文供选择历史结果，不把它强行灌入不支持的执行合同。\n"
    "        explicit_artifact = bool(re.search(r'art_[A-Za-z0-9_-]+', user_content))\n"
    "        if (\n"
    "            not explicit_artifact\n"
    "            and workspace.get('modelArtifactId')\n"
    "            and getattr(intent, 'task_type', None) == 'ANALYSIS'\n"
    "        ):\n"
    "            updates['model_artifact_id'] = workspace['modelArtifactId']\n"
    "            sources['modelArtifactId'] = 'PROJECT_WORKSPACE'\n"
    "        elif explicit_artifact:\n"
    "            sources['modelArtifactId'] = 'USER_SPECIFIED'\n",
    "        # PR4 Workspace 目前没有持久化自定义模型分析所需的 responseNodes /\n"
    "        # responseElementIds。只记住 modelArtifactId/SHA 用于历史匹配，不能单独\n"
    "        # 把模型引用灌入新 ANALYSIS，否则会制造不完整合同。用户本轮显式提供\n"
    "        # artifactId 时仍由原始 EngineeringIntent 负责传入并标记来源。\n"
    "        explicit_artifact = bool(re.search(r'art_[A-Za-z0-9_-]+', user_content))\n"
    "        if explicit_artifact:\n"
    "            sources['modelArtifactId'] = 'USER_SPECIFIED'\n",
)

replace_once(
    path,
    "        compact_summary = {\n"
    "            key: raw_summary.get(key)\n"
    "            for key in (\n"
    "                'evidenceMode', 'objectives', 'baselineObjectives', 'recommendedObjectives',\n"
    "                'responseComparison', 'validationStatus', 'reviewStatus',\n"
    "                'finalRecommendationStatus',\n"
    "            )\n"
    "            if raw_summary.get(key) is not None\n"
    "        }\n"
    "        if raw_summary.get('message'):\n"
    "            compact_summary['message'] = str(raw_summary['message'])[:500]\n",
    "        # Project memory only carries selection metadata. Engineering numbers and narrative\n"
    "        # stay behind registered result artifacts / inquiry tools, so bootstrap context can\n"
    "        # never become an alternate numeric evidence channel.\n"
    "        compact_summary = {\n"
    "            key: raw_summary.get(key)\n"
    "            for key in (\n"
    "                'evidenceMode', 'validationStatus', 'reviewStatus',\n"
    "                'finalRecommendationStatus',\n"
    "            )\n"
    "            if raw_summary.get(key) is not None\n"
    "        }\n",
)

print('PR5 safety refinements applied')
