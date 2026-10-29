"""Temporary bounded synchronous side traffic for shared-slot experiments."""
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import threading
import time
import uuid
import httpx
from .snapshot import read_secret


class SyncTraffic:
    def __init__(self,config,observer,on_error,emit):
        self.config=config;self.observer=observer;self.on_error=on_error;self.emit=emit
        self.halt=threading.Event();self.lock=threading.Lock();self.records=[];self.unsent=[];self.failure=None
        self.rate=float(config['sync_rate']);self.clients=16
        if not 0<self.rate<=200:raise ValueError('Bounded synchronous offered rate required')
        manifest_file=Path(config['raw_manifest_file']);manifest=json.loads(manifest_file.read_text())
        quality=json.loads(Path(config['expected_text_file']).read_text())['items'];self.inputs=[]
        for item in manifest:
            raw=(manifest_file.parent/item['file']).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=item['sha256']:raise ValueError('Sync image changed')
            self.inputs.append({'id':item['id'],'body':{'model':config['logical_model'],'image_base64':base64.b64encode(raw).decode()},
                'text_sha256':quality[item['id']]['text_sha256']})
        self.thread=threading.Thread(target=self._run,name='sync-side-traffic')

    def _rows(self):
        project,_,model,_=self.observer.scope
        with self.observer.lock:
            return self.observer.connection.execute("""SELECT s.token,s.state,s.outcome,
              (SELECT count(*)::int FROM sync_usage_events u WHERE u.token=s.token) events
              FROM sync_activity s WHERE s.project=%s AND s.model=%s AND NOT s.is_probe""",(project,model)).fetchall()

    def start(self):
        self.before={r['token'] for r in self._rows()};self.started=time.time();self.started_monotonic=time.monotonic();self.thread.start()

    def _fail(self,exc):
        self.failure=exc;self.halt.set();self.on_error(exc)

    def _one(self,client,sequence,scheduled):
        if self.halt.is_set():return
        item=self.inputs[sequence%len(self.inputs)];rid=uuid.uuid4().hex
        began=time.time();monotonic=time.monotonic()
        self.emit({'kind':'sync_intent','request_id':rid,'sequence':sequence,'image':item['id'],'at_epoch':began})
        try:
            r=client.post('/ocr',json=item['body'],headers={'X-Request-ID':rid})
            if r.status_code==200:
                lines=r.json()['lines'];text='\n'.join(line['text'] for line in lines)
                if hashlib.sha256(text.encode()).hexdigest()!=item['text_sha256']:raise RuntimeError('Synchronous OCR text mismatch')
            elif r.status_code==429:
                if r.json().get('error',{}).get('code')!='SYNC_CAPACITY':raise RuntimeError('Unexpected rejection class')
            else:raise RuntimeError('Unexpected synchronous HTTP status '+str(r.status_code))
            record={'request_id':rid,'sequence':sequence,'http_status':r.status_code,'sent_epoch':began,
                'response_request_id':r.headers.get('x-request-id'),
                'finished_epoch':time.time(),'seconds':time.monotonic()-monotonic,'lateness_seconds':monotonic-scheduled}
            with self.lock:self.records.append(record)
            self.emit({'kind':'sync_response',**record})
            return record
        except Exception as exc:self._fail(exc);raise

    def _run(self):
        pending=set();sequence=0;start=time.monotonic()
        try:
            with httpx.Client(base_url=self.config['base_url'],headers={'Authorization':'Bearer '+read_secret(self.config['customer_key_file'])},
                timeout=35,trust_env=False,follow_redirects=False,transport=httpx.HTTPTransport(retries=0,verify=self.config.get('ca_file',True))) as client:
                with ThreadPoolExecutor(max_workers=self.clients) as pool:
                    while not self.halt.is_set():
                        scheduled=start+sequence/self.rate
                        if self.halt.wait(max(0,scheduled-time.monotonic())):break
                        done={f for f in pending if f.done()}
                        for f in done:f.result()
                        pending-=done
                        if len(pending)>=self.clients or time.monotonic()-scheduled>.1:
                            self.unsent.append(sequence);raise RuntimeError('Synchronous generator budget/arrival missed')
                        if sequence>=100000:raise RuntimeError('Synchronous request budget exhausted')
                        pending.add(pool.submit(self._one,client,sequence,scheduled));sequence+=1
                    for f in pending:f.result()
        except Exception as exc:self._fail(exc)

    def stop(self):
        self.halt.set()
        if self.thread.ident is not None:self.thread.join(timeout=40)
        if self.thread.is_alive():raise RuntimeError('Synchronous requests still in flight')
        if self.failure:raise self.failure
        rows=[r for r in self._rows() if r['token'] not in self.before]
        successes=[r for r in self.records if r['http_status']==200]
        if len(rows)!=len(successes) or any(r['state']!='COMPLETED' or r['outcome']!='SUCCEEDED' or r['events']!=1 for r in rows):
            raise RuntimeError('Synchronous task/usage window mismatch')
        return {'offered_rate':self.rate,'http_clients_limit':self.clients,'unsent':self.unsent,
            'successes':len(successes),'capacity_rejections':sum(r['http_status']==429 for r in self.records),
            'records':self.records,'usage_events':len(rows),'scope':'exclusive project/model token difference; probes excluded'}
