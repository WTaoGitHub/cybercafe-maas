"""Final SQL cadence and accounting check; never upgrades a running/failed load."""
import argparse,json,re,subprocess,time
from pathlib import Path


def main():
 p=argparse.ArgumentParser();p.add_argument('run_id');a=p.parse_args()
 if not re.fullmatch(r'[A-Za-z0-9-]+',a.run_id):raise ValueError('Invalid run ID')
 folder=Path('/root/wtaobj/maas-live/endurance')/a.run_id;d=json.loads((folder/'status.json').read_text())
 if d['state']=='RUNNING':raise RuntimeError('Run is still active')
 sql="""WITH q AS (
 SELECT id,state,created_at,extract(epoch FROM created_at-lag(created_at) OVER(ORDER BY created_at)) gap
 FROM tasks WHERE idempotency_key LIKE 'endurance-"""+a.run_id+"""-%')
 SELECT json_build_object('tasks',count(*),'succeeded',count(*) FILTER(WHERE state='SUCCEEDED'),
 'span_seconds',coalesce(extract(epoch FROM max(created_at)-min(created_at)),0),'max_gap_seconds',coalesce(max(gap),0),
 'usage_events',(SELECT count(*) FROM task_usage_events u JOIN q ON q.id=u.task_id)) FROM q"""
 raw=subprocess.check_output(['docker','exec','maas-tasks-db-1','psql','-h','127.0.0.1','-p','18835','-U','maas','-d','maas','-tAc',sql],text=True,stderr=subprocess.PIPE,timeout=30)
 q=json.loads(raw);passed=(d['state']=='COMPLETE' and d['elapsed_seconds']>=d['duration_target_seconds'] and not d['errors']
  and q['tasks']==q['succeeded']==q['usage_events']==d['accepted']==d['succeeded']
  and q['tasks']>=d['fixtures'] and q['span_seconds']>=d['duration_target_seconds']-max(10,d['interval_seconds']*3)
  and q['max_gap_seconds']<=max(60,d['interval_seconds']*3))
 result={'run_id':a.run_id,'passed':passed,'target_seconds':d['duration_target_seconds'],'cadence':q,
   'max_allowed_gap_seconds':max(60,d['interval_seconds']*3),'checked_at':time.time()}
 (folder/'verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
 if not passed:raise SystemExit(1)
if __name__=='__main__':main()
