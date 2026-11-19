"""Run whole-document execution-guard acceptance on two existing GPU endpoints.

The operator supplies a prepared four-page chat body, a scoped task-ledger adapter
(seed, inspect, finish), and a callback that proves a foreign terminal is denied.
Use a disposable route and synthetic data, and clean the probe rows afterwards.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
import http.client
import json
import socket
from threading import Barrier
import time


def verify(endpoints, body, db, finished_by_other):
    """Check a concurrent race and a disconnect with the opposite GPU owning each."""
    assert len(endpoints) == 2 and endpoints[0][2] != endpoints[1][2]

    def connection(index, task):
        host, port, _ = endpoints[index]
        conn = http.client.HTTPConnection(host, port, timeout=180)
        try:
            conn.request('POST', '/v1/chat/completions', body, headers={
                'Content-Type': 'application/json',
                'X-MaaS-Execution-Attempt': 'task.' + task['token'],
            })
        except OSError:
            conn.close()
            raise
        return conn

    def result(conn):
        try:
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def wait(task, condition):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            row = db('inspect', task['token'])
            if condition(row):
                return row
            time.sleep(.15)
        raise RuntimeError('Probe condition not reached')

    def occupied(task, index, state, slots, finished):
        row = db('inspect', task['token'])
        assert (row['gpu_uuid'], row['state'], row['slots'], row['finished']) == (
            endpoints[index][2], state, slots, finished), row
        return row

    normal = db('seed')
    start = Barrier(len(endpoints))

    def race(index):
        start.wait(4)
        return result(connection(index, normal))

    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(race, range(len(endpoints))))
    codes = [code for code, _ in replies]
    assert sorted(codes) == [200, 409], codes
    winner = codes.index(200)
    document = replies[winner][1]
    metadata = document['metadata']
    assert metadata['doc_split_complete'] and metadata['doc_split_batches'] == 2
    choice = document['choices'][0]
    assert choice['finish_reason'] == 'stop' and choice['message']['content'].count('<PAGE>') == 4
    occupied(normal, winner, 'COMPLETED', 1, True)
    db('finish', json.dumps(normal))
    occupied(normal, winner, 'COMPLETED', 0, True)
    print(json.dumps({'race_codes': codes, 'winner': winner, 'pages': 4, 'windows': 2, 'slots': 0}), flush=True)

    lost = db('seed')
    owner = 1 - winner
    conn = connection(owner, lost)
    try:
        started = wait(lost, lambda row: row['gpu_uuid'] is not None)
        assert started['gpu_uuid'] == endpoints[owner][2] and not started['finished']
        with suppress(OSError):
            conn.sock.shutdown(socket.SHUT_RDWR)
    finally:
        conn.close()
    db('finish', json.dumps(lost))
    occupied(lost, owner, 'UNKNOWN', 1, False)
    assert result(connection(winner, lost))[0] == 409
    finished_by_other(lost, winner)
    occupied(lost, owner, 'UNKNOWN', 1, False)
    print(json.dumps({
        'disconnect_owner': owner, 'replay_http': 409, 'foreign_receipt_http': 409, 'state': 'UNKNOWN', 'slots': 1,
    }), flush=True)
    wait(lost, lambda row: row['finished'])
    ended = occupied(lost, owner, 'COMPLETED', 0, True)
    assert ended['task_state'] == 'FAILED' and ended['error_code'] == 'UNKNOWN_BACKEND_ACTIVITY'
    print(json.dumps({'disconnect_state': 'COMPLETED', 'slots': 0, 'task_status': 'FAILED'}), flush=True)
