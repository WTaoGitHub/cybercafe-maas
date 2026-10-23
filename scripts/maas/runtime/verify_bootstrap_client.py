"""Live verified fetch/prepare check only. Never launches workers or retains fetched tokens."""
import json,sys,tempfile,time,os
from pathlib import Path
ROOT=Path('/root/wtaobj/maas-bootstrap')
sys.path.insert(0,str(ROOT/'code'))
from maas_bootstrap.client import LocalSettings,LocalState,atomic_json,prepare

def main():
    with tempfile.TemporaryDirectory(prefix='maas-client-check-',dir='/run') as directory:
        tmp=Path(directory)
        cfg={'schema_version':1,'config_service_url':'https://10.11.31.1:18840',
             'shared_code_file':str(ROOT/'operator/shared-code'),'tls_certificate_file':str(ROOT/'operator/server.crt'),
             'release_public_key_file':str(ROOT/'operator/release-signing.pub'),'release_key_id':'e5-pilot-signing-v1',
             'state_dir':str(tmp/'state'),'worker_data_dir':'/var/lib/maas-bootstrap/gpustack-worker',
             'site_id':'cybercafe-lab','batch_id':'e5-pilot','cluster_id':3,'image_id':'tower-e5-pilot-v1','architecture':'x86_64',
             'allowed_server_urls':['http://10.11.31.1:18080'],
             'cloud_backup':{'url':'http://configuration.cybercafe.phanlink.cn','enabled':False}}
        atomic_json(tmp/'bootstrap.json',cfg);settings=LocalSettings(tmp/'bootstrap.json')
        first=prepare(settings);second=prepare(settings)
        assert first==second and LocalState(cfg['state_dir']).status()['applied'] is None
        report={'checked_at':time.time(),'verified_https_fetch':True,'ed25519_verified':True,
                'same_revision_prepare_idempotent':True,'revision':first['revision'],'cluster_id':first['cluster_id'],
                'generations':len(list((tmp/'state/generations').glob('*'))),'worker_launch_executed':False,'ok':True}
    report['temporary_credentials_removed']=not tmp.exists()
    print(json.dumps(report))
if __name__=='__main__':main()
