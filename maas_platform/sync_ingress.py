"""Bounded, non-streaming chat ingress with durable maintenance admission.

Only this ingress is covered. Native GPUStack/Playground/worker ports must be
closed to customers at the network edge before claiming global maintenance.
"""
import asyncio,json,uuid
from threading import BoundedSemaphore
import httpx
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from .capacity_admission import Admission, unavailable
from .errors import ServiceError
from .pp_ocr_execution import ATTEMPT_HEADER, finished
from .quarantine import blocks, unresolved


class SyncIngress:
    def __init__(self,service,client_factory=None):
        self.service=service
        self.client_factory=client_factory or httpx.AsyncClient
        self.input_slots=BoundedSemaphore(getattr(service.settings, 'max_running', 1))

    def begin(self,who,name,runtime,probe=False,expected_model=None):
        with self.service.db.transaction() as c:
            maintenance=c.execute('SELECT * FROM maintenance WHERE id=1 FOR UPDATE').fetchone()
            self.service.expire_sync(c)
            if (maintenance['paused'] and not probe) or (probe and not maintenance['paused']):
                raise ServiceError('MAINTENANCE_PAUSED','Inference admission is paused or probe window is closed',503)
            model = self.service.model(name)
            if expected_model is not None and model != expected_model:
                raise ServiceError('MODEL_REVISION_CHANGED', 'Registered model configuration changed', 409)
            route = model.get('upstream_model') or ''
            revision = maintenance['capacity_policy_hash']
            admission = Admission(c, self.service.settings, revision, include_preparing=False) if revision else None
            pool_id = admission.pool_ids.get(name) if admission else None
            if not blocks(unresolved(c), name, route, pool_id):
                if admission:
                    if not admission.pool(name).get('total') or not admission.fresh(c):
                        raise unavailable()
                    allowed = admission.allows(name, who.project, asynchronous=False)
                else:
                    slots=c.execute("SELECT count(*) total,count(*) FILTER (WHERE project=%s) own FROM inference_slots",(who.project,)).fetchone()
                    allowed = slots['total'] < self.service.settings.max_running and slots['own'] < self.service.settings.max_project_running
                if not allowed:
                    raise ServiceError('SYNC_CAPACITY','Synchronous admission capacity reached',429,True)
                token=uuid.uuid4().hex
                c.execute("""INSERT INTO sync_activity(token,project,model,state,deadline,is_probe,dispatch_route,dispatch_pool_id,execution_guard)
                    VALUES(%s,%s,%s,'DISPATCHING',clock_timestamp()+%s*interval '1 second',%s,%s,%s,%s)""",
                    (token,who.project,name,runtime,probe,route,pool_id,model.get('execution_guard',False)))
                return token
        raise ServiceError('BACKEND_QUARANTINED','Unresolved backend activity blocks this inference scope',503)

    def finish(self,token,confirmed,successful=False,metering=None):
        with self.service.db.transaction() as c:
            c.execute('SELECT id FROM maintenance WHERE id=1 FOR UPDATE')
            row=c.execute("""UPDATE sync_activity SET state=CASE WHEN gpu_finished_at IS NOT NULL THEN 'COMPLETED' ELSE %s END,
                outcome=%s,finished_at=now(),updated_at=now(),
                resolution_reason=CASE WHEN state='UNKNOWN' AND %s THEN 'Transport confirmed completion' ELSE resolution_reason END
                WHERE token=%s AND state IN ('DISPATCHING','UNKNOWN') RETURNING *""",
                ('COMPLETED' if confirmed else 'UNKNOWN','SUCCEEDED' if confirmed and successful else ('REJECTED' if confirmed else 'UNKNOWN'),confirmed,token)).fetchone()
            if row and confirmed and successful and not row['is_probe']:
                c.execute('INSERT INTO sync_usage_events(token,project,model) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',(token,row['project'],row['model']))
                if metering is not None:
                    from .metering import persist
                    persist(c,row['project'],metering)

    async def infer(self,who,body,probe=False,prepare_input=None,map_output=None):
        if not isinstance(body,dict) or not isinstance(body.get('model'),str) or body.get('stream',False) is not False:
            raise ServiceError('SYNC_INPUT','Use a registered logical model and stream=false',422)
        name=body['model'];model=self.service.model(name)
        if model['driver']!='gpustack.ocr':
            raise ServiceError('SYNC_UNSUPPORTED','This synchronous pilot supports the registered OCR connector',422)
        self.service.auth.authorize(who,name,model)
        key=self.service.auth.key_for(who)
        runtime=min(model['max_runtime_s'],120)
        if prepare_input and getattr(self.service.settings, 'capacity_policy_file', None):
            if not self.input_slots.acquire(blocking=False):
                raise ServiceError('SYNC_CAPACITY','Synchronous input preparation is full',429,True)
            try:
                body = await self.prepare(prepare_input)
                token = await run_in_threadpool(self.begin, who, name, runtime, probe, model)
            finally:
                self.input_slots.release()
            prepare_input = None
        else:
            token=await run_in_threadpool(self.begin,who,name,runtime+(10 if prepare_input else 0),probe,model)
        confirmed=True;successful=False;metering=None
        guarded=model.get('execution_guard',False)
        headers={'Authorization':'Bearer '+key,'Accept-Encoding':'identity'}
        if guarded:
            headers[ATTEMPT_HEADER]='sync.'+token
        try:
            if prepare_input:
                body=await self.prepare(prepare_input)
            confirmed=False
            async with asyncio.timeout(runtime):
                async with self.client_factory(trust_env=False,follow_redirects=False,timeout=httpx.Timeout(runtime,connect=min(5,runtime))) as client:
                    async with client.stream('POST',self.service.settings.gpustack_url+'/v1/chat/completions',
                        headers=headers,json={**body,'model':model['upstream_model']}) as response:
                        if response.headers.get('content-encoding','identity')!='identity':
                            raise ServiceError('OUTPUT_ENCODING','Compressed responses are unsupported',502)
                        raw=bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(raw)+len(chunk)>self.service.settings.max_output_bytes:
                                raise ServiceError('OUTPUT_SIZE','Inference output exceeds limit',502)
                            raw.extend(chunk)
                        terminal_status=response.status_code in (200,400,401,403,404,413,415,422)
                        gpu_ended = guarded and await run_in_threadpool(finished,self.service.db,'sync',token)
                        confirmed=terminal_status and (not guarded or gpu_ended)
                        if not confirmed:
                            if gpu_ended:
                                raise ServiceError('BACKEND_RESULT_UNAVAILABLE','GPU finished but its response was unavailable',502)
                            raise ServiceError('UNKNOWN_BACKEND_ACTIVITY','Upstream completion is unconfirmed',503)
                        if response.status_code!=200:
                            raise ServiceError('UPSTREAM_REJECTED','Inference rejected by upstream',response.status_code)
                        try:json.loads(raw)
                        except ValueError:raise ServiceError('OUTPUT_INVALID','Upstream returned invalid JSON',502) from None
                        output=map_output(bytes(raw)) if map_output else bytes(raw)
                        if len(output)>self.service.settings.max_output_bytes:
                            raise ServiceError('OUTPUT_SIZE','Converted inference output exceeds limit',502)
                        from .metering import receipt,tokens_from_response,single_image_ocr
                        if not probe and single_image_ocr(body,json.loads(raw)):
                            metering=receipt('sync',token,name,model['revision'],tokens_from_response(json.loads(raw)))
                        successful=True
                        response_headers={'Cache-Control':'no-store'}
                        if metering:response_headers['X-Usage-ID']=metering['id']
                        return Response(output,media_type='application/json',headers=response_headers)
        except (httpx.HTTPError,TimeoutError):
            if guarded and await run_in_threadpool(finished,self.service.db,'sync',token):
                raise ServiceError('BACKEND_RESULT_UNAVAILABLE','GPU finished but its response was unavailable',502) from None
            raise ServiceError('UNKNOWN_BACKEND_ACTIVITY','Inference transport failed; admission quarantined',503) from None
        finally:
            # A crashed process may not reach this write. Its expired DISPATCHING
            # record becomes UNKNOWN on the next admission/status inspection.
            await run_in_threadpool(self.finish,token,confirmed,successful,metering=metering)

    @staticmethod
    async def prepare(prepare_input):
        try:
            async with asyncio.timeout(10):
                return await prepare_input()
        except TimeoutError:
            raise ServiceError('IMAGE_FETCH_TIMEOUT','Input preparation exceeded its budget before inference',408) from None
