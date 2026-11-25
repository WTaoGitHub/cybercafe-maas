"""Private host-side maintenance/inspection; no public admin endpoint in P1."""
import argparse,json
from .auth import AuthConnector
from .config import Settings
from .service import TaskService
from .maintenance import Maintenance
from .recovery import probe_registered_models

def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['migrate','pause','resume','status','drain-status','cleanup','shutdown-events-cleanup','resolve-backend','resolve-sync','isolate-backend','isolate-sync','capacity-enforce-isolated','capacity-enforce-isolated-zero','metrics','alerts','capacity-enforce','capacity-static']);p.add_argument('--reason',default='');p.add_argument('--token');p.add_argument('--gpu-uuid');p.add_argument('--credential-id');p.add_argument('--probe-image');a=p.parse_args()
    s=Settings.from_env()
    if a.command == 'shutdown-events-cleanup':
        from .db import Database
        from .shutdown_events import cleanup
        try:
            db = Database(s.database_url)
            db.migrate()
            deleted = cleanup(db)
        except Exception:
            print('{"error":"SHUTDOWN_EVENT_CLEANUP_UNAVAILABLE"}')
            raise SystemExit(1) from None
        print(json.dumps({'deleted_events':deleted}));return
    v=TaskService(s,AuthConnector(s));v.db.migrate()
    if a.command in ('capacity-enforce-isolated','capacity-enforce-isolated-zero'):
        print(json.dumps({'global':v.activate_isolated_capacity(at_zero=a.command.endswith('-zero'))}));return
    if a.command in ('capacity-enforce', 'capacity-static'):
        print(json.dumps({'capacity_mode': v.set_capacity_mode(a.command=='capacity-enforce')}));return
    if a.command in ('metrics','alerts'):
        from .observability import snapshot,alerts,prometheus
        data=snapshot(v)
        if a.command=='metrics':print(prometheus(data),end='')
        else:print(json.dumps({'alerts':alerts(v,data),'usage':{'sync':data['sync_usage'],'tasks':data['task_usage']}},default=str))
        return
    if a.command in ('isolate-backend','isolate-sync'):
        from .quarantine import isolate_unknown
        if not a.token or not a.gpu_uuid:p.error('--token and --gpu-uuid are required')
        kind = 'task' if a.command=='isolate-backend' else 'sync'
        print(json.dumps({'isolated':isolate_unknown(v.db,kind,a.token,a.gpu_uuid,a.reason)}));return
    if a.command in ('resolve-backend','resolve-sync'):
        if not a.token:p.error('--token is required')
        resolver=v.resolve_backend if a.command=='resolve-backend' else Maintenance(v).resolve_sync
        print(json.dumps({'resolved':resolver(a.token,a.reason)}));return
    if a.command=='pause':v.pause(True,a.reason)
    if a.command=='resume':
        probes=[]
        def probe():probes.extend(probe_registered_models(v,a.credential_id,a.probe_image))
        print(json.dumps({'maintenance':Maintenance(v).resume(probe),'probes':probes}));return
    if a.command in ('pause','drain-status'):
        print(json.dumps(Maintenance(v).status()));return
    if a.command=='cleanup':print(json.dumps({'expired_artifacts':v.cleanup(),'orphan_files':v.cleanup_orphans()}))
    else:print(json.dumps(v.metrics()))
if __name__=='__main__':main()
