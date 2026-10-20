"""Private host-side maintenance/inspection; no public admin endpoint in P1."""
import argparse,json
from .auth import AuthConnector
from .config import Settings
from .service import TaskService

def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['migrate','pause','resume','status','cleanup','resolve-backend']);p.add_argument('--reason',default='');p.add_argument('--token');a=p.parse_args()
    s=Settings.from_env();v=TaskService(s,AuthConnector(s));v.db.migrate()
    if a.command=='resolve-backend':
        if not a.token:p.error('--token is required')
        print(json.dumps({'resolved':v.resolve_backend(a.token,a.reason)}));return
    if a.command in ('pause','resume'):v.pause(a.command=='pause',a.reason)
    if a.command=='cleanup':print(json.dumps({'expired_artifacts':v.cleanup()}))
    else:print(json.dumps(v.metrics()))
if __name__=='__main__':main()
