"""Bounded, non-streaming chat ingress with durable maintenance admission.

Only this ingress is covered. Native GPUStack/Playground/worker ports must be
closed to customers at the network edge before claiming global maintenance.
"""
import asyncio,json,uuid
import httpx
from fastapi.responses import Response
from .errors import ServiceError


class SyncIngress:
    def __init__(self,service,client_factory=None):
        self.service=service
        self.client_factory=client_factory or httpx.AsyncClient

    def begin(self,who,name,runtime,probe=False):
        with self.service.db.transaction() as c:
            maintenance=c.execute('SELECT * FROM maintenance WHERE id=1 FOR UPDATE').fetchone()
            self.service.expire_sync(c)
            if (maintenance['paused'] and not probe) or (probe and not maintenance['paused']):
                raise ServiceError('MAINTENANCE_PAUSED','Inference admission is paused or probe window is closed',503)
            if self.service.has_unknown(c):
                raise ServiceError('BACKEND_QUARANTINED','Unresolved backend activity blocks inference',503)
            total=c.execute("SELECT (SELECT count(*) FROM sync_activity WHERE state='DISPATCHING') + (SELECT count(*) FROM tasks WHERE state IN ('RUNNING','CANCEL_REQUESTED')) n").fetchone()['n']
            own=c.execute("SELECT (SELECT count(*) FROM sync_activity WHERE state='DISPATCHING' AND project=%s) + (SELECT count(*) FROM tasks WHERE state IN ('RUNNING','CANCEL_REQUESTED') AND project=%s) n",(who.project,who.project)).fetchone()['n']
            if total>=self.service.settings.max_running or own>=self.service.settings.max_project_running:
                raise ServiceError('SYNC_CAPACITY','Synchronous admission capacity reached',429,True)
            token=uuid.uuid4().hex
            c.execute("INSERT INTO sync_activity(token,project,model,state,deadline,is_probe) VALUES(%s,%s,%s,'DISPATCHING',now()+%s*interval '1 second',%s)",(token,who.project,name,runtime,probe))
            return token

    def finish(self,token,confirmed,successful=False):
        with self.service.db.transaction() as c:
            row=c.execute("UPDATE sync_activity SET state=%s,outcome=%s,finished_at=now(),updated_at=now() WHERE token=%s AND state IN ('DISPATCHING','UNKNOWN') RETURNING *",('COMPLETED' if confirmed else 'UNKNOWN','SUCCEEDED' if confirmed and successful else ('REJECTED' if confirmed else 'UNKNOWN'),token)).fetchone()
            if row and confirmed and successful and not row['is_probe']:
                c.execute('INSERT INTO sync_usage_events(token,project,model) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',(token,row['project'],row['model']))

    async def infer(self,who,body,probe=False,prepare_input=None,map_output=None):
        if not isinstance(body,dict) or not isinstance(body.get('model'),str) or body.get('stream',False) is not False:
            raise ServiceError('SYNC_INPUT','Use a registered logical model and stream=false',422)
        name=body['model'];model=self.service.model(name)
        if model['driver']!='gpustack.ocr':
            raise ServiceError('SYNC_UNSUPPORTED','This synchronous pilot supports the registered OCR connector',422)
        self.service.auth.authorize(who,name,model)
        key=self.service.auth.key_for(who)
        runtime=min(model['max_runtime_s'],120)
        token=self.begin(who,name,runtime+(10 if prepare_input else 0),probe)
        confirmed=True;successful=False
        try:
            if prepare_input:
                try:
                    async with asyncio.timeout(10):body=await prepare_input()
                except TimeoutError:
                    raise ServiceError('IMAGE_FETCH_TIMEOUT','Input preparation exceeded its budget before inference',408) from None
            confirmed=False
            async with asyncio.timeout(runtime):
                async with self.client_factory(trust_env=False,follow_redirects=False,timeout=httpx.Timeout(runtime,connect=min(5,runtime))) as client:
                    async with client.stream('POST',self.service.settings.gpustack_url+'/v1/chat/completions',
                        headers={'Authorization':'Bearer '+key,'Accept-Encoding':'identity'},json={**body,'model':model['upstream_model']}) as response:
                        if response.headers.get('content-encoding','identity')!='identity':
                            raise ServiceError('OUTPUT_ENCODING','Compressed responses are unsupported',502)
                        raw=bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(raw)+len(chunk)>self.service.settings.max_output_bytes:
                                raise ServiceError('OUTPUT_SIZE','Inference output exceeds limit',502)
                            raw.extend(chunk)
                        confirmed=response.status_code in (200,400,401,403,404,413,415,422)
                        if not confirmed:
                            raise ServiceError('UNKNOWN_BACKEND_ACTIVITY','Upstream completion is unconfirmed',503)
                        if response.status_code!=200:
                            raise ServiceError('UPSTREAM_REJECTED','Inference rejected by upstream',response.status_code)
                        try:json.loads(raw)
                        except ValueError:raise ServiceError('OUTPUT_INVALID','Upstream returned invalid JSON',502) from None
                        output=map_output(bytes(raw)) if map_output else bytes(raw)
                        if len(output)>self.service.settings.max_output_bytes:
                            raise ServiceError('OUTPUT_SIZE','Converted inference output exceeds limit',502)
                        successful=True
                        return Response(output,media_type='application/json',headers={'Cache-Control':'no-store'})
        except (httpx.HTTPError,TimeoutError):
            raise ServiceError('UNKNOWN_BACKEND_ACTIVITY','Inference transport failed; admission quarantined',503) from None
        finally:
            # A crashed process may not reach this write. Its expired DISPATCHING
            # record becomes UNKNOWN on the next admission/status inspection.
            self.finish(token,confirmed,successful)
