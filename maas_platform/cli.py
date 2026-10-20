"""Private host-side maintenance/inspection; no public admin endpoint in P1."""
import argparse,json
from .auth import AuthConnector
from .config import Settings
from .service import TaskService
from .maintenance import Maintenance
from .recovery import probe_registered_models

def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['migrate','pause','resume','status','drain-status','cleanup','resolve-backend','resolve-sync']);p.add_argument('--reason',default='');p.add_argument('--token');p.add_argument('--credential-id');p.add_argument('--probe-image');a=p.parse_args()
    s=Settings.from_env();v=TaskService(s,AuthConnector(s));v.db.migrate()
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
    if a.command=='cleanup':print(json.dumps({'expired_artifacts':v.cleanup()}))
    else:print(json.dumps(v.metrics()))
if __name__=='__main__':main()
