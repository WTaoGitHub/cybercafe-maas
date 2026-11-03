"""Read-only GPUStack sampling and operator daily reports."""
import argparse
from collections import defaultdict
from datetime import date, datetime, time, timedelta
import json
import os
import sqlite3
import sys
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


BEIJING = ZoneInfo('Asia/Shanghai')
MAX_GAP_SECONDS = 120


def pages(base_url, token, resource):
    items = []
    for page in range(1, 1001):
        query = urlencode({'page': page, 'perPage': 100})
        request = Request(f'{base_url.rstrip("/")}/v2/{resource}?{query}',
                          headers={'Authorization': f'Bearer {token}'})
        with urlopen(request, timeout=10) as response:
            payload = json.load(response)
        pagination = payload['pagination']
        if pagination['page'] != page or not isinstance(payload['items'], list):
            raise ValueError('Unexpected GPUStack page')
        items.extend(payload['items'])
        if page >= pagination['totalPage']:
            if len(items) != pagination['total'] or len({item['id'] for item in items}) != len(items):
                raise ValueError('GPUStack list changed during pagination')
            return items
    raise ValueError('GPUStack page limit exceeded')


def snapshot(workers, instances):
    devices = {}
    states = defaultdict(list)
    for worker in workers:
        for gpu in (worker.get('status') or {}).get('gpu_devices') or []:
            uuid = gpu.get('uuid')
            if uuid:
                states[uuid].append(worker['state'] == 'ready')
                devices[(worker['id'], gpu.get('type'), gpu['index'])] = uuid
    cards = {uuid: {'online': values[0] if len(values) == 1 else None, 'models': {}}
             for uuid, values in states.items()}
    unresolved = 0
    for instance in instances:
        if instance['state'] != 'running':
            continue
        distributed = instance.get('distributed_servers') or {}
        for binding in [instance, *(distributed.get('subordinate_workers') or [])]:
            if binding is not instance and binding['state'] != 'running':
                continue
            for index in binding.get('gpu_indexes') or []:
                uuid = devices.get((binding.get('worker_id'), binding.get('gpu_type'), index))
                if uuid is None and not binding.get('gpu_type'):
                    matches = {value for (worker_id, _, gpu_index), value in devices.items()
                               if worker_id == binding.get('worker_id') and gpu_index == index}
                    if len(matches) == 1:
                        uuid = matches.pop()
                if uuid is None or cards[uuid]['online'] is None:
                    unresolved += 1
                elif cards[uuid]['online']:
                    cards[uuid]['models'][str(instance['model_id'])] = instance['model_name']
    return {'cards': cards, 'unresolved_bindings': unresolved}


def open_db(path):
    db = sqlite3.connect(path)
    db.execute('CREATE TABLE IF NOT EXISTS samples (at INTEGER PRIMARY KEY, payload TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS cards (uuid TEXT PRIMARY KEY, first_seen INTEGER NOT NULL)')
    return db


def save(db, at, payload):
    with db:
        db.execute('INSERT INTO samples VALUES (?, ?)', (at, json.dumps(payload) if payload is not None else None))
        if payload is not None:
            db.executemany('INSERT INTO cards VALUES (?, ?) ON CONFLICT(uuid) DO UPDATE SET '
                           'first_seen=min(first_seen, excluded.first_seen)',
                           [(uuid, at) for uuid in payload['cards']])


def daily_report(db, day, now):
    start = int(datetime.combine(day, time.min, BEIJING).timestamp())
    midnight = int(datetime.combine(day + timedelta(days=1), time.min, BEIJING).timestamp())
    end = min(now, midnight)
    duration = max(0, end - start)
    cards = {
        uuid: {'online_seconds': 0, 'not_ready_seconds': 0, 'unknown_seconds': duration,
               'model_unknown_seconds': duration, 'models': {}}
        for (uuid,) in db.execute('SELECT uuid FROM cards WHERE first_seen<=? ORDER BY uuid', (end,))
    } if duration else {}
    previous = None
    samples = db.execute('SELECT at,payload FROM samples WHERE at BETWEEN ? AND ? ORDER BY at',
                         (start - MAX_GAP_SECONDS, min(now, end + MAX_GAP_SECONDS)))
    for at, raw in samples:
        if previous is not None and raw is not None:
            before, payload = previous
            seconds = min(at, end) - max(before, start)
            if payload is not None and 0 < seconds and at - before <= MAX_GAP_SECONDS:
                for uuid, observed in payload['cards'].items():
                    if uuid not in cards or observed['online'] is None:
                        continue
                    totals = cards[uuid]
                    key = 'online_seconds' if observed['online'] else 'not_ready_seconds'
                    totals[key] += seconds
                    totals['unknown_seconds'] -= seconds
                    if not payload['unresolved_bindings']:
                        totals['model_unknown_seconds'] -= seconds
                        for model_id, name in observed['models'].items():
                            model = totals['models'].setdefault(model_id, {'name': name, 'seconds': 0})
                            model.update(name=name, seconds=model['seconds'] + seconds)
        previous = at, json.loads(raw) if raw is not None else None
    return {'date': day.isoformat(), 'timezone': str(BEIJING), 'period_seconds': duration, 'gpus': cards}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('sample')
    commands.add_parser('report').add_argument('day', type=date.fromisoformat)
    args = parser.parse_args()
    os.umask(0o077)
    with open_db(args.db) as db:
        if args.command == 'report':
            print(json.dumps(daily_report(db, args.day, int(datetime.now().timestamp())), ensure_ascii=False))
            return 0
        base_url = os.environ['GPUSTACK_URL']
        token = os.environ['GPUSTACK_API_KEY']
        at = int(datetime.now().timestamp())
        try:
            payload = snapshot(pages(base_url, token, 'workers'), pages(base_url, token, 'model-instances'))
            if int(datetime.now().timestamp()) - at >= MAX_GAP_SECONDS:
                raise ValueError('Sample exceeded the maximum observation window')
        except (OSError, ValueError, KeyError, TypeError) as exc:
            save(db, at, None)
            print(f'GPUStack sample unavailable ({type(exc).__name__})', file=sys.stderr)
            return 1
        save(db, at, payload)
        print(json.dumps({'observed_gpus': len(payload['cards']),
                          'unresolved_bindings': payload['unresolved_bindings']}))
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
