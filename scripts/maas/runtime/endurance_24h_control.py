"""New PC supervisor for the endurance workload and its owned test resources."""

from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener


SERVICES = ('api', 'executor', 'observer')
MODELS = ('ocr.default', 'ocr.ovisocr3', 'document.unlimited-ocr-2')
GATEWAY = 'http://127.0.0.1:18080'


def command(*argv, **options):
    kwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=120)
    kwargs.update(options)
    return subprocess.run(argv, **kwargs).stdout


def private_write(path, data, uid=0, gid=0):
    temp = path.with_suffix(path.suffix + '.new')
    with temp.open('xb') as fp:
        os.fchmod(fp.fileno(), 0o600)
        os.fchown(fp.fileno(), uid, gid)
        fp.write(data)
        fp.flush()
        os.fsync(fp.fileno())
    temp.replace(path)


def json_bytes(value):
    return json.dumps(value).encode() + b'\n'


def native(opener, path, value=None, *, headers=None, method=None):
    data = json_bytes(value) if value is not None else None
    content = {'Content-Type': 'application/json', **(headers or {})}
    with opener.open(Request(GATEWAY + path, data=data, headers=content, method=method), timeout=10) as response:
        body = response.read()
    return json.loads(body) if body else None


def login(username, password):
    opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))
    payload = urlencode({'username': username, 'password': password}).encode()
    with opener.open(Request(GATEWAY + '/auth/login', data=payload), timeout=10):
        pass
    return opener


def supervise(root, control):
    run_id = root.name.removeprefix('tmp_agent_endurance_')
    if root.name != 'tmp_agent_endurance_' + run_id or not re.fullmatch(r'[a-z0-9]{8,24}', run_id):
        raise ValueError('Dedicated run directory required')
    project = 'test_agent_endurance_' + run_id
    api = 'maas-control-api-1'
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    stop_file = root / 'manifest.json'
    stage = root / 'evidence'
    reports = control / 'endurance-reports'
    config_dir = root / 'config'
    stats_dir = root / 'observe'
    code = root / 'code'
    status = {'phase': 'PREPARING'}
    report = {'run_id': run_id}
    user = key_id = process = publisher = admin = password = None
    network = container = False
    bindings_written = False
    cleanup_errors = []
    reference = json.loads(command('docker', 'inspect', api))[0]
    labels = reference['Config']['Labels']
    release = json.loads((control / 'ACTIVE-MAAS-RELEASE.json').read_text())['capacity_admission']
    compose = ['docker', 'compose', '--env-file', release['env_file'], '-p', labels['com.docker.compose.project'],
               '--project-directory', labels['com.docker.compose.project.working_dir']]
    for path in labels['com.docker.compose.project.config_files'].split(','):
        compose.extend(('-f', path))
    effective = json.loads(command(*compose, 'config', '--format', 'json'))['services']
    for service in SERVICES:
        svc = json.loads(command('docker', 'inspect', f'maas-control-{service}-1'))[0]
        if effective[service]['image'] != svc['Config']['Image']:
            raise RuntimeError('Active release and effective image differ')
    mounts = {m['Destination']: Path(m['Source']) for m in reference['Mounts']}
    bindings_path = mounts['/run/maas-three-model-bindings.json']
    original = bindings_path.read_bytes()
    ownership = bindings_path.stat()
    secrets_path = mounts['/run/secrets'] / project
    uid, gid = (int(v) for v in command('docker', 'exec', api, 'python', '-B', '-c',
                                       'import os;print(os.getuid(),os.getgid())').split())

    def reload_bindings():
        command(*compose, 'up', '--no-deps', '--force-recreate', '--no-build', '--pull', 'never',
                '--wait', '--wait-timeout', '90', *SERVICES)
        expected = bindings_path.read_bytes()
        for service in SERVICES:
            actual = command('docker', 'exec', f'maas-control-{service}-1', 'cat', '/run/maas-three-model-bindings.json')
            if actual != expected:
                raise RuntimeError('Binding mount did not converge')

    def app_script(name, *args):
        return json.loads(command('docker', 'exec', '-i', api, 'python', '-B', '-', *args,
                                  input=(code / name).read_bytes()))

    def audit(action):
        return app_script('endurance_24h_audit.py', action, project)

    def idle():
        sample = app_script('endurance_24h_observe.py', project, '--once')
        return sample['fresh'] and not sample['paused'] and sample['budget'] == [5, 2, 1, 6] and not (
            sample['used'] or sample['oldest_queue'])

    def publish():
        for line in process.stdout:
            sample = json.loads(line)
            private_write(stats_dir / 'snapshot.json', json_bytes(sample), uid, gid)

    def cleanup_step(label, callback):
        try:
            callback()
            return True
        except Exception as exc:
            cleanup_errors.append({'step': label, 'error': type(exc).__name__})
            return False

    def cleanup_app():
        report['reconciliation'] = audit('cleanup')
        numbers = report['reconciliation']['before']
        known = status.get('verified', {})
        if status['phase'] == 'LOAD_COMPLETE':
            report['reconciled'] = (
                numbers['tasks'] == numbers['succeeded'] == numbers['attempts'] == numbers['task_receipts']
                == numbers['task_usage'] == known.get('async') and numbers['sync_calls'] == numbers['sync_receipts']
                == numbers['sync_usage'] == known.get('sync'))

    def unbind():
        current = bindings_path.read_bytes()
        data = json.loads(current)
        data['credentials'] = [binding for binding in data['credentials'] if binding['id'] != project]
        restored = original if json.loads(original) == data else json_bytes(data)
        private_write(bindings_path, restored, ownership.st_uid, ownership.st_gid)
        reload_bindings()

    def cleanup_native_usage():
        # GPUStack batches usage writes at intervals of up to ten seconds.
        time.sleep(15)
        report['native_usage_removed'] = json.loads(command(
            'docker', 'exec', '-i', 'maas-control-gpustack-observe', 'python', '-B', '-', str(user), project,
            input=(code / 'endurance_24h_gpu_usage.py').read_bytes()))

    try:
        previous = audit('read')
        if any(previous.values()) or not idle():
            raise RuntimeError('Existing test data or control plane is not ready and idle')
        binding_data = json.loads(original)
        if any(row['id'] == project or row['project'] == project for row in binding_data['credentials']):
            raise RuntimeError('Test project already registered')
        for directory in (config_dir, stats_dir, stage):
            directory.mkdir(mode=0o700)
            os.chown(directory, uid, gid)
        reports.mkdir(mode=0o700, exist_ok=True)
        admin = login('admin', (control / 'operator/gpustack-admin.password').read_text().strip())
        catalog_script = ('import os,json;d=json.load(open(os.environ["MAAS_CATALOG_FILE"]));'
                          'print(json.dumps({k:v["upstream_model"] for k,v in d.items()}))')
        catalog = json.loads(command('docker', 'exec', api, 'python', '-B', '-c', catalog_script))
        allowed = [catalog[model] for model in MODELS]
        password = 'Ab9!' + secrets.token_hex(24)
        private_write(stop_file, json_bytes({'project': project, 'container': root.name, 'network': root.name}))
        user = native(admin, '/v2/users', {'username': project, 'password': password, 'is_admin': False})['id']
        private_write(stop_file, json_bytes({'project': project, 'gpu_user': user, 'container': root.name, 'network': root.name}))
        owner = login(project, password)
        key_record = native(owner, '/v2/api-keys', {'name': project, 'scope': ['inference'],
                                                  'allowed_model_names': allowed, 'expires_in': 30 * 3600})
        key_id = key_record['id']
        private_write(stop_file, json_bytes({'project': project, 'gpu_user': user, 'gpu_key': key_id,
                                            'container': root.name, 'network': root.name}))
        key = key_record.get('value') or key_record['api_key']
        bearer = build_opener(ProxyHandler({}))
        for route in allowed:
            native(bearer, '/token-auth', headers={'Authorization': 'Bearer ' + key, 'x-higress-llm-model': route})
        private_write(config_dir / 'key', key.encode() + b'\n', uid, gid)
        private_write(secrets_path, key.encode() + b'\n', uid, gid)
        binding_data['credentials'].append({'id': project, 'project': project, 'enabled': True,
                                            'models': list(MODELS), 'secret_file': '/run/secrets/' + project})
        if bindings_path.read_bytes() != original or stop.is_set() or not idle():
            raise RuntimeError('Binding changed during preparation or stop requested')
        private_write(bindings_path, json_bytes(binding_data), ownership.st_uid, ownership.st_gid)
        bindings_written = True
        reload_bindings()
        config = {'project': project, 'fixtures': '/run/maas-code', 'key_file': '/run/maas-test/key'}
        private_write(config_dir / 'workload.json', json_bytes(config), uid, gid)
        with (code / 'endurance_24h_observe.py').open('rb') as sampler:
            process = subprocess.Popen(['docker', 'exec', '-i', api, 'python', '-B', '-', project],
                                       stdin=sampler, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        publisher = threading.Thread(target=publish, daemon=True)
        publisher.start()
        for _ in range(30):
            if (stats_dir / 'snapshot.json').is_file():
                break
            if process.poll() is not None or stop.wait(1):
                raise RuntimeError('Observer exited before readiness')
        else:
            raise RuntimeError('Observer readiness expired')
        service_ip = socket.gethostbyname('maas-service')
        command('docker', 'network', 'create', '--driver', 'bridge', root.name)
        network = True
        if stop.wait(8):
            raise RuntimeError('Stop requested before workload')
        command('docker', 'run', '-d', '--name', root.name, '--restart', 'no', '--stop-timeout', '960',
                '--network', root.name, '--add-host', f'maas-service:{service_ip}', '--cap-drop', 'ALL',
                '--security-opt', 'no-new-privileges', '--user', f'{uid}:{gid}', '--entrypoint', 'python',
                '-v', f'{code}:/run/maas-code:ro', '-v', f'{config_dir}:/run/maas-test:ro',
                '-v', f'{stats_dir}:/run/maas-observe:ro', '-v', f'{stage}:/var/log/maas-endurance',
                reference['Image'], '-B', '/run/maas-code/endurance_24h.py', '/run/maas-test/workload.json',
                '/var/log/maas-endurance', '/run/maas-observe/snapshot.json')
        container = True
        print(json.dumps({'phase': 'CONTAINER_STARTED', 'run_id': run_id}), flush=True)
        while not stop.wait(5):
            current = command('docker', 'inspect', '-f', '{{.State.Running}}', root.name).strip()
            if current == b'false':
                break
    except Exception as exc:
        report['supervisor_error'] = type(exc).__name__
        if isinstance(exc, subprocess.CalledProcessError):
            report['failed_command'] = [Path(exc.cmd[0]).name, *exc.cmd[1:3]]
            report['exit_code'] = exc.returncode
        elif isinstance(exc, HTTPError):
            report['http_status'] = exc.code
            report['api_path'] = '/' + exc.url.split('/', 3)[-1].split('?', 1)[0]
    finally:
        stopped = True
        if container:
            stopped = cleanup_step('stop_runner', lambda: command('docker', 'stop', '--time', '960', root.name, timeout=975))
            if (stage / 'status.json').exists():
                status = json.loads((stage / 'status.json').read_text())
            if stopped:
                cleanup_step('remove_runner', lambda: command('docker', 'rm', root.name))
        if network:
            cleanup_step('remove_network', lambda: command('docker', 'network', 'rm', root.name))
        if process is not None:
            if process.poll() is None:
                process.terminate()
            cleanup_step('stop_observer', lambda: process.wait(timeout=5))
            publisher.join(timeout=5)
        restored = not bindings_written
        if bindings_written and stopped:
            cleanup_step('remove_maas_project_data', cleanup_app)
            restored = cleanup_step('restore_bindings', unbind)
        if stopped:
            if restored:
                cleanup_step('remove_bound_key_file', lambda: secrets_path.unlink(missing_ok=True))
            if key_id is not None:
                cleanup_step('revoke_gpu_key', lambda: native(login(project, password), f'/v2/api-keys/{key_id}', method='DELETE'))
            if user is not None and (not container or cleanup_step('remove_gpu_usage', cleanup_native_usage)):
                cleanup_step('remove_gpu_user', lambda: native(login('admin', (control / 'operator/gpustack-admin.password').read_text().strip()),
                                                               f'/v2/users/{user}', method='DELETE'))
        report.update(status=status, cleanup_errors=cleanup_errors)
        private_write(reports / (run_id + '.json'), json_bytes(report))
        print(json.dumps(report), flush=True)
        if not cleanup_errors:
            shutil.rmtree(root)
    return 0 if status['phase'] == 'LOAD_COMPLETE' and report.get('reconciled') and not cleanup_errors else 1


if __name__ == '__main__':
    raise SystemExit(supervise(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()))
