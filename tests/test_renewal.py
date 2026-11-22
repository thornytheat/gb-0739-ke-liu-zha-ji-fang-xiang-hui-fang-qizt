from app.fields import timestamp
from app.modules.m5_transfer.contracts import load_passes
from app.modules.m5_transfer.service import AUDIT_BASIS, FORMAL_BASIS, deduplicate, verdict
from app.evidence import Row


def pass_row(line, ticket, valid_from, valid_to, issued_at, transfer='yes'):
    data = {'ticket': ticket, 'valid_from': valid_from, 'valid_to': valid_to,
            'issued_at': issued_at, 'transfer': transfer}
    return Row('passes.csv', line, line, tuple(data.values()), data)


def entry(identifier, station, instant, ticket, line):
    return {'encounter': identifier, 'station': station, 'direction': 'entry', 'end_ms': instant,
            'ticket': ticket, 'gate': 'G1', 'pulses': [{'line': line}]}


def exit_motion(identifier, station, instant, ticket, line):
    return {'encounter': identifier, 'station': station, 'direction': 'exit', 'end_ms': instant,
            'ticket': ticket, 'gate': 'G1', 'pulses': [{'line': line}]}


def renewal_passes():
    rows = [
        pass_row(2, 'T1', '2026-11-20T00:00:00+08:00', '2026-11-20T00:03:00+08:00', '2026-11-19T23:00:00+08:00'),
        pass_row(3, 'T1', '2026-11-20T00:03:00+08:00', '2026-11-20T08:00:00+08:00', '2026-11-20T00:06:30+08:00'),
    ]
    passes, duplicates, conflicts = load_passes(rows, [])
    assert not duplicates and not conflicts and len(passes) == 2
    return passes


def test_renewal_chain_is_kept_instead_of_quarantined_as_conflict():
    passes = renewal_passes()
    assert [row['chain_index'] for row in passes] == [0, 1]
    assert {row['chain_size'] for row in passes} == {2}


def test_identical_renewal_export_is_duplicate_and_overlapping_flag_swap_is_conflict():
    identical = [pass_row(2, 'T2', '2026-11-20T00:00:00+08:00', '2026-11-20T08:00:00+08:00',
                          '2026-11-19T23:00:00+08:00'),
                 pass_row(3, 'T2', '2026-11-20T00:00:00+08:00', '2026-11-20T08:00:00+08:00',
                          '2026-11-19T23:00:00+08:00')]
    passes, duplicates, conflicts = load_passes(identical, [])
    assert len(passes) == 1 and len(duplicates) == 1 and not conflicts

    swapped = [pass_row(2, 'T3', '2026-11-20T00:00:00+08:00', '2026-11-20T08:00:00+08:00',
                        '2026-11-19T23:00:00+08:00', 'yes'),
               pass_row(3, 'T3', '2026-11-20T02:00:00+08:00', '2026-11-20T09:00:00+08:00',
                        '2026-11-20T01:00:00+08:00', 'no')]
    passes, duplicates, conflicts = load_passes(swapped, [])
    issues = []
    load_passes(swapped, issues)
    assert not passes and len(conflicts) == 2 and [item['code'] for item in issues] == ['PASS_CONFLICT', 'PASS_CONFLICT']


def test_formal_window_basis_allows_backdated_renewal_but_issued_basis_flags_it():
    passes = renewal_passes()
    entry_ms = timestamp('2026-11-20T00:05:10+08:00')
    formal_code, formal = verdict('T1', entry_ms, passes, FORMAL_BASIS)
    audit_code, audit = verdict('T1', entry_ms, passes, AUDIT_BASIS)
    assert formal_code == '' and formal['line'] == 3
    assert audit_code == 'PASS_PENDING_AT_ENTRY' and audit is None


def test_late_renewal_still_releases_transfer_and_records_the_disagreement():
    passes = renewal_passes()
    links = [{'from_station': 'NTH', 'to_station': 'STH', 'min_ms': 60_000, 'max_ms': 720_000, 'line': 2}]
    motions = [
        exit_motion('E-OUT', 'NTH', timestamp('2026-11-20T00:03:40+08:00'), 'T1', 10),
        entry('E-IN', 'STH', timestamp('2026-11-20T00:05:10+08:00'), 'T1', 11),
    ]
    decision, = deduplicate(motions, passes, links)
    assert decision['is_transfer'] is True and decision['unique_entry'] == 0
    assert decision['code'] == 'TRANSFER' and decision['pass_line'] == 3
    assert decision['audit_code'] == 'PASS_PENDING_AT_ENTRY' and decision['bases_disagree'] is True
    assert decision['headcount_effect'] == '换乘放行，不另计进站人次'
    assert [row['line'] for row in decision['pass_chain']] == [2, 3]


def test_prompt_renewal_agrees_across_both_bases():
    rows = [
        pass_row(2, 'T4', '2026-11-20T00:00:00+08:00', '2026-11-20T00:03:00+08:00', '2026-11-19T23:00:00+08:00'),
        pass_row(3, 'T4', '2026-11-20T00:03:00+08:00', '2026-11-20T08:00:00+08:00', '2026-11-20T00:02:00+08:00'),
    ]
    passes, _, _ = load_passes(rows, [])
    entry_ms = timestamp('2026-11-20T00:05:10+08:00')
    assert verdict('T4', entry_ms, passes, FORMAL_BASIS)[0] == ''
    assert verdict('T4', entry_ms, passes, AUDIT_BASIS)[0] == ''


def test_review_endpoint_lists_only_disagreements_with_policy_and_summary(client):
    response = client.get('/api/transfer/sample', params={'view': 'reviews'})
    assert response.status_code == 200
    body = response.json()
    row, = body['items']
    assert (row['ticket'], row['code'], row['audit_code']) == ('00123', 'TRANSFER', 'PASS_PENDING_AT_ENTRY')
    assert body['policy']['formal_basis'] == 'validity_window' and body['policy']['audit_basis'] == 'issued_at'
    assert body['review_summary'] == {'entries': 10, 'transfers': 2, 'unique_entries': 8,
                                      'bases_disagree': 1, 'audit_would_add_entries': 0,
                                      'audit_would_block_transfers': 1}


def test_sample_renewal_ticket_travels_from_pulse_to_original_pass_lines(sample):
    decision = next(row for row in sample['transfers'] if row['ticket'] == '00123')
    assert decision['anchor'] == 'E-RN-OUT-A' and decision['entry_line'] == 44
    assert (decision['pass_line'], decision['audit_pass_line']) == (9, None)
    pass_lines = {row['line'] for row in sample['passes'] if row['ticket'] == '00123'}
    assert pass_lines == {8, 9}
    raw = next(row for row in sample['passes'] if row['line'] == 9)['raw']
    assert raw[0] == '00123' and raw[3] == '2026-11-20T00:06:30+08:00'
