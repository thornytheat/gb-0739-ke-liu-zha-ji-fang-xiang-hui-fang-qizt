from collections import defaultdict, deque

from app.problems import MESSAGES

# 班报正式口径按票面有效期认定；办理时刻审计口径只认进站时已办好的凭证。
FORMAL_BASIS = 'validity_window'
AUDIT_BASIS = 'issued_at'


def _chain(passes, ticket):
    if not ticket:
        return []
    return sorted((row for row in passes if row['ticket'] == ticket),
                  key=lambda row: (row['start_ms'], row['issued_ms'], row['line']))


def _covering(chain, instant, basis):
    """同一时刻可能有多条续期凭证覆盖（窗口衔接或追溯重叠），取最晚办理的一条。"""
    candidates = [
        row for row in chain
        if row['start_ms'] <= instant < row['end_ms']
        and (basis == FORMAL_BASIS or row['issued_ms'] <= instant)
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda row: (row['issued_ms'], row['line']))[-1]


def verdict(ticket, instant, passes, basis=FORMAL_BASIS):
    """返回 (原因码, 命中凭证)。空原因码表示该口径下凭证有效且允许换乘。"""
    if not ticket:
        return 'NO_TICKET', None
    chain = _chain(passes, ticket)
    if not chain:
        return 'PASS_UNKNOWN', None
    selected = _covering(chain, instant, basis)
    if selected is None:
        if basis == AUDIT_BASIS and any(row['start_ms'] <= instant < row['end_ms'] for row in chain):
            return 'PASS_PENDING_AT_ENTRY', None
        return 'PASS_EXPIRED', None
    if not selected['transfer']:
        return 'PASS_NOT_TRANSFERABLE', selected
    return '', selected


def deduplicate(readings, passes, links):
    links = {(row['from_station'], row['to_station']): row for row in links}
    prior, choices = defaultdict(deque), []
    movements = (row for row in readings if row['direction'] in {'exit', 'entry'})
    for motion in sorted(movements, key=lambda row: (row['end_ms'], row['encounter'])):
        ticket, instant, station = motion['ticket'], motion['end_ms'], motion['station']
        credential_code, record = verdict(ticket, instant, passes)
        if motion['direction'] == 'exit':
            if not credential_code:
                prior[ticket].append(motion)
            continue
        audit_code, audit_record = verdict(ticket, instant, passes, AUDIT_BASIS)
        # 口径分歧只来自凭证采信层（票面有效 vs 办理时刻未生效）；锚点匹配与口径无关。
        disagree = credential_code != audit_code
        code = credential_code
        anchor, link = None, None
        if not code:
            exits = prior[ticket]
            while exits and instant - exits[0]['end_ms'] > 3_600_000:
                exits.popleft()
            for previous in reversed(exits):
                route = links.get((previous['station'], station))
                duration = instant - previous['end_ms']
                if route and route['min_ms'] <= duration <= route['max_ms']:
                    anchor, link = previous, route
                    exits.remove(previous)
                    break
            code = 'TRANSFER' if anchor else 'NO_ELIGIBLE_EXIT'
        choices.append({
            'encounter': motion['encounter'], 'station': station, 'gate': motion['gate'],
            'ticket': ticket, 'at_ms': instant, 'code': code, 'reason': MESSAGES[code], 'is_transfer': bool(anchor),
            'credential_code': credential_code, 'unique_entry': int(anchor is None),
            'headcount_effect': '换乘放行，不另计进站人次' if anchor else '计为独立进站（+1 人次）',
            'entry_line': motion['pulses'][-1]['line'],
            'anchor': anchor['encounter'] if anchor else None, 'from_station': anchor['station'] if anchor else None,
            'anchor_ms': anchor['end_ms'] if anchor else None,
            'anchor_line': anchor['pulses'][-1]['line'] if anchor else None,
            'gap_ms': instant - anchor['end_ms'] if anchor else None,
            'link_line': link['line'] if link else None,
            'pass_line': record['line'] if record else None,
            'pass_issued_ms': record['issued_ms'] if record else None,
            'pass_start_ms': record['start_ms'] if record else None,
            'pass_end_ms': record['end_ms'] if record else None,
            'pass_chain_index': record.get('chain_index') if record else None,
            'pass_chain_size': record.get('chain_size') if record else None,
            'audit_basis': AUDIT_BASIS, 'audit_code': audit_code,
            'audit_reason': MESSAGES.get(audit_code, ''), 'audit_pass_line': audit_record['line'] if audit_record else None,
            'bases_disagree': disagree,
            'pass_chain': [
                {'line': row['line'], 'start_ms': row['start_ms'], 'end_ms': row['end_ms'],
                 'issued_ms': row['issued_ms'], 'transfer': row['transfer'],
                 'chain_index': row.get('chain_index'), 'chain_size': row.get('chain_size')}
                for row in _chain(passes, ticket)
            ],
        })
    return choices
