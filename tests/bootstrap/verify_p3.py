"""Run inside a fresh loopback-only GPUStack v2.2.3 lab with P2 required."""

import asyncio
from hashlib import sha256
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import ssl
import subprocess
from tempfile import TemporaryDirectory
from threading import Thread
import time
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from gpustack.config.config import Config
from gpustack.worker.worker_manager import WorkerManager
import httpx
import uvicorn

from maas_bootstrap.client import BootstrapError, LocalSettings, atomic_bytes, atomic_json
from maas_bootstrap.enrollment import NativeEnrollment, StartDenied, WorkerStartBroker, approve
from maas_bootstrap.publisher import Publisher
from maas_bootstrap.service import create_app
from maas_bootstrap.worker import NativeDockerWorker, apply
from verify_p1 import BASE, SyntheticCollector, bearer, check, workers


class NativeTLSProxy(BaseHTTPRequestHandler):
    def forward(self):
        connection = HTTPConnection('127.0.0.1', 30080, timeout=8)
        try:
            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            headers = {key:self.headers[key] for key in ('Authorization','Content-Type') if key in self.headers}
            connection.request(self.command, self.path, body, headers)
            upstream = connection.getresponse()
            raw = upstream.read()
            self.send_response(upstream.status)
            self.send_header('Content-Type', upstream.getheader('Content-Type') or 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        finally:
            connection.close()

    do_GET = do_POST = forward

    def log_message(self, *_):
        pass


class SyntheticRuntime(NativeDockerWorker):
    def __init__(self, gpu, cert, fail_start=False):
        self.gpu, self.cert, self.fail_start = gpu, cert, fail_start
        self.container = None
        self.creates = 0

    def guard(self):
        pass

    def inspect(self):
        return self.container

    def validate_layout(self, *_):
        return {}

    def check_environment(self, *_):
        return self.gpu

    def create(self, settings, envelope, metadata, identity, env_file):
        self.folder = Path(settings.data['worker_data_dir'])
        assert (self.folder/'external_id').read_bytes() == self.gpu.encode()
        assert env_file.stat().st_mode & 0o777 == 0o600
        self.env = dict(line.split('=', 1) for line in env_file.read_text().splitlines())
        self.container = {'status':'created','running':False,'fingerprint':metadata['fingerprint'],
                          'session_id':identity['session_id']}
        self.creates += 1

    def start(self):
        if self.fail_start:
            self.fail_start = False
            raise BootstrapError('RUNTIME_COMMAND_FAILED')
        self.container.update(status='running', running=True)
        os.environ['SSL_CERT_FILE'] = str(self.cert)
        cfg = Config(data_dir=str(self.folder), server_url=self.env['GPUSTACK_SERVER_URL'],
                     token=self.env['GPUSTACK_TOKEN'], worker_name=self.env['GPUSTACK_WORKER_NAME'])
        collector = SyntheticCollector(self.folder, self.gpu)
        manager = WorkerManager(cfg=cfg, is_embedded=False, collector=collector)
        asyncio.run(manager.register_with_server())
        self.worker_token = (self.folder/'worker_token').read_text().strip()
        with httpx.Client(base_url=cfg.server_url, trust_env=False,
                          verify=ssl.create_default_context(cafile=str(self.cert))) as worker:
            check(worker.post('/v2/worker-status', headers=bearer(self.worker_token),
                              json=collector.timed_collect().model_dump(mode='json')), 204)


def fails(settings, runtime, code):
    try:
        apply(settings, backend=runtime, timeout=0)
    except BootstrapError as error:
        assert error.code == code, (error.code, code)
    else:
        raise AssertionError(f'Expected {code}')


def main():
    with TemporaryDirectory(prefix='maas-p3-', dir='/dev/shm') as tmp, httpx.Client(base_url=BASE, trust_env=False) as admin:
        root = Path(tmp)
        assert os.environ['MAAS_WORKER_HANDOFF_MODE'] == 'required'
        assert check(admin.get('/version'))['version'] == 'v2.2.3'
        check(admin.post('/auth/login', data={'username':'admin','password':os.environ['GPUSTACK_BOOTSTRAP_PASSWORD']}))
        assert check(admin.get('/v2/clusters'))['items'] == [] and workers(admin) == [], 'Fresh lab only'
        cluster = check(admin.post('/v2/clusters', json={'name':'e5-p3-lab','provider':'Docker'}))['id']
        cert, tls_key = root/'tls.crt', root/'tls.key'
        subprocess.run(['openssl','req','-x509','-nodes','-newkey','rsa:2048','-days','1', '-subj','/CN=127.0.0.1',
                        '-addext','subjectAltName=IP:127.0.0.1','-keyout',str(tls_key),'-out',str(cert)],
                       check=True, capture_output=True)
        native = ThreadingHTTPServer(('127.0.0.1',0), NativeTLSProxy)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain(cert,tls_key)
        native.socket = tls.wrap_socket(native.socket,server_side=True)
        Thread(target=native.serve_forever,daemon=True).start()
        native_url = f'https://127.0.0.1:{native.server_port}'
        server_port = 18840
        releases, approvals = root/'releases-state', root/'approvals'
        releases.mkdir(mode=0o700);approvals.mkdir(mode=0o700)
        signing = Ed25519PrivateKey.generate()
        key_file, pub_file, shared_file = root/'signing.key',root/'signing.pub',root/'shared-code'
        atomic_bytes(key_file, signing.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                      serialization.NoEncryption()))
        atomic_bytes(pub_file, signing.public_key().public_bytes(serialization.Encoding.PEM,
                                                                 serialization.PublicFormat.SubjectPublicKeyInfo))
        atomic_bytes(shared_file, uuid4().hex.encode())
        common = {'site_id':'lab','batch_id':'p3','cluster_id':cluster,'image_id':'e5-lab'}
        atomic_json(releases/'policy.json', {'schema_version':1,'allowed_cidrs':['127.0.0.1/32'],'grants':[
            {**common,'enabled':True,'code_sha256':sha256(shared_file.read_bytes()).hexdigest()}]})
        broker = WorkerStartBroker(approvals, NativeEnrollment(BASE,os.environ['MAAS_WORKER_ENROLLMENT_KEY_FILE']))
        config = uvicorn.Config(create_app(releases,worker_start=broker), host='127.0.0.1', port=server_port,
                               ssl_certfile=str(cert),ssl_keyfile=str(tls_key),access_log=False,log_level='error')
        service = uvicorn.Server(config)
        Thread(target=service.run,daemon=True).start()
        for _ in range(100):
            if service.started:break
            time.sleep(.05)
        assert service.started
        gpu = f'GPU-{uuid4()}'

        def clone(tag, previous=None, worker_id=None):
            folder = root/tag
            settings_path, grant_path = folder/'bootstrap.json', folder/'boot-grant'
            settings = {**common,'schema_version':1,'config_service_url':f'https://127.0.0.1:{server_port}',
                        'shared_code_file':str(shared_file),'tls_certificate_file':str(cert),
                        'release_public_key_file':str(pub_file),'release_key_id':'lab',
                        'worker_start_grant_file':str(grant_path),'state_dir':str(folder/'state'),
                        'worker_data_dir':str(folder/'native'),'model_root':str(folder/'models'),
                        'model_cache_dir':str(folder/'cache'),'architecture':'x86_64','allowed_server_urls':[native_url]}
            atomic_json(settings_path,settings)
            loaded = LocalSettings(settings_path)
            manifest = {**{key:settings[key] for key in ('site_id','batch_id','cluster_id','architecture','worker_data_dir')},
                        'schema_version':1,'config_revision':tag,'compatible_image_ids':[common['image_id']],
                        'gpustack_version':'v2.2.3','server_url':native_url,'worker_name_policy':'gpu-external-id',
                        'worker_image_digest':'test/gpustack@sha256:'+'a'*64,'model_mounts':[]}
            Publisher(releases,loaded,key_file).publish(manifest,{'cluster_id':cluster})
            approve(approvals,grant_path,**common,gpu=gpu,config_revision=tag,worker_id=worker_id,
                    previous_session_id=previous,fence_receipt='lab-confirmed-exit' if previous else None)
            return loaded

        settings_a = clone('r-a')
        a = SyntheticRuntime(gpu,cert)
        initial = apply(settings_a,backend=a)
        assert initial['native_state']=='ready' and initial['gpu_uuid']==gpu
        assert apply(settings_a,backend=a)['code']=='UNCHANGED' and a.creates==1
        print('PASS MaaS HTTPS approval, first enrollment, E5 launch and ready read-back',flush=True)
        settings_b = clone('r-b',initial['session_id'],initial['worker_id'])
        b = SyntheticRuntime(gpu,cert,fail_start=True)
        fails(settings_b,b,'WORKER_START_BLOCKED')
        assert b.creates==0
        subprocess.run(['runuser','-u','postgres','--','psql','-d','gpustack','-XAtqc',
                        "UPDATE workers SET state='NOT_READY', heartbeat_time=(now() AT TIME ZONE 'UTC') - interval '1 hour' "
                        f"WHERE id={initial['worker_id']}"],check=True)
        try:
            broker.native.post('handoff',{'worker_id':initial['worker_id'],'gpu_uuid':f'GPU-{uuid4()}',
                'session_id':str(uuid4()),'previous_session_id':initial['session_id'],'fence_receipt':'lab-confirmed-exit'})
        except StartDenied as error:
            assert error.code=='NATIVE_HANDOFF_REJECTED'
        else:
            raise AssertionError('Native handoff accepted a mismatched GPU binding')
        fails(settings_b,b,'RUNTIME_COMMAND_FAILED')
        continued = apply(settings_b,backend=b)
        assert b.creates==1 and continued['worker_id']==initial['worker_id'] and continued['session_id']!=initial['session_id']
        assert continued['native_state']=='ready' and b.worker_token!=a.worker_token
        with httpx.Client(base_url=BASE,trust_env=False) as stale:
            check(stale.post('/v2/worker-heartbeat',headers=bearer(a.worker_token)),401)
        assert len(workers(admin))==1
        print('PASS active worker blocks takeover; approved handoff, created-container retry and old-token revocation',flush=True)
        service.should_exit = True
        native.shutdown()


if __name__ == '__main__':
    main()
