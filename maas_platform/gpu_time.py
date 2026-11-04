"""Read-only GPUStack sampling and operator daily and monthly reports."""
import argparse
from collections import defaultdict
from datetime import date, datetime, time, timedelta
import json
import os
from uuid import uuid4
import sys
import signal
from threading import Event
from time import monotonic

import psycopg
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb
from .db import Database


BEIJING = ZoneInfo('Asia/Shanghai')
MAX_GAP_SECONDS = 120


def pages(base_url, token):
    deadline = monotonic() + 30
    items = []
    for page in range(1, 1001):
        query = urlencode({'page': page, 'perPage': 100})
        request = Request(f'{base_url.rstrip("/")}/v2/workers?{query}',
                          headers={'Authorization': f'Bearer {token}'})
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError('Worker pagination timed out')
        with urlopen(request, timeout=min(10, remaining)) as response:
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


def snapshot(workers):
    states = defaultdict(list)
    for worker in workers:
        for gpu in (worker.get('status') or {}).get('gpu_devices') or []:
            uuid = gpu.get('uuid')
            if uuid:
                states[uuid].append(worker['state'] == 'ready')
    cards = {}
    for uuid, values in states.items():
        if len(values) == 1:
            cards[uuid] = values[0]
        else:
            cards[uuid] = True if values.count(True) == 1 else None
    return cards


def save(db, at, session_id, cards):
    with db.transaction() as conn:
        conn.execute('INSERT INTO e17.samples VALUES (%s, %s, %s)',
                     (at, session_id, Jsonb(cards) if cards is not None else None))
        if cards:
            with conn.cursor() as cursor:
                cursor.executemany(
                    'INSERT INTO e17.cards VALUES (%s, %s) ON CONFLICT (uuid) DO UPDATE SET '
                    'first_seen=least(e17.cards.first_seen, excluded.first_seen)',
                    [(uuid, at) for uuid in cards],
                )


def summarize(uuids, samples, start, end):
    duration = max(0, end - start)
    cards = {uuid: {'online_seconds': 0, 'not_ready_seconds': 0, 'unknown_seconds': duration}
             for uuid in uuids}
    previous = None
    for at, session_id, observed in samples:
        if previous is not None and observed is not None:
            before, prior_session, prior_cards = previous
            seconds = min(at, end) - max(before, start)
            if prior_cards is not None and session_id == prior_session and 0 < seconds and at - before <= MAX_GAP_SECONDS:
                for uuid, online in prior_cards.items():
                    if uuid not in cards or online is None:
                        continue
                    totals = cards[uuid]
                    key = 'online_seconds' if online else 'not_ready_seconds'
                    totals[key] += seconds
                    totals['unknown_seconds'] -= seconds
        previous = at, session_id, observed
    return cards


def report(db, period, now):
    day = date.fromisoformat(period + '-01' if len(period) == 7 else period)
    if period == day.isoformat():
        stop = day + timedelta(days=1)
    elif period == day.isoformat()[:7]:
        stop = (day.replace(day=28) + timedelta(days=4)).replace(day=1)
    else:
        raise ValueError('Use YYYY-MM-DD or YYYY-MM')
    start = int(datetime.combine(day, time.min, BEIJING).timestamp())
    end = min(now, int(datetime.combine(stop, time.min, BEIJING).timestamp()))
    cards = {}
    if end > start:
        with db.transaction() as conn:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            uuids = [row['uuid'] for row in conn.execute(
                'SELECT uuid FROM e17.cards WHERE first_seen < %s ORDER BY uuid', (end,))]
            with conn.cursor(name='e17_report') as cursor:
                cursor.execute('SELECT at,session_id,cards FROM e17.samples WHERE at BETWEEN %s AND %s ORDER BY at',
                               (start - MAX_GAP_SECONDS, min(now, end + MAX_GAP_SECONDS)))
                cards = summarize(uuids, ((r['at'], r['session_id'], r['cards']) for r in cursor), start, end)
    return {'period': period, 'timezone': str(BEIJING), 'period_seconds': max(0, end - start), 'gpus': cards}


def sample(db, base_url, token, session_id):
    at = int(datetime.now().timestamp())
    try:
        cards = snapshot(pages(base_url, token))
        if int(datetime.now().timestamp()) - at >= MAX_GAP_SECONDS:
            raise ValueError('Sample exceeded the maximum observation window')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        save(db, at, session_id, None)
        print(f'GPUStack sample unavailable ({type(exc).__name__})', file=sys.stderr)
        return 1
    save(db, at, session_id, cards)
    print(json.dumps({'observed_gpus': len(cards)}))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    sampling = commands.add_parser('sample')
    sampling.add_argument('--loop', action='store_true')
    commands.add_parser('report').add_argument('period')
    args = parser.parse_args()
    db = Database(os.environ['MAAS_DATABASE_URL'])
    if args.command == 'report':
        print(json.dumps(report(db, args.period, int(datetime.now().timestamp())), ensure_ascii=False))
        return 0
    base_url = os.environ['MAAS_GPUSTACK_URL']
    with open(os.environ['E17_GPUSTACK_KEY_FILE'], encoding='utf-8') as key_file:
        token = key_file.read().strip()
    if not token:
        parser.error('GPUStack key file is empty')
    session_id = uuid4()
    stopped = Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())
    with psycopg.connect(db.url, autocommit=True, connect_timeout=5) as lock:
        if not lock.execute('SELECT pg_try_advisory_lock(842125217)').fetchone()[0]:
            print('An E17 sampler is already running', file=sys.stderr)
            return 1
        while not stopped.is_set():
            started = monotonic()
            lock.execute('SELECT 1')
            status = sample(db, base_url, token, session_id)
            if not args.loop:
                return status
            stopped.wait(max(0, 60 - (monotonic() - started)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
