"""A separately hashed workload may accompany the fixed ordinary profile."""
from .snapshot import read_private,digest


def prepared_fixtures(config,ordinary):
    workload=ordinary
    if config.get('workload_fixture_file'):
        workload=read_private(config['workload_fixture_file'])
        if digest(workload)!=config.get('workload_sha256'):raise ValueError('Selected workload changed')
    fixtures=workload['fixtures']
    if config.get('expected_text_file'):
        quality=read_private(config['expected_text_file'])
        if quality['profile_id']!=config['profile_id'] or quality['input_sha256']!=digest(workload):raise ValueError('Different workload quality reference')
        fixtures=[{**f,'expected_text_sha256':quality['items'][f['id']]['text_sha256']} for f in fixtures]
    return fixtures,{'workload_sha256':digest(workload),'workload_kind':workload.get('workload','ordinary_fixed_40')}
