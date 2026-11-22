from collections import defaultdict

from app.evidence import isolate, validated_groups
from app.fields import key, number, time_window, timestamp
from app.problems import BadRow


def parse_pass(row):
    ticket = key(row['ticket'])
    start, end = time_window(row, 'valid_from', 'valid_to')
    issued = timestamp(row['issued_at'])
    if row['transfer'].strip() not in {'yes', 'no'}:
        raise BadRow('BAD_TRANSFER_FLAG')
    return {'ticket': ticket, 'start_ms': start, 'end_ms': end, 'issued_ms': issued,
            'transfer': row['transfer'].strip() == 'yes'}


def parse_link(row):
    origin = key(row['from_station'])
    destination = key(row['to_station'])
    minimum = number(row['min_seconds'], 1, 3600)
    maximum = number(row['max_seconds'], 1, 3600)
    if origin == destination or minimum > maximum:
        raise BadRow('BAD_LINK')
    return {'from_station': origin, 'to_station': destination, 'min_ms': minimum * 1000, 'max_ms': maximum * 1000}


def _overlap(left, right):
    return left['start_ms'] < right['end_ms'] and right['start_ms'] < left['end_ms']


def load_passes(rows, issues):
    """同票多行构成续期链：按办理时刻排序后全部保留，逐行带原件出处。

    完全相同的导出只保留最早来源（PASS_DUPLICATE）；票面窗口相互重叠且
    换乘资格矛盾的整组隔离（PASS_CONFLICT），避免在互斥凭证间任选一条。
    """
    groups = defaultdict(list)
    for row in rows:
        try:
            value = parse_pass(row.data)
        except BadRow as exc:
            isolate(issues, row.origin(), str(exc), 'transfer', row.raw[0] if row.raw else '')
            continue
        groups[value['ticket']].append((value, row.origin()))

    retained, duplicates, conflicts = [], [], []
    for ticket, group in groups.items():
        ordered = sorted(group, key=lambda item: (item[0]['issued_ms'], item[1]['line']))
        first_value, first_origin = ordered[0]
        if all(alternative == first_value for alternative, _ in ordered[1:]):
            retained.append({**first_value, **first_origin})
            for _, current in ordered[1:]:
                isolate(issues, current, 'PASS_DUPLICATE', 'transfer', ticket,
                        retained_line=first_origin['line'])
                duplicates.append({**first_value, **current, 'retained_line': first_origin['line']})
            continue
        clashing = [
            (member, other)
            for index, member in enumerate(ordered)
            for other in ordered[index + 1:]
            if _overlap(member[0], other[0]) and member[0]['transfer'] != other[0]['transfer']
        ]
        if clashing:
            for value, origin in ordered:
                conflicts.append({**value, **origin})
                isolate(issues, origin, 'PASS_CONFLICT', 'transfer', ticket,
                        related_lines=[item[1]['line'] for item in ordered[:20]],
                        related_count=len(ordered))
            continue
        for serial, (value, origin) in enumerate(ordered):
            retained.append({**value, **origin, 'chain_index': serial, 'chain_size': len(ordered)})
    retained.sort(key=lambda row: (row['ticket'], row['start_ms'], row['line']))
    return retained, duplicates, conflicts


def load_rules(passes, links, issues):
    pass_rows, _, _ = load_passes(passes, issues)
    link_rows, _, _ = validated_groups(links, parse_link, lambda row: (row['from_station'], row['to_station']),
                                     'transfer', 'LINK', issues)
    return pass_rows, link_rows
