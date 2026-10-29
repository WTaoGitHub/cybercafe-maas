"""Bounded replica recreations with TCP-header-only evidence. Never resumes MaaS.
Any failed POST stops; the original failed business request is never replayed.
"""
import argparse,re,base64,fcntl,json,os,socket,struct,subprocess,sys,threading,time,urllib.request,uuid
from pathlib import Path
sys.path[:0]=['/root/wtaobj/maas-framework','/root/wtaobj']
from replica_capacity_matrix import change_model
from benchmark_executors import atomic,command
from maas_platform.cluster_maintenance import model_hash
LIVE=Path('/root/wtaobj/maas-live')


def tcp_metadata(frame,now):
    if len(frame)<54 or frame[12:14]!=b'\x08\x00':return None
    off=14;ihl=(frame[off]&15)*4
    if ihl<20 or len(frame)<off+ihl+20 or frame[off+9]!=6:return None
    start=off+ihl;src,dst=struct.unpack('!HH',frame[start:start+4]);flags=frame[start+13]
    if 10150 not in (src,dst) or not flags&7:return None
    sip=socket.inet_ntoa(frame[off+12:off+16]);dip=socket.inet_ntoa(frame[off+16:off+20])
    if not {'10.11.31.2','10.11.31.3'}.intersection((sip,dip)):return None
    return {'at':now,'src':sip,'dst':dip,'sport':src,'dport':dst,'syn':bool(flags&2),'ack':bool(flags&16),'rst':bool(flags&4),'fin':bool(flags&1)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id',default='recreate-debug-20261029')
    parser.add_argument('--cycles',type=int,choices=range(1,4),default=1)
    args=parser.parse_args()
    if not re.fullmatch(r'[a-z0-9-]{1,60}',args.run_id):parser.error('Bounded alphanumeric run ID required')
    os.umask(0o077)
    from gpustack_admin import api
    folder=LIVE/'e3'/args.run_id;folder.mkdir(mode=0o700,exist_ok=False)
    compose=['docker','compose','--env-file',str(LIVE/'app.env'),'-f','/root/wtaobj/maas-framework/deploy/maas/compose.yaml']
    def managed():return json.loads(command(compose+['exec','-T','api','python','-m','maas_platform.cli','drain-status']))
    m=api('/v2/models/18');assert m['replicas']==4
    original=m;status=managed();assert status['paused'] and status['managed_requests_drained'];generation=status['generation']
    route=json.loads(command(['ip','-j','route','get','10.11.31.3']))[0];interface=route['dev']
    # Open capture before touching replicas; permission/route failure stops without deployment writes.
    raw=socket.socket(socket.AF_PACKET,socket.SOCK_RAW,socket.htons(3));raw.bind((interface,0));raw.settimeout(.2)
    stop=threading.Event();report={'started_at':time.time(),'generation':generation,'requested_cycles':args.cycles,'original_replicas':4,'original_model_sha256':model_hash(m),'phase':'PREPARED','requests':[],'tcp_interface':interface,'payload_recorded':False}
    def save():atomic(folder/'status.json',report)
    def capture():
        count=0
        with (folder/'tcp.jsonl').open('w') as f:
            while not stop.is_set() and count<20000:
                try:frame=raw.recv(96)
                except socket.timeout:continue
                except OSError:break
                value=tcp_metadata(frame,time.time())
                if value:f.write(json.dumps(value)+'\n');f.flush();count+=1
    t=threading.Thread(target=capture)
    key=(LIVE/'secrets/pilot-key').read_text().strip();img=(Path('/root/wtaobj/maas-endurance-fixtures')/json.loads(Path('/root/wtaobj/maas-endurance-fixtures/manifest.json').read_text())[0]['file']).read_bytes()
    payload={'model':m['name'],'stream':False,'messages':[{'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(img).decode()}}]}]}
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def check():
        s=managed()
        if s['generation']!=generation or not s['managed_requests_drained']:raise RuntimeError('Maintenance/UNKNOWN changed; stop')
    def wait(count):
        end=time.monotonic()+120
        while time.monotonic()<end:
            rows=api('/v2/models/18/instances')['items']
            if any(r['state']=='error' for r in rows):raise RuntimeError('Instance error; stop')
            if len(rows)==count and all(r['state']=='running' for r in rows):
                report['ready_at']=time.time();report['instances']=[{k:r.get(k) for k in ['id','worker_id','port','state','updated_at']} for r in rows];save();return
            time.sleep(.5)
        raise RuntimeError('Readiness deadline; stop')
    def infer(label):
        r={'label':label,'request_id':str(uuid.uuid4()),'intent_at':time.time()};report['requests'].append(r);save()
        req=urllib.request.Request('http://127.0.0.1:18080/v1/chat/completions',data=json.dumps(payload).encode(),headers={'Authorization':'Bearer '+key,'Content-Type':'application/json','X-Request-ID':r['request_id']})
        try:
            with opener.open(req,timeout=30) as response:value=json.load(response);r['status']=response.status
            content=json.loads(value['choices'][0]['message']['content']);assert len(content['lines'])==46
            r.update(lines=46,node=content.get('node'),finished_at=time.time());save()
        except Exception as e:
            r.update(error_class=type(e).__name__,http_status=getattr(e,'code',None),failed_at=time.time());save();raise
    t.start()
    try:
        with open('/run/maas-global-maintenance.lock','a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);check();save()
            for cycle in range(args.cycles):
                report['cycle']=cycle+1;save()
                for count in [2,0,4]:
                    report.update(phase='SCALING',target=count,change_at=time.time());save()
                    check();m=change_model(api,m,count,m['worker_selector']);wait(count)
                    if count:report['phase']='FIRST_REQUEST';save();infer('cycle-'+str(cycle+1)+'-immediate-ready-'+str(count))
                for index in range(3):infer('cycle-'+str(cycle+1)+'-steady-new-'+str(index))
            report.update(phase='COMPLETE_NO_FAILURE_REPRODUCED',finished_at=time.time(),original_config_matches=model_hash(api('/v2/models/18'))==model_hash(original));save()
    except Exception as e:
        report.update(phase='FAILED_STOP',error_class=type(e).__name__,http_status=getattr(e,'code',None),finished_at=time.time());save();raise
    finally:
        stop.set();raw.close();t.join(timeout=3)
    print(json.dumps(report))


if __name__=='__main__':main()
