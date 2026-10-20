"""Private host-side metrics and actionable alerts; no customer HTTP endpoint."""
import shutil


def snapshot(service):
    result=service.metrics()
    with service.db.transaction() as c:
        storage=c.execute("SELECT coalesce(sum(size),0) bytes,count(*) files FROM artifacts WHERE deleted_at IS NULL").fetchone()
        sync=c.execute("SELECT state,count(*) n FROM sync_activity GROUP BY state").fetchall()
        usage=c.execute("SELECT project,model,sum(quantity) quantity FROM sync_usage_events GROUP BY project,model").fetchall()
        tasks=c.execute("SELECT project,sum(quantity) quantity FROM task_usage_events GROUP BY project").fetchall()
        age=c.execute("SELECT coalesce(max(extract(epoch FROM now()-created_at)),0) age FROM tasks WHERE state IN ('QUEUED','RETRY_WAIT')").fetchone()['age']
        latency=c.execute("SELECT count(*) n,coalesce(sum(extract(epoch FROM finished_at-created_at)),0) seconds FROM sync_activity WHERE finished_at IS NOT NULL AND state='COMPLETED'").fetchone()
    result.update(storage={'live_bytes':int(storage['bytes']),'live_files':storage['files'],
        'free_bytes':shutil.disk_usage(service.store.root).free,'quota_bytes':service.settings.max_storage_bytes},
        sync={r['state']:r['n'] for r in sync},sync_usage=usage,task_usage=tasks,
        oldest_queue_seconds=float(age),sync_completed_seconds=float(latency['seconds']),sync_completed_count=latency['n'])
    return result


def alerts(service, data):
    out=[]
    def add(code,severity,message):out.append({'code':code,'severity':severity,'message':message})
    if data['unknown_backend_activity'] or data['unknown_sync_activity']:
        add('BACKEND_QUARANTINED','critical','Dispatch blocked: inspect backend activity before manual resolution')
    if data['storage']['free_bytes']<service.settings.min_free_bytes+service.settings.max_output_bytes:
        add('DISK_LOW','critical','Free storage cannot safely accommodate the next maximum result')
    if data['storage']['live_bytes']>=service.settings.max_storage_bytes*.8:
        add('STORAGE_QUOTA_NEAR','warning','Live artifact bytes exceed 80% of global quota; run cleanup or increase capacity')
    queued=sum(data['tasks'].get(k,0) for k in ('QUEUED','RETRY_WAIT'))
    if queued>=service.settings.max_queued*.8:add('QUEUE_NEAR_FULL','warning','Queue exceeds 80% of admission limit')
    if data['oldest_queue_seconds']>=300:add('QUEUE_WAIT','warning','Oldest queued task has waited at least 300 seconds')
    if data['dispatch_paused']:add('MAINTENANCE_PAUSED','info','Admission is intentionally paused; verify recovery when maintenance ends')
    return out


def prometheus(data):
    # Fixed low-cardinality aggregate labels only: no project/key/task IDs.
    values={'successful_tasks_total':data['successful_tasks'],
        'successful_sync_requests_total':sum(r['quantity'] for r in data['sync_usage']),
        'dispatch_paused':int(data['dispatch_paused']),
        'unknown_backend_activity':data['unknown_backend_activity'],
        'unknown_sync_activity':data['unknown_sync_activity'],
        'artifact_live_bytes':data['storage']['live_bytes'],
        'artifact_free_bytes':data['storage']['free_bytes'],
        'oldest_queue_seconds':data['oldest_queue_seconds'],
        'sync_completed_duration_seconds_sum':data['sync_completed_seconds'],
        'sync_completed_duration_seconds_count':data['sync_completed_count']}
    lines=[]
    for name,value in values.items():
        kind='counter' if name.endswith('_total') else 'gauge'
        lines.extend([f'# TYPE maas_{name} {kind}',f'maas_{name} {value}'])
    for state,value in sorted(data['tasks'].items()):lines.append(f'maas_tasks{{state="{state}"}} {value}')
    for state,value in sorted(data['sync'].items()):lines.append(f'maas_sync_requests{{state="{state}"}} {value}')
    return '\n'.join(lines)+'\n'
