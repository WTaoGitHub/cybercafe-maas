from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
from threading import Event
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from maas_bootstrap.client import BootstrapError, LocalState, ReleaseVerifier, atomic_json
from maas_bootstrap.enrollment import StartDenied, WorkerStartBroker, approve
from maas_bootstrap import handoff
from maas_bootstrap.service import create_app
from maas_bootstrap.worker import NativeDockerWorker, WORKER_CA, apply
from test_client import Fetcher, setup
from test_service import CODE, publish


GPU = 'GPU-11111111-2222-4333-8444-555555555555'
OTHER_GPU = 'GPU-aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'
PERMIT = 'gpustack_maas-handoff_' + 'f' * 64


def binding(cluster=3):
    return {'cluster_id':cluster,'worker_id':41,'worker_name':GPU,'external_id':GPU}


def permit(session, **overrides):
    return {**binding(),'session_id':session,'registration_token':PERMIT,
            'expires_at':(datetime.now(timezone.utc)+timedelta(minutes=4)).isoformat(),**overrides}


@pytest.fixture
def diskless(setup, tmp_path):
    settings, sign = setup
    native_url = 'https://native.example.invalid:443'
    settings.data.update(allowed_server_urls=[native_url],worker_start_grant_file=str(tmp_path/'boot-grant'))
    manifest = dict(sign()['manifest'],worker_name_policy='gpu-external-id',server_url=native_url)
    envelope = sign(manifest)
    envelope['registration'].pop('token')
    return settings,envelope,sign


class Native:
    def __init__(self, uncertain=False):
        self.calls = []
        self.uncertain = uncertain

    def post(self, action, body):
        self.calls.append((action,body))
        if action == 'enrollment':return binding()
        if self.uncertain:raise BootstrapError('ENROLLMENT_UNAVAILABLE')
        return permit(body['session_id'])


def service(diskless, tmp_path, native, *, manual_grant=True, **previous):
    settings,envelope,_ = diskless
    config, approvals = tmp_path/'published', tmp_path/'approvals'
    (config/'releases').mkdir(parents=True);approvals.mkdir(mode=0o700)
    scope = {'site_id':'lab','batch_id':'pilot','cluster_id':3,'image_id':'tower-v1','config_revision':'r1'}
    atomic_json(config/'policy.json',{'schema_version':1,'allowed_cidrs':['127.0.0.1/32'],
        'grants':[{**scope,'code_sha256':hashlib.sha256(CODE.encode()).hexdigest(),'enabled':True}]})
    publish(config,envelope)
    output = Path(settings.data['worker_start_grant_file'])
    result = approve(approvals,output,**scope,gpu=GPU,**previous) if manual_grant else None
    if manual_grant:
        assert output.stat().st_mode & 0o777 == 0o600
    body = {key:scope[key] for key in ('cluster_id','image_id','config_revision')} | {'gpu_uuid':GPU}
    client = TestClient(create_app(config,worker_start=WorkerStartBroker(approvals,native)),client=('127.0.0.1',12345))
    return client,body,output.read_text().strip() if manual_grant else None,result['session_id'] if result else None


def test_boot_grant_is_scoped_and_retries_return_same_permit(diskless,tmp_path):
    native = Native()
    client,body,token,session = service(diskless,tmp_path,native)
    path = '/bootstrap/v1/worker-start'
    assert client.post(path,json=body,headers={'Authorization':'Bearer '+CODE}).status_code==401
    for mismatch in ({'gpu_uuid':OTHER_GPU},{'cluster_id':4},{'config_revision':'r2'},{'image_id':'other'}):
        response = client.post(path,json=body|mismatch,headers={'Authorization':'Bearer '+token})
        assert response.status_code==403 and token not in response.text
    assert not native.calls
    headers = {'Authorization':'Bearer '+token}
    first = client.post(path,json=body,headers=headers)
    assert first.status_code==200 and first.headers['Cache-Control']=='no-store'
    assert first.json()['session_id']==session
    assert client.post(path,json=body,headers=headers).json()==first.json()
    assert native.calls==[('enrollment',{'cluster_id':3,'gpu_uuid':GPU}),
                          ('handoff',{'worker_id':41,'gpu_uuid':GPU,'session_id':session})]
    config = client.get('/bootstrap/v1/config?image_id=tower-v1',headers={'Authorization':'Bearer '+CODE})
    assert config.status_code==200 and config.json()['registration']=={'cluster_id':3}


def test_restricted_automatic_start_requires_approved_gpu_and_serializes_boots(diskless,tmp_path):
    entered, release = Event(), Event()

    class BlockingNative(Native):
        offline = False

        def post(self, action, body):
            assert action == 'handoff' and body['automatic']
            if self.calls and not self.offline:
                raise StartDenied(409, 'NATIVE_HANDOFF_REJECTED')
            entered.set()
            assert release.wait(5)
            return super().post(action, body)

    native = BlockingNative()
    client,body,_,_ = service(diskless,tmp_path,native,manual_grant=False)
    path = '/bootstrap/v1/worker-start/automatic'
    body['boot_id'] = str(uuid4())
    headers = {'Authorization':'Bearer '+CODE}
    assert client.post(path,json=body).status_code==401
    assert client.post(path,json=body,headers=headers).status_code==403
    policy_path = tmp_path/'published'/'policy.json'
    policy = json.loads(policy_path.read_bytes())
    policy['grants'][0]['auto_start_gpus'] = {GPU:41}
    atomic_json(policy_path, policy)
    assert client.post(path,json=body|{'gpu_uuid':OTHER_GPU},headers=headers).status_code==403
    assert client.post(path,json=body|{'cluster_id':4},headers=headers).status_code==403
    assert not native.calls
    other_boot = body|{'boot_id':str(uuid4())}
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, path, json=body, headers=headers)
        try:
            assert entered.wait(5)
            assert client.post(path,json=other_boot,headers=headers).status_code==409
        finally:
            release.set()
        first = pending.result(timeout=5)
    assert first.status_code==200
    assert client.post(path,json=body,headers=headers).json()==first.json()
    assert client.post(path,json=other_boot,headers=headers).status_code==409
    native.offline = True
    second = client.post(path,json=other_boot,headers=headers)
    assert second.status_code==200 and second.json()['worker_id']==41
    assert second.json()['session_id']!=first.json()['session_id']
    assert client.post(path,json=other_boot,headers=headers).json()==second.json()
    assert native.calls==[
        ('handoff',{'worker_id':41,'gpu_uuid':GPU,'automatic':True,'session_id':r.json()['session_id']})
        for r in (first,second)
    ]


def test_automatic_client_sends_boot_id_and_prefers_manual_grant(diskless,monkeypatch):
    settings,envelope,_ = diskless
    settings.data['worker_start_automatic'] = True
    boot, session = str(uuid4()), str(uuid4())
    calls = []
    real_read = handoff.read_file
    monkeypatch.setattr(handoff, 'read_file', lambda p,**kw:
                        boot.encode() if str(p)=='/proc/sys/kernel/random/boot_id' else real_read(p,**kw))

    class Response(io.BytesIO):
        status = 200

    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            return Response(json.dumps(permit(session)).encode())

    monkeypatch.setattr(handoff, 'https_opener', lambda _:Opener())
    fetcher = handoff.WorkerStartFetcher()
    assert fetcher.fetch(settings,envelope['manifest'],GPU)['session_id']==session
    assert calls[-1].full_url.endswith('/worker-start/automatic')
    assert json.loads(calls[-1].data)['boot_id']==boot
    manual = session+'.'+'a'*64
    path = Path(settings.data['worker_start_grant_file'])
    path.write_text(manual)
    path.chmod(0o600)
    fetcher.fetch(settings,envelope['manifest'],GPU)
    assert calls[-1].full_url.endswith('/worker-start')
    assert calls[-1].get_header('Authorization')=='Bearer '+manual


def test_ambiguous_handoff_does_not_issue_again(diskless,tmp_path):
    native = Native(uncertain=True)
    client,body,token,session = service(diskless,tmp_path,native,worker_id=41,
        previous_session_id='2e6df8ac-35db-4d28-907c-59524cc7b0a5',fence_receipt='verified exit receipt')
    headers = {'Authorization':'Bearer '+token}
    path = '/bootstrap/v1/worker-start'
    assert client.post(path,json=body,headers=headers).status_code==503
    again = client.post(path,json=body,headers=headers)
    assert again.status_code==409 and again.json()['error']['code']=='WORKER_START_NEEDS_RECONCILIATION'
    assert native.calls==[('handoff',{'worker_id':41,'gpu_uuid':GPU,'session_id':session,
        'previous_session_id':'2e6df8ac-35db-4d28-907c-59524cc7b0a5','fence_receipt':'verified exit receipt'})]


class PermitFetcher:
    def __init__(self, result):
        self.result,self.calls = result,0

    def fetch(self, *_):
        self.calls += 1
        if isinstance(self.result, BootstrapError):raise self.result
        return self.result


class Runtime:
    def __init__(self):
        self.container,self.creates,self.starts = None,0,0

    def guard(self):pass
    def inspect(self):return self.container
    def validate_layout(self,*_):return {}
    def check_environment(self,*_):return GPU
    def check_diskless_storage(self,*_):pass

    def create(self, settings, envelope, metadata, identity, env_file):
        assert (Path(settings.data['worker_data_dir'])/'external_id').read_bytes()==GPU.encode()
        assert f'GPUSTACK_TOKEN={PERMIT}\n' in env_file.read_text()
        self.creates+=1
        self.container={'running':False,'status':'created','fingerprint':metadata['fingerprint'],
                        'session_id':identity['session_id']}

    def start(self):
        self.starts+=1
        self.container.update(running=True,status='running')

    def registered(self, settings, envelope, identity):
        return {'worker_id':identity['worker_id'],'worker_name':identity['worker_name'],
                'cluster_id':identity['cluster_id'],'native_state':'ready'}


@pytest.mark.parametrize('result,code',[
    (BootstrapError('WORKER_START_UNAVAILABLE'),'WORKER_START_UNAVAILABLE'),
    ({'worker_name':OTHER_GPU},'WORKER_BINDING_MISMATCH'),
    ({'cluster_id':4},'WORKER_BINDING_MISMATCH'),
    ({'expires_at':'2001-01-01T00:00:00+00:00'},'WORKER_PERMIT_EXPIRED'),
])
def test_invalid_or_missing_permit_does_not_create_worker(diskless,result,code):
    settings,envelope,_ = diskless
    runtime = Runtime()
    provided = result if isinstance(result,BootstrapError) else permit(str(uuid4()),**result)
    with pytest.raises(BootstrapError,match=code):
        apply(settings,backend=runtime,fetcher=Fetcher(envelope),permit_fetcher=PermitFetcher(provided))
    assert runtime.creates==runtime.starts==0


def test_running_apply_reuses_identity_and_exited_worker_requires_reprovision(diskless):
    settings,envelope,_ = diskless
    f = Fetcher(envelope)
    runtime = Runtime()
    p = PermitFetcher(permit(str(uuid4())))
    result = apply(settings,backend=runtime,fetcher=f,permit_fetcher=p)
    assert result['worker_id']==41 and result['gpu_uuid']==GPU
    assert apply(settings,backend=runtime,fetcher=f,permit_fetcher=p)['code']=='UNCHANGED'
    state = LocalState(settings.data['state_dir'])
    assert PERMIT not in json.dumps(state.status())
    assert PERMIT not in (state.root/'identity.json').read_text()
    assert not list((state.root/'generations').rglob('worker.env'))
    runtime.container.update(running=False,status='exited')
    with pytest.raises(BootstrapError,match='HANDOFF_REPROVISION_REQUIRED'):
        apply(settings,backend=runtime,fetcher=f,permit_fetcher=p)
    assert runtime.creates==runtime.starts==p.calls==1


def test_signed_diskless_release_requires_tls_and_omits_cluster_token(diskless):
    settings,envelope,sign = diskless
    ReleaseVerifier().verify(settings,envelope)
    with pytest.raises(BootstrapError,match='DISKLESS_RELEASE_INVALID'):
        ReleaseVerifier().verify(settings,sign(envelope['manifest']))
    legacy = sign(dict(envelope['manifest'],worker_name_policy='native-independent'))
    with pytest.raises(BootstrapError,match='DISKLESS_RELEASE_INVALID'):
        ReleaseVerifier().verify(settings,legacy)
    url = 'http://native.example.invalid:80'
    settings.data['allowed_server_urls'].append(url)
    bad = sign(dict(envelope['manifest'],server_url=url))
    bad['registration'].pop('token')
    with pytest.raises(BootstrapError,match='DISKLESS_RELEASE_INVALID'):
        ReleaseVerifier().verify(settings,bad)


def test_diskless_layout_mounts_pinned_ca_readonly(diskless,monkeypatch):
    import maas_bootstrap.worker as worker
    settings,envelope,_ = diskless
    monkeypatch.setattr(worker,'run',lambda _args:'[{"Config":{"Env":[]}}]')
    layout = NativeDockerWorker().desired_layout(settings,envelope)
    assert layout['env']['SSL_CERT_FILE']==WORKER_CA
    assert {'source':settings.data['tls_certificate_file'],'target':WORKER_CA,'rw':False} in layout['mounts']
    assert WORKER_CA in layout['env']['GPUSTACK_RUNTIME_DEPLOY_MIRRORED_DEPLOYMENT_IGNORE_VOLUMES'].split(';')
