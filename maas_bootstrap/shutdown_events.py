"""One best-effort shutdown POST. Never stops workers or changes VM state."""
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from types import SimpleNamespace
import urllib.parse
import urllib.request
from uuid import uuid4

from .client import BootstrapError, canonical, https_opener, read_file
from .shutdown_auth import validate_scope
from .worker import guard_clone_host

PATH = '/maas/internal/worker-shutdown-events'
BOOT_ID = Path('/proc/sys/kernel/random/boot_id')


def configuration(value):
    if value is None:
        return None
    try:
        if not isinstance(value, dict) or set(value) != {'url','tls_certificate_file'}:
            raise ValueError()
        url, cert = value['url'], value['tls_certificate_file']
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.path != PATH or '?' in url or '#' in url
                or (parsed.port is not None and not 1 <= parsed.port <= 65535)):
            raise ValueError()
        path = Path(cert)
        if not path.is_absolute() or '..' in path.parts or str(path) != cert:
            raise ValueError()
        return dict(value)
    except (ValueError, TypeError, AttributeError):
        raise BootstrapError('SHUTDOWN_EVENT_CONFIG_INVALID') from None


def credential_scope(token):
    """Catch delivery mixups locally; only MaaS can verify the issuer's MAC."""
    try:
        if not isinstance(token, str) or len(token) > 1024:
            raise ValueError()
        version, payload, signature = token.split('.')
        if version != 'mse1' or not signature:
            raise ValueError()
        raw = base64.b64decode(payload + '='*(-len(payload)%4), altchars=b'-_', validate=True)
        return validate_scope(json.loads(raw))
    except (ValueError, TypeError):
        raise BootstrapError('SHUTDOWN_EVENT_CREDENTIAL_INVALID') from None


def binding(identity, boot_id):
    return validate_scope({**{k:identity[k] for k in ('cluster_id','worker_id','gpu_uuid','session_id')},
                           'boot_id':boot_id})


def remaining(deadline):
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise TimeoutError()
    return seconds


def is_shutdown(deadline):
    try:
        result = subprocess.run(['/usr/bin/systemctl','list-jobs','--plain','--no-legend','--no-pager'],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            timeout=min(.1,remaining(deadline)), env={**os.environ,'LC_ALL':'C'})
        if result.returncode:
            return False
        for line in result.stdout.splitlines():
            parts = line.split()
            if (len(parts) == 4 and parts[0].isdigit() and parts[1] in ('poweroff.target','halt.target')
                    and parts[2] == 'start' and parts[3] in ('waiting','running')):
                return True
        return False
    except (OSError, subprocess.SubprocessError):
        return False


def notify(config_path='/etc/maas/bootstrap.json', *, deadline=None):
    deadline = time.monotonic()+1 if deadline is None else deadline
    guard_clone_host()
    cfg = json.loads(read_file(config_path, secret=True))
    events = configuration(cfg.get('shutdown_events'))
    if events is None:
        return {'code':'SHUTDOWN_EVENT_DISABLED'}
    root = Path(cfg['state_dir'])
    if (not root.is_absolute() or '..' in root.parts or root.is_symlink()
            or root.stat().st_mode & 0o077 or root.stat().st_uid not in (0, os.geteuid())):
        raise BootstrapError('SHUTDOWN_EVENT_STATE_UNTRUSTED')
    prepared = json.loads(read_file(root/'shutdown-event.json', secret=True))
    current = binding(json.loads(read_file(root/'identity.json', secret=True)), read_file(BOOT_ID).decode().strip())
    if (prepared['scope'] != current or credential_scope(prepared['credential']) != current
            or {k:prepared[k] for k in events} != events):
        raise BootstrapError('SHUTDOWN_EVENT_BINDING_CHANGED')
    if not is_shutdown(deadline):
        return {'code':'SHUTDOWN_EVENT_SKIPPED'}
    event = dict(current, event_id=str(uuid4()), event_type='worker.shutdown.requested',
                 occurred_at=datetime.now(timezone.utc).isoformat())
    opener = https_opener(SimpleNamespace(data=events))
    request = urllib.request.Request(events['url'], data=canonical(event), method='POST',
        headers={'Content-Type':'application/json','Authorization':'Bearer '+prepared['credential']})
    with opener.open(request, timeout=remaining(deadline)) as response:
        if response.status != 201:
            raise BootstrapError('SHUTDOWN_EVENT_UNAVAILABLE')
    return {'code':'SHUTDOWN_EVENT_SENT'}


def main(argv=None):
    import argparse
    deadline = time.monotonic()+1
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='/etc/maas/bootstrap.json')
    args = parser.parse_args(argv)
    def timeout(*_):
        raise TimeoutError()
    prior = signal.signal(signal.SIGALRM, timeout)
    try:
        signal.setitimer(signal.ITIMER_REAL, remaining(deadline))
        result = notify(args.config, deadline=deadline)
    except Exception:
        result = {'code':'SHUTDOWN_EVENT_UNAVAILABLE'}
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, prior)
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
