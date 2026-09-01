from __future__ import annotations

from app.services.agent_run_comparison import CrossRunComparisonService


def _candidate(rank: int, value: float) -> dict:
    return {
        'targetKey': f'agr_opt#rank:{rank}',
        'runId': 'agr_opt',
        'candidateRank': rank,
        'metrics': {
            'max_tower_base_shear': {
                'value': value,
                'unit': 'N',
                'label': '塔底剪力',
                'direction': 'LOWER_IS_BETTER',
                'evidence': {'artifactId': 'art_opt', 'candidateRank': rank},
            },
        },
    }


def test_rankings_keep_candidates_from_same_run_distinct() -> None:
    rankings = CrossRunComparisonService._rankings(
        [_candidate(1, 90.0), _candidate(2, 80.0), _candidate(3, 85.0)],
        ['max_tower_base_shear'],
    )

    rows = rankings[0]['rows']
    assert [item['candidateRank'] for item in rows] == [2, 3, 1]
    assert [item['targetKey'] for item in rows] == [
        'agr_opt#rank:2',
        'agr_opt#rank:3',
        'agr_opt#rank:1',
    ]
    assert all(item['runId'] == 'agr_opt' for item in rows)
