"""Reconcile authenticated engine-end receipts on a private per-GPU persistent volume."""
import argparse
from collections import Counter
import json
import logging
import os
from pathlib import Path
import re
import signal
import threading

import psycopg

from .config import private_text
from .db import Database
from .finish_receipt import ATTEMPT, read_finish
from .pp_ocr_execution import GPUEvent, TABLE, record_finish

LOG = logging.getLogger('maas.receipts')
GPU = re.compile(r'GPU-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')


def read_receipt(directory, name, gpu, key):
    payload, _ = read_finish(directory, name, gpu, key)
    return GPUEvent.model_validate_json(payload)


def reconcile(db, event, apply):
    kind, token = event.attempt.split('.')
    table = TABLE[kind]
    with db.transaction() as c:
        mode = c.execute('SELECT record_unknown_only FROM maintenance WHERE id=1'
                         + (' FOR UPDATE' if apply else '')).fetchone()
        if mode['record_unknown_only']:
            return 'record_only'
        lock = ' FOR UPDATE' if apply else ''
        row = c.execute(f'SELECT * FROM {table} WHERE token=%s' + lock, (token,)).fetchone()
        if row and row['gpustack_routed'] and row['state'] == 'UNKNOWN':
            return 'record_only'
        if (not row or not row['execution_guard'] or not (row['guard_scope'] or row['gpustack_routed']) or row['guard_conflict'] or
                (row['dispatch_route'], row['gpu_uuid'], row['gpu_instance']) !=
                (event.route, event.gpu_uuid, event.instance)):
            return 'mismatch'
        if row['gpu_finished_at'] is not None or row['state'] == 'RESOLVED':
            return 'settled'
        if row['state'] != 'UNKNOWN':
            return 'waiting'
        if not apply:
            return 'verified'
        record_finish(c, table, row, 'Recovered authenticated GPU finish receipt')
    return 'recovered'


def scan(db, root, keys, routes, apply):
    for gpu, key in keys.items():
        if not GPU.fullmatch(gpu) or not isinstance(key, str) or not re.fullmatch('[0-9a-f]{64}', key):
            raise ValueError('Invalid GPU key configuration')
        try:
            directory = os.open(root / gpu, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except FileNotFoundError:
            continue  # GPU has not been enrolled for durable receipts.
        except OSError:
            yield 'unreadable'
            continue
        try:
            with os.scandir(directory) as files:
                for entry in files:
                    if not ATTEMPT.fullmatch(entry.name):
                        continue
                    try:
                        event = read_receipt(directory, entry.name, gpu, key)
                        outcome = reconcile(db, event, apply) if event.route in routes else 'other_route'
                        if apply and outcome in ('recovered', 'settled'):
                            # The transaction has committed. An interrupted unlink is safe to retry.
                            os.unlink(entry.name, dir_fd=directory)
                    except FileNotFoundError:
                        continue  # The callback has already acknowledged this file.
                    except (ValueError, OSError):
                        outcome = 'invalid_or_unreadable'
                    yield outcome
        finally:
            os.close(directory)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', required=True, type=Path)
    p.add_argument('--route', action='append', required=True)
    p.add_argument('--mode', choices=('observe','apply'), default='observe')
    p.add_argument('--once', action='store_true')
    args = p.parse_args()
    if not args.root.is_dir() or args.root.is_symlink():
        p.error('Provide the mounted receipt directory')
    db = Database(os.environ['MAAS_DATABASE_URL'])
    keys_file = os.environ['MAAS_PP_OCR_WORKER_KEYS_FILE']
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    logging.basicConfig(level=logging.INFO)
    delay = 1
    while not stop.is_set():
        outcomes = Counter()
        try:
            keys = json.loads(private_text(keys_file))
            if not isinstance(keys, dict):
                raise ValueError('Invalid key file')
            for n, outcome in enumerate(scan(db, args.root, keys, args.route, args.mode == 'apply'), 1):
                outcomes[outcome] += 1
                if stop.is_set() or (n % 32 == 0 and stop.wait(1)):
                    break
        except (OSError, ValueError, psycopg.Error):
            outcomes['unavailable'] += 1
        if outcomes or args.once:
            LOG.info('receipt_scan mode=%s counts=%s', args.mode, dict(outcomes))
        if args.once:
            return int(bool(outcomes['unavailable']))
        delay = 1 if outcomes['recovered'] else min(15, delay * 2)
        stop.wait(delay)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
