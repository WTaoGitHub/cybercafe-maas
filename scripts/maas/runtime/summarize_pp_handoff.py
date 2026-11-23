"""Partition PP-OCR capacity time using privately collected experiment traces."""
import argparse
import collections
import datetime
import hashlib
import json
import math
from pathlib import Path


def summarize(ledger, events, gateway):
    t0, t1 = ledger['period']
    if t1 <= t0:
        raise ValueError('A positive observation window is required')
    traces = collections.defaultdict(dict)
    for row in events:
        traces[row['kind'], row['token']].update(row)
    forwarded = {row['tag'].split('-')[-1]: row for row in gateway}
    spans = collections.defaultdict(list)
    observations = collections.defaultdict(list)
    total = collections.Counter()
    matched = collections.Counter()
    for row in ledger['attempts']:
        if row['probe'] or not row.get('completed'):
            continue
        if row['authorized'] >= t1 or row['completed'] <= t0:
            continue
        kind = row['kind']
        total[kind] += 1
        trace = traces[kind, row['token']]
        tag = hashlib.sha256(f"{kind}.{row['token']}".encode()).hexdigest()[:24]
        access = forwarded.get(tag, {})
        start = row['started'] if kind == 'task' else trace.get('started')
        if (str(access.get('attempts')) != '1' or str(access.get('code')) != '200'
                or access.get('upstream_tx_ms', '-') == '-'
                or not (start and trace.get('granted') and row['gpu_finished'])):
            continue
        matched[kind] += 1
        ingress = datetime.datetime.fromisoformat(access['ingress'].replace('Z', '+00:00')).timestamp()
        sent = ingress + float(access['upstream_tx_ms']) / 1000
        authorized = row['authorized']
        grant = trace['granted']
        finish = row['gpu_finished']
        # Align millisecond gateway timestamps with the finer-grained task ledger.
        points = [
            start, max(start, authorized), max(start, authorized, min(sent, grant)),
            max(start, authorized, grant), max(start, authorized, grant, finish),
            max(start, authorized, grant, finish, row['completed']),
        ]
        names = ['preparation', 'gateway_forwarding', 'waiting_gpu_grant', 'gpu_held', 'result_finalization']
        for (name, p, q) in zip(names, points, points[1:]):
            observations[kind, name].append(1000 * (q - p))
            p = max(p, t0)
            q = min(q, t1)
            if p < q:
                spans[kind].extend(((p, name, 1), (q, name, -1)))
        observations[kind, 'maas_to_gateway'].append(max(0, (ingress - authorized) * 1000))
        observations[kind, 'gateway_request_tx'].append(float(access['upstream_tx_ms']))
        if kind == 'task':
            observations[kind, 'submission_to_claim'].append((start - row['queued']) * 1000)
            if trace.get('context_ready') and trace.get('encoded'):
                observations[kind, 'claim_to_adapter'].append((trace['context_ready'] - start) * 1000)
                observations[kind, 'adapter_input_encoding'].append((trace['encoded'] - trace['context_ready']) * 1000)
        elif trace.get('handler'):
            observations[kind, 'sync_client_to_handler'].append((trace['handler'] - start) * 1000)
            observations[kind, 'sync_handler_to_authorized'].append((authorized - trace['handler']) * 1000)

    def percentiles(numbers):
        values = sorted(numbers)
        return {
            'median_ms': round(values[(len(values) - 1) // 2], 2),
            'p95_ms': round(values[math.ceil(0.95 * len(values)) - 1], 2),
        }

    output = {}
    for kind, slots in (('task', 3), ('sync', 2)):
        timeline = sorted(spans[kind] + [(t0, 'boundary', 0), (t1, 'boundary', 0)])
        contributing = collections.Counter()
        active = collections.Counter()
        last = t0
        for when, stage, delta in timeline:
            room = slots
            # A finished request may still finalize while its slot is reused.
            for name in ('gpu_held', 'preparation', 'gateway_forwarding', 'waiting_gpu_grant', 'result_finalization'):
                n = min(room, active[name])
                contributing[name] += (when - last) * n
                room -= n
            contributing['unassigned'] += (when - last) * room
            active[stage] += delta
            last = when
        output[kind] = {
            'matched': matched[kind], 'attempts': total[kind],
            'slot_seconds': {stage: round(seconds, 3) for stage, seconds in contributing.items()},
            'request_times': {
                stage: percentiles(numbers)
                for (side, stage), numbers in observations.items() if side == kind
            },
        }
    return {
        'period_seconds': round(t1 - t0, 1), 'by_kind': output,
        'coverage_ok': all(total[kind] and matched[kind] >= 0.985 * total[kind] for kind in ('sync', 'task')),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger', type=Path, required=True, help='private MaaS dispatch ledger export')
    parser.add_argument('--events', type=Path, required=True, help='private GPU grant and preparation JSONL')
    parser.add_argument('--gateway', type=Path, required=True, help='private Envoy request timing JSONL')
    args = parser.parse_args()
    result = summarize(
        json.loads(args.ledger.read_text()),
        map(json.loads, args.events.read_text().splitlines()),
        map(json.loads, args.gateway.read_text().splitlines()),
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result['coverage_ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
