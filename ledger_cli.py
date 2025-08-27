#!/usr/bin/env python3
"""CRDT audit ledger CLI for peptide design decisions."""

import argparse
import hashlib
import json
import os
import sys
import time
from typing import Dict, List, Tuple
import heapq
import operator

# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def load_events(path: str) -> List[dict]:
    events: List[dict] = []
    if not os.path.exists(path):
        return events
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def write_events(path: str, events: List[dict]) -> None:
    with open(path, 'w', encoding='utf-8') as f:
        for e in events:
            f.write(json.dumps(e, sort_keys=True) + "\n")


def canonical_json(event: dict) -> str:
    data = {k: event[k] for k in event if k not in ('event_id', 'sig')}
    return json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def compute_event_id(event: dict) -> str:
    return hashlib.blake2s(canonical_json(event).encode('utf-8')).hexdigest()


def compute_sig(event: dict, key: bytes) -> str:
    return hashlib.blake2s(canonical_json(event).encode('utf-8'), key=key).hexdigest()

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_init(args: argparse.Namespace) -> None:
    with open(args.ledger, 'w', encoding='utf-8'):
        pass


def cmd_append(args: argparse.Namespace) -> None:
    events = load_events(args.ledger)
    replica = args.replica
    counter = 0
    for e in events:
        if e['clock'][1] == replica and e['clock'][0] > counter:
            counter = e['clock'][0]
    counter += 1

    if args.parents:
        parents = args.parents.split(',')
    else:
        parents = [events[-1]['event_id']] if events else []

    metrics = json.loads(args.metrics) if args.metrics else {}
    reason_codes = args.reason.split(',') if args.reason else []

    event = {
        'peptide_id': args.peptide,
        'decision': args.decision,
        'reason_codes': reason_codes,
        'metrics': metrics,
        'clock': [counter, replica],
        'parents': parents,
        'author': args.author,
        'ts': time.time(),
    }
    event['event_id'] = compute_event_id(event)
    if args.key:
        with open(args.key, 'rb') as f:
            key = f.read()
        event['sig'] = compute_sig(event, key)

    with open(args.ledger, 'a', encoding='utf-8') as f:
        f.write(json.dumps(event, sort_keys=True) + "\n")


def cmd_list(args: argparse.Namespace) -> None:
    events = load_events(args.ledger)
    for e in events:
        rc = ','.join(e.get('reason_codes', []))
        print(f"{e['event_id'][:8]} {tuple(e['clock'])} {e['author']} {e['peptide_id']} {e['decision']} {rc}")


def cmd_merge(args: argparse.Namespace) -> None:
    by_id: Dict[str, dict] = {}
    for ledger in args.ledgers:
        for e in load_events(ledger):
            by_id[e['event_id']] = e
    events = list(by_id.values())
    events.sort(key=lambda e: (e['clock'][0], e['clock'][1], e['author'], e['event_id']))
    write_events(args.out, events)


def cmd_verify(args: argparse.Namespace) -> None:
    key = None
    if args.key:
        with open(args.key, 'rb') as f:
            key = f.read()
    ok = True
    for e in load_events(args.ledger):
        eid = compute_event_id(e)
        if eid != e.get('event_id'):
            print(f"event {e.get('event_id')}: hash mismatch")
            ok = False
        if key is not None:
            sig = compute_sig(e, key)
            if sig != e.get('sig'):
                print(f"event {e.get('event_id')}: sig mismatch")
                ok = False
    if ok:
        print('OK')
    else:
        sys.exit(1)


def topological_order(events: List[dict]) -> List[dict]:
    by_id = {e['event_id']: e for e in events}
    indegree: Dict[str, int] = {e['event_id']: 0 for e in events}
    children: Dict[str, List[str]] = {e['event_id']: [] for e in events}
    for e in events:
        for p in e.get('parents', []):
            if p in by_id:
                indegree[e['event_id']] += 1
                children[p].append(e['event_id'])
    heap: List[Tuple[int, str, str, str]] = []
    for eid, e in by_id.items():
        if indegree[eid] == 0:
            heapq.heappush(heap, (e['clock'][0], e['clock'][1], e['author'], eid))
    order: List[dict] = []
    while heap:
        _, _, _, eid = heapq.heappop(heap)
        e = by_id[eid]
        order.append(e)
        for child in children[eid]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ce = by_id[child]
                heapq.heappush(heap, (ce['clock'][0], ce['clock'][1], ce['author'], ce['event_id']))
    return order


def cmd_replay(args: argparse.Namespace) -> None:
    events = topological_order(load_events(args.ledger))
    with open(args.rules, 'r', encoding='utf-8') as f:
        rules = json.load(f)
    ops = {
        '<': operator.lt,
        '<=': operator.le,
        '>': operator.gt,
        '>=': operator.ge,
        '==': operator.eq,
    }
    final: Dict[str, Tuple[str, str]] = {}
    for e in events:
        valid = True
        for rc in e.get('reason_codes', []):
            rule = rules.get(rc)
            if not rule:
                valid = False
                break
            for cond in rule.get('all_of', []):
                metric = cond['metric']
                op = ops[cond['op']]
                value = cond['value']
                if metric not in e['metrics'] or not op(e['metrics'][metric], value):
                    valid = False
                    break
            if not valid:
                break
        if valid:
            final[e['peptide_id']] = (e['decision'], e['event_id'])
    for pep in sorted(final):
        decision, eid = final[pep]
        print(f"{pep}: {decision} ({eid[:8]})")


def cmd_graph(args: argparse.Namespace) -> None:
    events = load_events(args.ledger)
    by_id = {e['event_id']: e for e in events}
    print('digraph {')
    for e in events:
        label = f"{e['event_id'][:8]}\\n{e['peptide_id']}\\n{e['decision']}"
        eid = e['event_id']
        print(f'"{eid}" [label="{label}"]')
        for p in e.get('parents', []):
            if p in by_id:
                print(f'"{p}" -> "{eid}"')
    print('}')

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="CRDT audit ledger CLI")
    sub = p.add_subparsers(dest='cmd', required=True)

    sp = sub.add_parser('init', help='create empty ledger')
    sp.add_argument('ledger')
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser('append', help='append event')
    sp.add_argument('ledger')
    sp.add_argument('--peptide', required=True)
    sp.add_argument('--decision', choices=['accept', 'reject'], required=True)
    sp.add_argument('--reason', required=True, help='comma separated reason codes')
    sp.add_argument('--metrics', default='{}')
    sp.add_argument('--replica', required=True)
    sp.add_argument('--author', required=True)
    sp.add_argument('--parents')
    sp.add_argument('--key')
    sp.set_defaults(func=cmd_append)

    sp = sub.add_parser('list', help='list events')
    sp.add_argument('ledger')
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser('merge', help='merge ledgers')
    sp.add_argument('out')
    sp.add_argument('ledgers', nargs='+')
    sp.set_defaults(func=cmd_merge)

    sp = sub.add_parser('verify', help='verify hashes and signatures')
    sp.add_argument('ledger')
    sp.add_argument('--key')
    sp.set_defaults(func=cmd_verify)

    sp = sub.add_parser('replay', help='replay ledger applying rules')
    sp.add_argument('ledger')
    sp.add_argument('--rules', required=True)
    sp.set_defaults(func=cmd_replay)

    sp = sub.add_parser('graph', help='emit Graphviz DOT')
    sp.add_argument('ledger')
    sp.set_defaults(func=cmd_graph)

    return p


def main(argv=None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == '__main__':
    main()
