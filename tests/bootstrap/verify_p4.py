"""Validate P4 Nginx ingress, worker retirement and legacy recovery in a fresh lab."""

import asyncio
import os
from pathlib import Path
import ssl
import subprocess
from tempfile import TemporaryDirectory
import time
from uuid import UUID, uuid4

from gpustack.websocket_proxy.message import BaseClientInfo
import httpx
from websockets.sync.client import connect

from verify_p1 import BASE, ENROLL, bearer, check, register, workers
from verify_p3 import main as verify_startup


RETIRE = '/internal/maas/worker-retire'


def sql(statement):
    return subprocess.check_output(['runuser','-u','postgres','--','psql','-d','gpustack','-XAtqc',statement],
                                   text=True).strip()


def main():
    url = os.environ['MAAS_P4_LAB_NATIVE_URL']
    cert = Path(os.environ['MAAS_P4_LAB_TLS_DIR'])/'tls.crt'
    ssl_context = ssl.create_default_context(cafile=str(cert))
    with httpx.Client(base_url=url,verify=ssl_context,trust_env=False) as ingress:
        assert check(ingress.get('/version'))['version']=='v2.2.3'
        assert ingress.post(ENROLL,json={}).status_code==404
        assert ingress.post(RETIRE,json={}).status_code==404
        assert ingress.post('/auth/login',data={}).status_code==404
        assert ingress.get('/v2/clusters/1/registration-token').status_code==404
    try:
        httpx.get(url+'/version',trust_env=False)
    except httpx.ConnectError:
        pass
    else:
        raise AssertionError('Untrusted TLS certificate was accepted')
    denied = subprocess.check_output(['curl','--noproxy','*','--interface','127.0.0.2','--cacert',str(cert),
                                     '-sS','-o','/dev/null','-w','%{http_code}',url+'/version'],text=True)
    assert denied=='403'
    print('PASS Nginx verifies TLS and source, and excludes internal and human management routes',flush=True)

    verify_startup()
    with httpx.Client(base_url=BASE,trust_env=False) as admin, httpx.Client(base_url=BASE,trust_env=False) as peer:
        check(admin.post('/auth/login',data={'username':'admin','password':os.environ['GPUSTACK_BOOTSTRAP_PASSWORD']}))
        row, = workers(admin)
        worker_id, gpu, cluster = row['id'], row['external_id'], row['cluster_id']
        assert row['state']=='ready'
        cluster_token = check(admin.get(f'/v2/clusters/{cluster}/registration-token'))['token']
        maas_key = Path(os.environ['MAAS_WORKER_ENROLLMENT_KEY_FILE']).read_text().strip()
        session_id = sql(f'SELECT session_id FROM maas_worker_handoffs WHERE worker_id={worker_id}')
        worker_uuid, old_token = sql(f'SELECT worker_uuid, token FROM workers WHERE id={worker_id}').split('|')
        body = {'worker_id':worker_id,'gpu_uuid':gpu,'session_id':session_id,'fence_receipt':'lab-isolation-confirmed'}

        def retire(expected, **overrides):
            return check(peer.post(RETIRE,json=body|overrides,headers=bearer(maas_key)),expected)

        check(admin.delete(f'/v2/workers/{worker_id}'),409)
        check(peer.post(RETIRE,json=body,headers=bearer(cluster_token)),401)
        retire(409)
        headers = {**BaseClientInfo(UUID(worker_uuid),['127.0.0.1/32']).to_headers(),**bearer(old_token)}
        with connect(url.replace('https:','wss:',1)+'/connect',ssl=ssl_context,additional_headers=headers,proxy=None):
            sql("UPDATE workers SET state='NOT_READY', heartbeat_time=(now() AT TIME ZONE 'UTC') - interval '1 hour' "
                f'WHERE id={worker_id}')
            assert 'tunnel' in retire(409)['detail']
            handoff = {'worker_id':worker_id,'gpu_uuid':gpu,'session_id':str(uuid4()),
                       'previous_session_id':session_id,'fence_receipt':'lab-isolation-confirmed'}
            result = check(peer.post('/internal/maas/worker-handoff',json=handoff,headers=bearer(maas_key)),409)
            assert 'tunnel' in result['detail']
        print('PASS real WSS through Nginx; a connected worker tunnel blocks handoff and retirement',flush=True)

        model = check(admin.post('/v2/models',json={
            'name':'p4-lab-placement-reference','source':'local_path','local_path':'/tmp/p4-lab-placement',
            'cluster_id':cluster,'replicas':0,'categories':['llm'],'worker_selector':{'worker-name':gpu},
        }))
        for _ in range(30):
            blocked = retire(409)['detail']
            if 'placement' in blocked:break
            time.sleep(.1)
        assert 'placement' in blocked
        check(admin.delete(f"/v2/models/{model['id']}"))
        retire(409,session_id=str(uuid4()))
        assert retire(200)['worker_id']==worker_id
        check(admin.get(f'/v2/workers/{worker_id}'),404)
        check(peer.post('/v2/worker-heartbeat',headers=bearer(old_token)),401)
        assert sql(f'SELECT count(*) FROM maas_worker_handoffs WHERE worker_id={worker_id}')=='0'
        rebound = check(peer.post(ENROLL,json={'cluster_id':cluster,'gpu_uuid':gpu},headers=bearer(maas_key)))
        assert rebound['worker_id']!=worker_id
        retire(200,worker_id=rebound['worker_id'],session_id=None)
        print('PASS retirement checks references and session, revokes credentials and frees the same GPU name',flush=True)

        with TemporaryDirectory(prefix='p4-legacy-') as directory:
            legacy = asyncio.run(register(Path(directory)/'legacy',cluster_token,'e5-'+uuid4().hex[:24],gpu))
        assert legacy['id'] not in {worker_id,rebound['worker_id']} and legacy['external_id'] is None
        print('PASS ordinary native registration on the same synthetic GPU after managed retirement',flush=True)


if __name__=='__main__':
    main()
