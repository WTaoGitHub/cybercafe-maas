"""LAN read tests from existing nodes, with secrets kept only in SSH/stdin memory.
Does not install worker code, write node credentials or register a worker.
"""
import json,os,subprocess
from pathlib import Path
BASE=['ssh','-F',os.environ.get('MAAS_SSH_CONFIG',str(Path(__file__).resolve().parents[2]/'.maas/ssh/config')),'-o','BatchMode=yes','-o','ConnectTimeout=8']

def remote(alias,code):
    p=subprocess.run(BASE+[alias,'python3 -'],input=code,capture_output=True,text=True,timeout=30)
    if p.returncode:raise RuntimeError('Remote verification failed for configured alias '+alias)
    return json.loads(p.stdout)

def main():
    # Never print this result, put it on disk or use it as shell text/arguments.
    bundle=remote('maas-controller',"from pathlib import Path\nimport json\np=Path('/root/wtaobj/maas-bootstrap/operator')\nprint(json.dumps({'cert':(p/'server.crt').read_text(),'code':(p/'shared-code').read_text().strip()}))\n")
    template='''import json,ssl,urllib.request,urllib.error,socket
bundle=PAYLOAD
ctx=ssl.create_default_context(cadata=bundle['cert'])
base='https://10.11.31.1:18840'
def req(path,code=None):
 h={'Authorization':'Bearer '+code} if code else {}
 try:
  with urllib.request.urlopen(urllib.request.Request(base+path,headers=h),context=ctx,timeout=6) as r:
   d=json.load(r);return r.status,d,r.headers.get('Cache-Control')
 except urllib.error.HTTPError as e:return e.code,{},e.headers.get('Cache-Control')
status,d,cache=req('/bootstrap/v1/config?image_id=tower-e5-pilot-v1',bundle['code'])
r={'health':req('/healthz')[0],'authorized_read':status,'anonymous_read':req('/bootstrap/v1/config')[0],
   'revision':d.get('manifest',{}).get('config_revision'),'cluster_id':d.get('manifest',{}).get('cluster_id'),
   'registration_material_present':bool(d.get('registration',{}).get('token')),'cache_control':cache,'tls_verified':True}
assert r['health']==status==200 and r['anonymous_read']==401 and r['cluster_id']==3
# Native registration endpoint is checked only as TCP reachability. No token is sent.
try:
 with socket.create_connection(('10.11.31.1',18080),timeout=2):r['native_server_tcp_reachable']=True
except OSError:r['native_server_tcp_reachable']=False
print(json.dumps(r))
'''
    result={}
    for host,alias in [('XZ-31-002','maas-legacy-worker'),('tower-mNST8iPdv','maas-template-legacy')]:
        result[host]=remote(alias,template.replace('PAYLOAD',repr(bundle)))
    Path('results/maas-e5-preparation/bootstrap-lan-clients.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
if __name__=='__main__':main()
