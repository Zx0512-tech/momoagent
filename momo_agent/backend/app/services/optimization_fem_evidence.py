from __future__ import annotations

import math
from typing import Any


def selected_accepted_fem_review(
    summary: dict[str, Any],
    *,
    load_kind: str,
) -> dict[str, Any] | None:
    """返回 TOPSIS 最优候选的已通过真实 FEM 复核响应。"""
    optimization = summary.get('optimization')
    topsis = optimization.get('topsis') if isinstance(optimization, dict) else None
    best_index = topsis.get('best_index') if isinstance(topsis, dict) else None
    if isinstance(best_index, bool):
        return None
    try:
        pareto_index = int(best_index)
    except (TypeError, ValueError):
        return None
    return accepted_fem_review_for_candidate(
        summary,
        load_kind=load_kind,
        pareto_index=pareto_index,
    )


def accepted_fem_review_for_candidate(
    summary: dict[str, Any],
    *,
    load_kind: str,
    pareto_index: int,
) -> dict[str, Any] | None:
    """返回指定 Pareto 候选唯一、已验收的真实 FEM 响应。"""
    reviews = summary.get('review_records')
    if not isinstance(reviews, list):
        return None
    matched = [
        (index, review)
        for index, review in enumerate(reviews)
        if isinstance(review, dict)
        and review.get('accepted') is True
        and review.get('verified_execution') is True
        and isinstance(review.get('candidate'), dict)
        and review['candidate'].get('pareto_index') == pareto_index
    ]
    if len(matched) != 1:
        return None

    review_index, review = matched[0]
    results = [
        item for item in review.get('analysis_results') or []
        if isinstance(item, dict) and str(item.get('status') or '').lower() == 'completed'
    ]
    expected_case = str(load_kind or '').lower()
    selected = [
        item for item in results
        if str((item.get('load_case') or {}).get('name') or '').lower() == expected_case
    ]
    if len(selected) != 1:
        return None

    objectives = selected[0].get('objectives')
    if not isinstance(objectives, dict):
        return None
    normalized = {
        str(name).split(':', 1)[-1]: float(value)
        for name, value in objectives.items()
        if isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    }
    if not normalized:
        return None
    return {
        'objectives': normalized,
        'paretoIndex': pareto_index,
        'reviewRecordIndex': review_index,
        'caseId': selected[0].get('case_id'),
        'solver': selected[0].get('solver'),
    }
