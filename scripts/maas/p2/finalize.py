"""Remove only recorded P2 test resources and export cleanup/production sanity evidence."""
import base64,json,shutil,subprocess,sys,urllib.request
from pathlib import Path
sys.path.insert(0,'/root/wtaobj/maas-framework/scripts/maas/p0')
from common import Resources,admin
root=Path('/root/wtaobj/maas-p2');app=Path('/root/wtaobj/maas-framework')
ids=json.loads((root/'target.json').read_text())
assert ids['name']=='maas-ocr-p2-r1'
def run(*args):return subprocess.check_output(args,text=True)
report={}
report['task_counts']=run('docker','exec','wta-maas-p1-test-db','psql','-U','maas','-d','maas_p2_test','-tAc',
    "SELECT json_build_object('tasks',(SELECT count(*) FROM tasks),'successful_events',(SELECT count(*) FROM task_usage_events),'unknown',(SELECT count(*) FROM backend_activity WHERE state='UNKNOWN'),'resolved',(SELECT count(*) FROM backend_activity WHERE state='RESOLVED'))").strip()
res=Resources();res.created=ids['resources']
report['dedicated_resources_removed']=res.cleanup();report['cleanup_failures']=res.failures
assert report['dedicated_resources_removed']
original=admin('/v2/models/1')
report['original_ocr']={k:original[k] for k in ('id','name','backend','backend_version','replicas')}
assert report['original_ocr']=={'id':1,'name':'rapidocr-gpu','backend':'RapidOCR-custom','backend_version':'0.3','replicas':2}
instances=admin('/v2/models/1/instances')['items'];assert len(instances)==2 and all(x['state']=='running' for x in instances)
report['original_instances']=[{'id':x['id'],'worker':x['worker_name'],'state':x['state']} for x in instances]
image=Path('/root/wtaobj/service-validation/fixtures/upright.png').read_bytes()
body=json.dumps({'image_base64':base64.b64encode(image).decode()}).encode()
x=instances[0]
with urllib.request.urlopen(urllib.request.Request('http://'+x['worker_ip']+':'+str(x['port'])+'/ocr',data=body,headers={'Content-Type':'application/json'}),timeout=30) as r:
    result=json.load(r);assert result['lines'];report['original_direct_smoke']={'status':r.status,'lines':len(result['lines'])}
run('docker','exec','wta-maas-p1-test-db','psql','-U','maas','-d','postgres','-c','DROP DATABASE maas_p2_test')
report['disposable_acceptance_db_removed']=True
archive=root/'ocr-p2-r1.tar.gz'
if archive.exists():archive.unlink()
shutil.rmtree(app/'p2-artifacts',ignore_errors=True)
report['archive_and_temporary_artifacts_removed']=True
report['remaining_p2_containers']=run('docker','ps','-a','--filter','name=wta-maas-p2','--format','{{.Names}}').splitlines()
assert not report['remaining_p2_containers']
(root/'cleanup.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report))
