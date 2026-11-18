"""Private metrics files and transition-only journal alerts (no outbound messages)."""
import argparse,asyncio,json,logging,os,signal,tempfile,threading,time
from pathlib import Path
from .config import Settings
from .auth import AuthConnector
from .service import TaskService
from .observability import snapshot,alerts,prometheus

log=logging.getLogger('maas.observer')


def atomic(path,text):
    fd,name=tempfile.mkstemp(prefix='.',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:
            f.write(text);f.flush();os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def collect(service,output,previous=None):
    output=Path(output);output.mkdir(parents=True,exist_ok=True,mode=0o700)
    try:
        data=snapshot(service);current=alerts(service,data)
        atomic(output/'metrics.prom',prometheus(data))
        ok=True
    except Exception:
        # Never log exceptions/connection strings/credential material.
        current=[{'code':'OBSERVABILITY_UNAVAILABLE','severity':'critical','message':'Database or artifact metrics unavailable; inspect private services'}]
        ok=False
        # Remove stale scrape data. status.json remains the authoritative freshness check.
        (output/'metrics.prom').unlink(missing_ok=True)
    codes={x['code'] for x in current}
    for item in current:
        if previous is None or item['code'] not in previous:
            log.warning('alert_open code=%s severity=%s',item['code'],item['severity'])
    for code in (previous or set())-codes:log.info('alert_resolved code=%s',code)
    atomic(output/'status.json',json.dumps({'generated_at_unix':time.time(),'ok':ok,'alerts':current},ensure_ascii=False)+'\n')
    return codes


def capacity_loop(settings, db, path, output, stop):
    from .capacity_observe import observe, status

    def report():
        try:
            payload = status(db)
        except Exception:
            payload = {'mode': 'observe', 'state': 'unavailable'}
        atomic(output, json.dumps(payload) + '\n')

    try:
        db.migrate()
        asyncio.run(observe(settings, path, stop, report))
    except Exception:
        log.error('capacity_observer_unavailable')
        atomic(output, '{"mode":"observe","state":"unavailable"}\n')


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--loop',action='store_true');p.add_argument('--interval',type=int,default=60);a=p.parse_args()
    if a.interval<5:p.error('interval must be at least 5 seconds')
    logging.basicConfig(level=logging.INFO)
    s=Settings.from_env();service=TaskService(s,AuthConnector(s));stop=threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:stop.set())
    capacity = None
    path = os.getenv('MAAS_CAPACITY_POLICY_FILE')
    if a.loop and path:
        output = Path(a.output)
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        capacity = threading.Thread(target=capacity_loop,
                                    args=(s, service.db, Path(path), output/'capacity.json', stop), daemon=True)
        capacity.start()
    previous=None
    try:
        while not stop.is_set():
            previous=collect(service,a.output,previous)
            if not a.loop:break
            stop.wait(a.interval)
    finally:
        stop.set()
        if capacity:
            capacity.join(timeout=30)


if __name__=='__main__':main()
