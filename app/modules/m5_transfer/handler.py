from fastapi import APIRouter, Depends, Request

from app.modules.m5_transfer.service import AUDIT_BASIS, FORMAL_BASIS
from app.problems import MESSAGES
from app.query import QueryRows, issues, snapshot

router = APIRouter(prefix='/api/transfer')

POLICY = {
    'formal_basis': FORMAL_BASIS,
    'formal_basis_label': '票面有效期（左闭右开），续期凭证可追溯生效，保证续期尽快接上',
    'audit_basis': AUDIT_BASIS,
    'audit_basis_label': '办理时刻，只承认进站时刻已办好（issued_at ≤ 进站时刻）的凭证',
    'disagreement_code': 'PASS_PENDING_AT_ENTRY',
    'disagreement_reason': MESSAGES['PASS_PENDING_AT_ENTRY'],
}


@router.get('/{run_id}')
def transfers(request: Request, run_id: str, query: QueryRows = Depends()):
    data = snapshot(request, run_id)
    rows = data['transfers']
    reviews = [row for row in rows if row['bases_disagree']]
    result = query.result({'main': rows, 'anchors': [row for row in rows if row['is_transfer']],
                           'reviews': reviews, 'passes': data['passes'], 'links': data['links'],
                           'isolated': issues(data, 'transfer')})
    result['policy'] = POLICY
    result['review_summary'] = {
        'entries': len(rows), 'transfers': sum(row['is_transfer'] for row in rows),
        'unique_entries': sum(row['unique_entry'] for row in rows),
        'bases_disagree': len(reviews),
        'audit_would_add_entries': sum(row['unique_entry'] for row in reviews),
        'audit_would_block_transfers': sum(1 for row in reviews if row['is_transfer']),
    }
    return result
