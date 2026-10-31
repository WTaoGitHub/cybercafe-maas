"""A separately hashed workload may accompany the fixed ordinary profile."""
from .snapshot import read_private,digest


def prepared_fixtures(config,ordinary):
    workload=ordinary
    if config.get('workload_fixture_file'):
        workload=read_private(config['workload_fixture_file'])
        if digest(workload)!=config.get('workload_sha256'):raise ValueError('Selected workload changed')
    if config.get('fixture_refresh_file'):
        from .t6_preparation import load_refresh
        receipt,manifests=load_refresh(config,read_private(config['refresh_template_file']),read_private(config['claim_proof_file']))
        choices=[k for k,v in manifests.items() if digest(v)==digest(workload)]
        if len(choices)!=1 or config['expected_text_file']!=receipt[choices[0]]['quality_file']:
            raise ValueError('Actual workload is outside content-preserving refresh receipt')
    fixtures=workload['fixtures']
    if config.get('expected_text_file'):
        quality=read_private(config['expected_text_file'])
        if quality['profile_id']!=config['profile_id'] or quality['input_sha256']!=digest(workload):raise ValueError('Different workload quality reference')
        fixtures=[{**f,'expected_text_sha256':quality['items'][f['id']]['text_sha256']} for f in fixtures]
    return fixtures,{'workload_sha256':digest(workload),'workload_kind':workload.get('workload','ordinary_fixed_40')}
