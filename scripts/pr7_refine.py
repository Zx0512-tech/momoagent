from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding='utf-8')
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{path}: expected one match, found {count}: {old!r}')
    p.write_text(text.replace(old, new), encoding='utf-8')


replace_once(
    'momo_agent/backend/app/services/agent_task_proposal.py',
    "ProposalState = Literal['NEEDS_CLARIFICATION', 'NEEDS_INPUT', 'READY_FOR_CONFIRMATION', 'BLOCKED']",
    "ProposalState = Literal['NEEDS_CLARIFICATION', 'NEEDS_INPUT', 'PLANNING', 'READY_FOR_CONFIRMATION', 'BLOCKED']",
)
replace_once(
    'momo_agent/backend/app/services/agent_task_proposal.py',
    "    elif str(run.get('status') or '') in {'WAITING_MAPPING', 'LOAD_STANDARDIZATION'}:\n        proposal_state = 'NEEDS_INPUT'\n    else:\n        proposal_state = 'READY_FOR_CONFIRMATION'\n",
    "    elif str(run.get('status') or '') in {'WAITING_MAPPING', 'LOAD_STANDARDIZATION'}:\n        proposal_state = 'NEEDS_INPUT'\n    elif preflight_passed is True or run.get('pendingApprovalId') or str(run.get('status') or '') == 'WAITING_APPROVAL':\n        proposal_state = 'READY_FOR_CONFIRMATION'\n    else:\n        proposal_state = 'PLANNING'\n",
)
replace_once(
    'momo_agent/backend/tests/test_agent_task_proposal.py',
    "    second = build_engineering_task_proposal(run)\n    assert first['proposalId'] == second['proposalId']",
    "    second = build_engineering_task_proposal(run)\n    assert first['proposalId'] == second['proposalId']\n    assert first['proposalState'] == 'PLANNING'\n    assert first['readyForApproval'] is False",
)
replace_once(
    'platform-ui/src/api/agentApi.ts',
    '  proposalState: "NEEDS_CLARIFICATION" | "NEEDS_INPUT" | "READY_FOR_CONFIRMATION" | "BLOCKED";',
    '  proposalState: "NEEDS_CLARIFICATION" | "NEEDS_INPUT" | "PLANNING" | "READY_FOR_CONFIRMATION" | "BLOCKED";',
)
replace_once(
    'platform-ui/src/pages/chat/cards/TaskProposalCard.tsx',
    '  NEEDS_INPUT: "待确认输入",\n  READY_FOR_CONFIRMATION: "待执行确认",',
    '  NEEDS_INPUT: "待确认输入",\n  PLANNING: "正在生成受控计划",\n  READY_FOR_CONFIRMATION: "待执行确认",',
)

print('PR7 proposal state refinement applied')
