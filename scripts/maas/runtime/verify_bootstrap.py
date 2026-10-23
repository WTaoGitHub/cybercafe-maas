"""Verify the deployed bootstrap API locally; emit only public metadata."""
import base64,hashlib,json,socket,ssl,subprocess,time,urllib.request,urllib.error
from pathlib import Path
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
ROOT=Path('/root/wtaobj/maas-bootstrap')

def main():
    code=(ROOT/'operator/shared-code').read_text().strip()
    certificate=(ROOT/'operator/server.crt').read_text()
    ctx=ssl.create_default_context(cadata=certificate)
    base='https://10.11.31.1:18840'
    def req(path,token=None,context=ctx):
        h={'Authorization':'Bearer '+token} if token else {}
        try:
            with urllib.request.urlopen(urllib.request.Request(base+path,headers=h),context=context,timeout=5) as r:
                return r.status,json.load(r),r.headers
        except urllib.error.HTTPError as e:return e.code,json.load(e),e.headers
    status,payload,headers=req('/bootstrap/v1/config?image_id=tower-e5-pilot-v1',code)
    assert status==200 and headers['Cache-Control']=='no-store' and payload['registration']['cluster_id']==3
    manifest=payload['manifest'];public=serialization.load_pem_public_key((ROOT/'operator/release-signing.pub').read_bytes())
    public.verify(base64.b64decode(payload['manifest_signature']['value']),json.dumps(manifest,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode())
    cert=x509.load_pem_x509_certificate(certificate.encode())
    report={'checked_at':time.time(),'url':base,'health_status':req('/healthz')[0],
            'authenticated_read':status,'unauthenticated_status':req('/bootstrap/v1/config')[0],
            'wrong_code_status':req('/bootstrap/v1/config?image_id=tower-e5-pilot-v1','invalid-code-'+('z'*32))[0],
            'incompatible_image_status':req('/bootstrap/v1/config?image_id=unknown',code)[0],
            'certificate_not_after':cert.not_valid_after.isoformat()+'Z',
            'certificate_sha256':cert.fingerprint(hashes.SHA256()).hex(),'manifest_signature_verified':True,
            'revision':manifest['config_revision'],'test_cluster_id':3,'code_id':'e5-pilot-v1',
            'native_registration_path_verified':False,'cloud_backup':manifest['cloud_backup'],'protected':{}}
    try:req('/healthz',context=ssl.create_default_context())
    except urllib.error.URLError as e:report['untrusted_certificate_rejected']=isinstance(e.reason,ssl.SSLCertVerificationError)
    try:
        with socket.create_connection(('10.11.31.1',18840),timeout=3) as s:
            with ctx.wrap_socket(s,server_hostname='127.0.0.1'):pass
    except ssl.SSLCertVerificationError:report['wrong_hostname_rejected']=True
    for name in ('wta-gpustack-server','maas-tasks-api-1','maas-tasks-executor-1','maas-tasks-observer-1','maas-tasks-db-1'):
        c=json.loads(subprocess.check_output(['docker','inspect',name],text=True))[0]
        report['protected'][name]={'id':c['Id'],'running':c['State']['Running'],'started_at':c['State']['StartedAt']}
    report['allowed_cidrs']=json.loads((ROOT/'private/policy.json').read_text())['allowed_cidrs']
    report['service_active']=subprocess.check_output(['systemctl','is-active','maas-bootstrap-config.service'],text=True).strip()
    report['boundary_active']=subprocess.check_output(['systemctl','is-active','maas-bootstrap-boundary.service'],text=True).strip()
    report['listening']=subprocess.check_output(['ss','-H','-ltn','sport = :18840'],text=True).strip()
    assert report['health_status']==200 and report['unauthenticated_status']==report['wrong_code_status']==401
    assert report['incompatible_image_status']==409 and report.get('untrusted_certificate_rejected') and report.get('wrong_hostname_rejected')
    assert report['certificate_not_after']=='9999-12-31T23:59:59Z'
    assert all(p['running'] for p in report['protected'].values())
    assert report['service_active']==report['boundary_active']=='active'
    report['ok']=True
    (ROOT/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))
if __name__=='__main__':main()
