"""Optional one-shot read-only controller evidence, NOT a collector or inference gate.

Only fixed commands and parameterized read-only SQL. No services are changed,
no credentials/ENV are exported, and no queries are executed against GPUStack DB.
"""
import json
from pathlib import Path
import re
import subprocess
from .core import check

SQL_CODE = '''
import json,os,sys,psycopg
p=json.load(sys.stdin)
with psycopg.connect(os.environ['MAAS_DATABASE_URL']) as conn:
 conn.execute('SET TRANSACTION READ ONLY')
 if p['kind']=='task_usage':
  r=conn.execute("SELECT count(*) FROM task_usage_events WHERE task_id=%s",(p['task_id'],)).fetchone()
  print(json.dumps({'success_events':r[0]}))
 elif p['kind']=='sync_usage':
  r=conn.execute("SELECT count(*) FROM sync_usage_events WHERE project=%s",(p['project'],)).fetchone()
  print(json.dumps({'success_events':r[0]}))
 else: raise ValueError('Unsupported read operation')
'''

REMOTE = '''
import json,subprocess,sys,time
from pathlib import Path
p=PAYLOAD
if p['kind'] in ('task_usage','sync_usage'):
 r=subprocess.run(['docker','exec','-i','maas-tasks-api-1','python','-c',SQL],
                  input=json.dumps(p),capture_output=True,text=True,timeout=15)
 if r.returncode: raise RuntimeError('Read-only database evidence unavailable')
 print(r.stdout)
elif p['kind']=='components':
 raw=subprocess.check_output(['docker','ps','--format','{{json .}}'],text=True,timeout=10)
 rows=[json.loads(x) for x in raw.splitlines()]
 print(json.dumps({'containers':[{k:r[k] for k in ('Names','Image','Status')} for r in rows]}))
elif p['kind']=='monitor':
 m=json.loads(Path('/root/wtaobj/maas-live/monitor/status.json').read_text())
 print(json.dumps({'age_s':time.time()-m['generated_at_unix'],'ok':m['ok'],
  'alerts':len(m.get('alerts',[])),
  'unknown_backend':m['metrics']['maas_unknown_backend_activity'],
  'unknown_sync':m['metrics']['maas_unknown_sync_activity']}))
elif p['kind']=='collector_retired':
 r=subprocess.run(['systemctl','show','maas-collector.service','-p','UnitFileState','-p','ActiveState'],
                  capture_output=True,text=True,timeout=10)
 check=dict(line.split('=',1) for line in r.stdout.splitlines() if '=' in line)
 print(json.dumps({'active':check.get('ActiveState'),'unit_state':check.get('UnitFileState')}))
else: raise ValueError('Unsupported read-only operation')
'''


def observe(c, kind, **parameters):
    operator = c.config['operator']
    host = operator['host']
    user = operator.get('user', 'root')
    check(bool(re.fullmatch(r'[a-zA-Z0-9_.:-]+', host)) and not host.startswith('-'), 'Invalid operator host')
    check(bool(re.fullmatch(r'[a-zA-Z0-9_][a-zA-Z0-9_-]*', user)), 'Invalid operator user')
    port = operator.get('port', 2023)
    check(isinstance(port, int) and 1 <= port <= 65535, 'Invalid operator port')
    key = str(Path(operator['key_file']).expanduser())
    payload = {'kind': kind, **parameters}
    # JSON data is inserted as a Python literal; no shell interpolation of config or key contents.
    code = 'SQL = ' + repr(SQL_CODE) + '\n' + REMOTE.replace('PAYLOAD', repr(payload))
    command = ['ssh', '-T', '-p', str(port), '-i', key, '-o', 'BatchMode=yes',
               '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=8',
               user+'@'+host, 'python3 -']
    result = subprocess.run(command, input=code, capture_output=True, text=True, timeout=30)
    check(result.returncode == 0, 'Read-only operator SSH evidence unavailable; no retry')
    try:
        value = json.loads(result.stdout)
    except ValueError:
        raise ValueError('Invalid read-only observer response') from None
    return value
