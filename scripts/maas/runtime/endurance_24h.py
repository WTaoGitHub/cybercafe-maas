"""Drive the three OCR pools through the consumer entry, using control-plane samples."""

import argparse
import base64
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path
import re
from runpy import run_path
import signal
import threading
import time

import httpx


PP = 'ocr.default'
OVIS = 'ocr.ovisocr3'
DOCUMENT = 'document.unlimited-ocr-2'
MODELS = (PP, OVIS, DOCUMENT)
PP_WORDS = ('quarterly', 'service', 'report', 'qsr', '2026', '0410', '128', '12', 'minutes', 'complete')
DENSE_PP_WORDS = ('synthetic', 'invoice', 'reconciliation', 'inventory', 'reconciliation', 'warehouse')
CAPS = (5, 2, 1)
CADENCE = 300
DAY = 86400


class Interrupted(Exception):
    pass


def require(condition, code):
    if not condition:
        raise Interrupted(code)


def percentile(values, fraction):
    return sorted(values)[math.ceil(len(values) * fraction) - 1] if values else None


def lanes(level, elapsed, extra_fraction):
    if level == 40:
        return (2, 1, 1 if elapsed % 30 < 6 else 0)
    if level == 60:
        return (3, 1, 1)
    if level == 75:
        return (4, 1, 1)
    cycle = elapsed % 30 / 30
    if extra_fraction > 1:
        return (5 if cycle < extra_fraction - 1 else 4, 2, 1)
    remainder = (1 - extra_fraction) / 3
    return (4, 1, 1) if cycle < 2 * remainder else (4, 2, 0 if cycle < 3 * remainder else 1)


class Meter:
    def __init__(self):
        self.previous = None
        self.available = self.occupied = self.own = 0.0

    def push(self, sample):
        if self.previous is not None:
            previous = self.previous
            delta = sample['mono'] - previous['mono']
            require(0 < delta <= 10, 'SAMPLE_GAP')
            self.available += previous['capacity'] * delta
            self.occupied += previous['used'] * delta
            self.own += previous['own'] * delta
        self.previous = sample

    def result(self):
        require(self.available > 0, 'NO_CAPACITY_EVIDENCE')
        return {'occupancy': self.occupied / self.available, 'own': self.own / self.available}


class Consumer:
    def __init__(self, workload, evidence):
        self.project = workload['project']
        self.dir = evidence
        self.lock = threading.Lock()
        self.sequence = 0
        self.verified = Counter()
        self.events = deque()
        root = Path(workload['fixtures'])
        self.verify = run_path(str(root / 'verify_ocr_result.py'))
        self.png = (root / 'service-report.png').read_bytes()
        self.pdf = (root / 'ocr-service-verification.pdf').read_bytes()
        key_path = Path(workload['key_file'])
        require(key_path.stat().st_mode & 0o077 == 0, 'KEY_PERMISSIONS')
        key = key_path.read_text().strip()
        require(bool(key) and '\n' not in key, 'KEY_FORMAT')
        self.sync_fixture = (self.sync_body(self.png), PP_WORDS)
        self.pressure_fixture = self.sync_fixture
        if workload.get('pp_dense'):
            generator = run_path(str(root / 'make_peak_capacity_fixture.py'))
            image = generator['ledger'](3200, 4400, 62, 'Synthetic invoice reconciliation')
            data = BytesIO()
            image.save(data, format='PNG')
            raw = data.getvalue()
            self.pressure_fixture = (self.sync_body(raw), DENSE_PP_WORDS)
            self.record('fixture', model=PP, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        self.artifacts = {}
        self.uploaded = 0.0
        self.client = httpx.Client(base_url='http://maas-service:18831', trust_env=False, follow_redirects=False,
                                   headers={'Authorization': 'Bearer ' + key}, timeout=httpx.Timeout(45, connect=3))

    @staticmethod
    def sync_body(image):
        encoded = 'data:image/png;base64,' + base64.b64encode(image).decode()
        return {'model': PP, 'stream': False, 'messages': [{'role': 'user', 'content': [
            {'type': 'image_url', 'image_url': {'url': encoded}}
        ]}]}

    def call(self, method, path, status=200, **options):
        response = self.client.request(method, path, **options)
        require(response.status_code == status, 'HTTP_' + str(response.status_code))
        return response

    def record(self, kind, **fields):
        with self.lock, (self.dir / 'requests.jsonl').open('a') as out:
            out.write(json.dumps({'kind': kind, **fields}, separators=(',', ':')) + '\n')

    def refresh(self):
        for name, raw, media in ((OVIS, self.png, 'image/png'), (DOCUMENT, self.pdf, 'application/pdf')):
            res = self.call('POST', '/maas/v1/artifacts', 201, content=raw, headers={'Content-Type': media}).json()
            self.artifacts[name] = res['id']
            self.record('input', model=name, artifact=res['id'])
        self.artifacts[PP] = self.artifacts[OVIS]
        self.uploaded = time.monotonic()

    def request(self, model, synchronous, pressure=True):
        with self.lock:
            self.sequence += 1
            request_id = f'{self.project}-{self.sequence}'
        begin = time.monotonic()
        self.record('intent', request_id=request_id, model=model, synchronous=synchronous)
        headers = {'X-Request-ID': request_id}
        if synchronous:
            body, words = self.pressure_fixture if pressure else self.sync_fixture
            response = self.call('POST', '/gpustack/v1/chat/completions', json=body, headers=headers)
            finished = time.monotonic()
            usage = response.headers.get('X-Usage-ID')
            require(bool(usage), 'SYNC_USAGE_MISSING')
            return model, request_id, begin, finished, usage, response, words
        headers['Idempotency-Key'] = request_id
        attachment = 'document' if model == DOCUMENT else 'image'
        body = {'model': model, 'input': {attachment: {'artifact_id': self.artifacts[model]}},
                'timeout_seconds': 900 if model == DOCUMENT else 120, 'max_attempts': 1}
        reply = self.call('POST', '/maas/v1/tasks', 202, headers=headers, json=body).json()
        task = reply['id']
        self.record('accepted', request_id=request_id, task=task, model=model)
        endpoint = '/maas/v1/tasks/' + task
        while True:
            row = self.call('GET', endpoint).json()
            if row['status'] == 'SUCCEEDED':
                finished = time.monotonic()
                break
            require(row['status'] not in ('FAILED', 'CANCELLED'), 'TASK_' + row['status'])
            require(time.monotonic() - begin < body['timeout_seconds'] + 30, 'TASK_DEADLINE')
            time.sleep(.12 if model != DOCUMENT else .5)
        require(row['model'] == model and row['attempts'] == 1, 'TASK_IDENTITY_OR_ATTEMPTS')
        return model, request_id, begin, finished, None, task, row['result_artifact_id']

    def verify_result(self, response_data):
        model, request_id, begin, finished, usage, payload, reference = response_data
        if usage:
            receipt = self.call('GET', '/maas/v1/usage/' + usage).json()
            content = json.loads(payload.json()['choices'][0]['message']['content'])
            text = ' '.join(line['text'] for line in content['lines'])
            require(receipt['id'] == usage and receipt['successful_images'] == 1 and
                    self.verify['words_in_order'](text, reference), 'SYNC_TEXT_OR_USAGE')
            self.record('verified', request_id=request_id, usage=usage, model=model, synchronous=True)
            size = 0
        else:
            task = payload
            endpoint = '/maas/v1/tasks/' + task
            result = self.call('GET', '/maas/v1/artifacts/' + reference)
            receipt = self.call('GET', endpoint + '/usage').json()
            self.verify['verify'](model, result.json(), receipt)
            self.record('verified', request_id=request_id, usage=receipt['id'], model=model, task=task,
                        sha256=hashlib.sha256(result.content).hexdigest())
            size = len(result.content)
        elapsed = finished - begin
        with self.lock:
            self.verified['sync' if usage else 'async'] += 1
            self.events.append((finished, model, elapsed, size))

    def metrics(self, after):
        with self.lock:
            while self.events and self.events[0][0] < after:
                self.events.popleft()
            rows = [item for item in self.events if item[0] >= after]
        return {'count': {m: sum(row[1] == m for row in rows) for m in MODELS},
                'p95': {m: percentile([row[2] for row in rows if row[1] == m], .95) for m in MODELS},
                'bytes': sum(row[3] for row in rows)}


def write_status(directory, **status):
    draft = directory / 'status.new'
    draft.write_text(json.dumps(status) + '\n')
    draft.replace(directory / 'status.json')
    print(json.dumps(status), flush=True)


def run(config, output, samples):
    require(re.fullmatch(r'test_agent_endurance_[a-z0-9]{8,24}', config['project']), 'PROJECT_SCOPE')
    # O_EXCL protects the audit namespace against restart or concurrent runners.
    (output / 'run.lock').touch(exist_ok=False)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    client = Consumer(config, output)
    baseline = json.loads(samples.read_text())
    latest = baseline
    meter = Meter()
    budget = baseline['budget']
    require(baseline['capacity'] == 8 and budget == [5, 2, 1, 6], 'BASELINE_CAPACITY')
    extra = .4
    pending = {}
    checking = set()
    ready = threading.Event()
    cpu_history = deque()
    retest = bool(config.get('pp_dense'))

    def poll(stage, level, length, pool, verifier):
        nonlocal latest, extra
        started = time.monotonic()
        window = Meter()
        recent = deque()
        adjusted = started
        sample = latest
        next_read = started
        while time.monotonic() - started < length:
            require(not stop.is_set(), 'STOPPED')
            ready.clear()
            for future in tuple(checking):
                if future.done():
                    checking.remove(future)
                    future.result()
            for future in tuple(pending):
                if future.done():
                    pending.pop(future)
                    check = verifier.submit(client.verify_result, future.result())
                    checking.add(check)
                    check.add_done_callback(lambda _: ready.set())
            now = time.monotonic()
            if now >= next_read:
                sample = json.loads(samples.read_text())
                now = time.monotonic()
                next_read = now + .2
            require(0 <= now - sample['mono'] < 10 and sample['fresh'], 'CAPACITY_STALE')
            require(sample['budget'] == budget and sample['maintenance'] == baseline['maintenance'], 'CAPACITY_OR_MAINTENANCE_CHANGED')
            require(sample['unknown'] == baseline['unknown'], 'UNKNOWN_CHANGED')
            require(sample['oldest_queue'] < 10, 'QUEUE_AGE')
            require(sample['memory_bytes'] >= 2 * 1024**3 and sample['disk_fraction'] >= .2, 'CONTROL_RESOURCES')
            require(sample['project_bytes'] < .9 * sample['project_quota'] and sample['bytes'] < .9 * sample['quota'], 'STORAGE_QUOTA')
            if sample['mono'] > latest['mono']:
                window.push(sample)
                if stage == 'FORMAL':
                    meter.push(sample)
                latest = sample
                recent.append(sample)
                cpu_history.append(sample)
                while recent and sample['mono'] - recent[0]['mono'] > 300:
                    recent.popleft()
                while cpu_history and sample['mono'] - cpu_history[0]['mono'] > 300:
                    cpu_history.popleft()
                if cpu_history and sample['mono'] - cpu_history[0]['mono'] > 290:
                    require(sum(s['cpu'] for s in cpu_history) / len(cpu_history) <= .8, 'CONTROL_CPU')
            if level == 80 and now - adjusted >= 60 and len(recent) > 30:
                share = sum(s['used'] / s['capacity'] for s in recent) / len(recent)
                if abs(share - .8) > .01:
                    extra = min(2, max(.05, extra + (.12 if share < .8 else -.12)))
                adjusted = now
            if now - client.uploaded >= 12 * 3600:
                client.refresh()
            own = Counter(pending.values())
            limits = lanes(level, now - started, extra)
            if level and len(checking) < 32:
                for model, limit in zip(MODELS, limits):
                    background = sample['by_pool'][model] - sample['own_by_pool'][model]
                    while own[model] < max(0, limit - background) and sum(own.values()) < 8 - (sample['used'] - sample['own']):
                        request = pool.submit(client.request, model, model == PP)
                        pending[request] = model
                        request.add_done_callback(lambda _: ready.set())
                        own[model] += 1
            ready.wait(.05)
        return {**window.result(), **client.metrics(started), 'extra_fraction': extra, 'time': time.time()}

    try:
        require(baseline['fresh'] and not baseline['paused'] and baseline['used'] == 0, 'BASELINE_BUSY_OR_PAUSED')
        for model in MODELS:
            require(client.call('GET', '/maas/v1/models/' + model + '/capabilities').json()['model'] == model,
                    'CAPABILITY_MODEL')
        client.refresh()
        for model, sync in ((PP, True), (PP, False), (OVIS, False), (DOCUMENT, False)):
            client.verify_result(client.request(model, sync, pressure=False))
        if config.get('pp_dense'):
            client.verify_result(client.request(PP, True))
        write_status(output, phase='SMOKE_PASSED', verified=dict(client.verified))
        with ThreadPoolExecutor(max_workers=sum(CAPS)) as pool, ThreadPoolExecutor(max_workers=4) as verifier:
            write_status(output, phase='CALIBRATION', target=0, seconds=120 if retest else 600)
            poll('CALIBRATION', 0, 120 if retest else 600, pool, verifier)
            for level in (40, 60, 75):
                write_status(output, phase='CALIBRATION', target=level)
                result = poll('CALIBRATION', level, 120 if retest else CADENCE, pool, verifier)
                write_status(output, phase='CALIBRATION_POINT', target=level, result=result)
            require(result['occupancy'] > 0, 'MISSING_CALIBRATION_LOAD')
            extra = min(2, max(.05, .8 * 6 / result['occupancy'] - 6))
            consecutive = []
            for _ in range(6):
                result = poll('CALIBRATION', 80, CADENCE, pool, verifier)
                write_status(output, phase='CALIBRATION_POINT', target=80, result=result)
                if .78 <= result['occupancy'] <= .82:
                    consecutive.append(result)
                    if len(consecutive) == 2:
                        break
                else:
                    consecutive.clear()
            require(len(consecutive) == 2 and all(sum(r['count'][m] for r in consecutive) >= 20 for m in MODELS),
                    'CALIBRATION_80_NOT_ACHIEVED')
            require_storage(latest, sum(r['bytes'] for r in consecutive) / (2 * CADENCE), DAY)
            t0 = time.time()
            start = time.monotonic()
            write_status(output, phase='FORMAL', t0=t0, end_at=t0 + DAY, calibration=consecutive)
            for hour in range(24):
                duration = start + (hour + 1) * 3600 - time.monotonic()
                result = poll('FORMAL', 80, duration, pool, verifier)
                require(.75 <= result['occupancy'] <= .85, 'FORMAL_HOUR_OCCUPANCY')
                require(all(result['p95'][m] is not None and result['p95'][m] <=
                            1.25 * max(r['p95'][m] or 0 for r in consecutive) for m in MODELS), 'FORMAL_HOUR_LATENCY')
                require_storage(latest, result['bytes'] / 3600, (23 - hour) * 3600)
                write_status(output, phase='FORMAL', t0=t0, end_at=t0 + DAY, hours_complete=hour + 1, result=result)
            for future in pending:
                checking.add(verifier.submit(client.verify_result, future.result()))
            for future in checking:
                future.result()
        stats = meter.result()
        require(.78 <= stats['occupancy'] <= .82 and stats['own'] >= .75, 'FORMAL_FINAL_OCCUPANCY')
        write_status(output, phase='LOAD_COMPLETE', t0=t0, end_at=t0 + DAY, result=stats, verified=dict(client.verified))
    except Exception as exc:
        write_status(output, phase='INTERRUPTED', reason=str(exc) if isinstance(exc, Interrupted) else type(exc).__name__,
                     verified=dict(client.verified))
        raise SystemExit(1) from None
    finally:
        client.client.close()


def require_storage(snapshot, rate, seconds):
    reserved = 1.25 * (rate * seconds + 128 * 1024)
    require(snapshot['project_bytes'] + reserved < .9 * snapshot['project_quota'] and
            snapshot['bytes'] + reserved < .9 * snapshot['quota'] and reserved < snapshot['disk_reserve_bytes'],
            'STORAGE_FORECAST')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('snapshot', type=Path)
    args = parser.parse_args()
    run(json.loads(args.config.read_text()), args.output, args.snapshot)
