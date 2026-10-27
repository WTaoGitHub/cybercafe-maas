"""Selection, bounded HTTP, safe checkpoints and machine-readable test reports."""
from dataclasses import dataclass, field
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import ssl
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET


class CheckFailed(Exception):
    pass


class SkipCase(Exception):
    pass


class TransportUncertain(Exception):
    pass


@dataclass(frozen=True)
class Case:
    case_id: str
    module: str
    title: str
    labels: tuple
    run: object = field(repr=False, compare=False)
    effects: tuple = ()
    requires: tuple = ()

    def public(self):
        return {k: getattr(self, k) for k in
                ('case_id', 'module', 'title', 'labels', 'effects', 'requires')}


CASES = []


def case(case_id, module, title, labels=(), effects=(), requires=()):
    def decorate(fn):
        if any(c.case_id == case_id for c in CASES):
            raise ValueError('Duplicate case ID: ' + case_id)
        CASES.append(Case(case_id, module, title, tuple(labels), fn,
                          tuple(effects), tuple(requires)))
        return fn
    return decorate


def select(cases, ids=(), modules=(), labels=(), exclude_labels=()):
    """OR within a selector, AND across selectors; stable catalog order."""
    for requested, known, kind in [
        (ids, {c.case_id for c in cases}, 'case_id'),
        (modules, {c.module for c in cases}, 'module'),
        (labels, {v for c in cases for v in c.labels}, 'label'),
        (exclude_labels, {v for c in cases for v in c.labels}, 'excluded label'),
    ]:
        unknown = set(requested) - known
        if unknown:
            raise ValueError('Unknown ' + kind + ': ' + ', '.join(sorted(unknown)))
    return [c for c in cases if (not ids or c.case_id in ids)
            and (not modules or c.module in modules)
            and (not labels or set(labels).intersection(c.labels))
            and not set(exclude_labels).intersection(c.labels)]


def check(condition, message):
    if not condition:
        raise CheckFailed(message)


def private_key(path):
    p = Path(path).expanduser()
    check(p.is_file(), 'Key file is missing')
    check(not stat.S_IMODE(p.stat().st_mode) & 0o077, 'Key file permissions must be 600 or stricter')
    key = p.read_text().strip()
    check(bool(key) and len(key) <= 1024 and all(33 <= ord(c) <= 126 for c in key),
          'Invalid key file contents')
    return key


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass
class Response:
    status: int
    headers: dict
    raw: bytes

    def json(self):
        try:
            return json.loads(self.raw)
        except (ValueError, UnicodeError):
            raise CheckFailed('Expected a JSON response') from None

    def expect(self, status, code=None):
        check(self.status == status, f'Expected HTTP {status}, received {self.status}')
        if code:
            check(self.json().get('error', {}).get('code') == code, 'Unexpected error code')
        return self


class Context:
    def __init__(self, config, output, effects=()):
        self.config = config
        self.output = Path(output)
        self.output.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.output.chmod(0o700)
        self.effects = set(effects)
        self.run_id = self.output.name
        self.current = None
        self.blocked = False
        self.resources = []
        self.report = {'run_id': self.run_id, 'started_at': time.time(),
                       'selection': [], 'results': [], 'resources': self.resources,
                       'no_automatic_request_retry': True, 'inference_blocked': False}
        self.keys = {}
        self._validate_config()
        self.save()

    def _validate_config(self):
        base = self.config['base_url'].rstrip('/')
        url = urllib.parse.urlsplit(base)
        check(bool(url.hostname) and not url.username and not url.password
              and not url.path and not url.query and not url.fragment,
              'base_url must be an origin without path, credentials or query')
        check(url.scheme == 'https' or (url.scheme == 'http' and url.hostname in ('127.0.0.1', '::1')),
              'Use HTTPS, or loopback HTTP through an authorized tunnel')
        self.base = base
        check(0 < self.config.get('request_timeout', 45) <= 120, 'Invalid request_timeout')
        check(0 < self.config.get('poll_timeout', 150) <= 3600, 'Invalid poll_timeout')
        check(.1 <= self.config.get('poll_interval', 1) <= 30, 'Invalid poll_interval')
        self.keys['primary'] = private_key(self.config['key_file'])
        for identity, option in [('secondary', 'secondary_key_file'), ('restricted', 'restricted_key_file')]:
            if self.config.get(option):
                self.keys[identity] = private_key(self.config[option])
        context = ssl.create_default_context(cafile=str(Path(self.config['cert_file']).expanduser())) \
            if url.scheme == 'https' else None
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect(), urllib.request.HTTPSHandler(context=context))

    def save(self):
        self.report['inference_blocked'] = self.blocked
        tmp = self.output / '.report.tmp'
        with tmp.open('w') as f:
            os.fchmod(f.fileno(), 0o600)
            json.dump(self.report, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(self.output / 'report.json')

    def track(self, kind, ident):
        self.resources.append({'case_id': self.current['case_id'], 'kind': kind, 'id': ident})
        self.save()
        return ident

    @property
    def model(self):
        return self.config.get('model', 'ocr.default')

    def request(self, method, path, body=None, raw=None, media='application/json',
                identity='primary', headers=None, inference=False):
        check(path.startswith('/') and not path.startswith('//'), 'Invalid request path')
        if method != 'GET':
            effect = 'inference' if inference else 'write'
            check(effect in self.current['effects'], 'Case did not declare request effect: ' + effect)
            check(effect in self.effects, 'Request effect not enabled: ' + effect)
        if inference and self.blocked:
            raise SkipCase('Inference circuit open; inspect uncertain activity before a new run')
        h = {'Accept': 'application/json'}
        if identity == 'invalid':
            h['Authorization'] = 'Bearer e2e-unregistered-' + uuid.uuid4().hex
        elif identity is not None:
            h['Authorization'] = 'Bearer ' + self.keys[identity]
        h.update(headers or {})
        if body is not None:
            raw = json.dumps(body).encode()
        if raw is not None:
            h['Content-Type'] = media
        # Only method/path/status and server request ID are reported, never bodies, keys or OCR text.
        evidence = {'method': method, 'path': path, 'state': 'SENDING'}
        self.current['requests'].append(evidence)
        self.save()  # Durable marker BEFORE any send, including uploads/submissions.
        request = urllib.request.Request(self.base + path, data=raw, headers=h, method=method)
        try:
            try:
                response = self.opener.open(request, timeout=self.config.get('request_timeout', 45))
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                payload = response.read(21 * 1024 * 1024 + 1)
                check(len(payload) <= 21 * 1024 * 1024, 'Response exceeds test client bound')
                result = Response(response.code, {k.lower(): v for k, v in response.headers.items()}, payload)
                evidence.update(state='RECEIVED', status=result.status,
                                request_id=result.headers.get('x-request-id'))
                if 'json' in result.headers.get('content-type', ''):
                    try:
                        code = result.json().get('error', {}).get('code')
                    except (CheckFailed, AttributeError):
                        code = None
                    if code:
                        evidence['error_code'] = code
                    if code in ('UNKNOWN_BACKEND_ACTIVITY', 'BACKEND_QUARANTINED'):
                        self.blocked = True
                if inference and result.status >= 500:
                    self.blocked = True
                return result
        except Exception as exc:
            # Do not log exception text, URLs or credentials. No automatic retry/replay.
            evidence.update(state='UNCONFIRMED', error_type=type(exc).__name__)
            if method != 'GET':
                self.blocked = True
            raise TransportUncertain('Request not fully observed; no replay; inspect report checkpoint') from None
        finally:
            self.save()

    def image(self, name='image_file'):
        from .fixtures import blank_png
        if self.config.get(name):
            raw = Path(self.config[name]).expanduser().read_bytes()
            check(0 < len(raw) <= 10 * 1024 * 1024, 'Fixture size must be 1..10MiB')
            return raw
        return blank_png()

    def ocr_body(self, **values):
        return {'model': self.model, 'image_base64': base64.b64encode(self.image()).decode(), **values}

    def upload(self, raw=None, media='image/png'):
        raw = self.image() if raw is None else raw
        r = self.request('POST', '/maas/v1/artifacts', raw=raw, media=media).expect(201)
        value = r.json()
        self.track('artifact', value['id'])
        check(value['size'] == len(raw) and value['sha256'] == hashlib.sha256(raw).hexdigest(),
              'Upload size/hash mismatch')
        return value['id']

    def task_body(self, artifact):
        return {'model': self.model, 'input': {'image': {'artifact_id': artifact}},
                'parameters': {}, 'timeout_seconds': 120, 'max_attempts': 1}

    def submit(self, body, idem=None):
        idem = idem or 'e2e-' + uuid.uuid4().hex
        self.track('idempotency_key', idem)  # Before submission; do not lose recovery handle.
        r = self.request('POST', '/maas/v1/tasks', body=body,
                         headers={'Idempotency-Key': idem}).expect(202)
        row = r.json()
        self.track('task', row['id'])
        check(r.headers.get('location') == '/maas/v1/tasks/' + row['id'], 'Missing task Location')
        return row, idem

    def wait(self, ident):
        deadline = time.monotonic() + self.config.get('poll_timeout', 150)
        while True:
            row = self.request('GET', '/maas/v1/tasks/' + ident).expect(200).json()
            if row.get('error_code') == 'UNKNOWN_BACKEND_ACTIVITY':
                self.blocked = True
                self.save()
            if row['state'] in ('SUCCEEDED', 'FAILED', 'CANCELLED'):
                return row
            if time.monotonic() >= deadline:
                self.blocked = True
                self.save()
                raise CheckFailed('Task still active after poll budget; not resubmitted or auto-cancelled')
            time.sleep(self.config.get('poll_interval', 1))


def execute(context, cases):
    context.report['selection'] = [c.public() for c in cases]
    context.save()
    interrupted = False
    try:
        for c in cases:
            row = {**c.public(), 'status': 'RUNNING', 'started_at': time.time(), 'requests': []}
            context.report['results'].append(row)
            context.current = row
            context.save()
            try:
                missing = set(c.effects) - context.effects
                if missing:
                    raise SkipCase('Enable effects: ' + ', '.join(sorted(missing)))
                absent = [name for name in c.requires if not context.config.get(name)]
                if absent:
                    raise SkipCase('Missing configuration: ' + ', '.join(absent))
                if context.blocked and set(c.effects).intersection(('inference', 'write')):
                    raise SkipCase('Circuit open; mutations skipped, read-only checks can continue')
                c.run(context)
                row['status'] = 'PASS'
            except SkipCase as exc:
                row.update(status='SKIP', reason=str(exc))
            except (CheckFailed, TransportUncertain) as exc:
                row.update(status='FAIL', reason=str(exc))
            except KeyboardInterrupt:
                context.blocked = bool(c.effects)
                row.update(status='ERROR', reason='Interrupted; inspect last request before another run')
                interrupted = True
                raise
            except Exception as exc:
                # Unexpected handler errors must never leak secret-bearing exception values.
                if c.effects:
                    context.blocked = True
                row.update(status='ERROR', reason='Handler error: ' + type(exc).__name__)
            finally:
                row['duration_s'] = round(time.time() - row['started_at'], 3)
                context.save()
                print(c.case_id, row['status'], row.get('reason', ''), flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        context.report['finished_at'] = time.time()
        context.report['interrupted'] = interrupted
        context.report['summary'] = {s: sum(r['status'] == s for r in context.report['results'])
                                     for s in ('PASS', 'FAIL', 'ERROR', 'SKIP')}
        context.save()
        render_reports(context)
    return context.report


def render_reports(context):
    report = context.report
    counts = report['summary']
    suite = ET.Element('testsuite', name='maas-e2e', tests=str(len(report['results'])),
                       failures=str(counts['FAIL']), errors=str(counts['ERROR']), skipped=str(counts['SKIP']))
    md = ['# MaaS E2E test report', '', 'Run: ' + context.run_id,
          '', json.dumps(counts), '', 'SKIP is not a verified pass. No request is automatically replayed.',
          '', '| Case | Module | Status | Seconds |', '|---|---|---|---|']
    for row in report['results']:
        node = ET.SubElement(suite, 'testcase', name=row['case_id'], classname=row['module'],
                             time=str(row['duration_s']))
        tag = {'FAIL': 'failure', 'ERROR': 'error', 'SKIP': 'skipped'}.get(row['status'])
        if tag:
            ET.SubElement(node, tag, message=row.get('reason', ''))
        md.append('| ' + ' | '.join(str(row[k]) for k in ('case_id', 'module', 'status', 'duration_s')) + ' |')
    for name, content in [('junit.xml', ET.tostring(suite, encoding='unicode')),
                          ('REPORT.md', '\n'.join(md) + '\n')]:
        p = context.output / name
        with p.open('w') as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(content)
