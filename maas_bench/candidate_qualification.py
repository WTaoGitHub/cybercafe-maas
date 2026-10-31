"""Explicit new-tuple resource qualification and workload-specific calibration."""
from .queue_policy import conservative_mu


def planned():
    return [{'point_id':'resource-qualification','workload':'mixed','seconds':300,'phase':'resource','idle_seconds':60}]+[
        {'point_id':kind+'-mu-'+str(n),'workload':kind,'seconds':60,'phase':'mu','idle_seconds':0}
        for kind in ('ordinary','mixed') for n in (1,2,3)]


def reference_rates(rows,parameters):
    qualification=[r for r in rows if r['phase']=='resource']
    if len(qualification)!=1 or not qualification[0].get('resource_qualified'):
        raise ValueError('New tuple resource qualification must pass first')
    return {kind:conservative_mu([r for r in rows if r['phase']=='mu' and r['workload']==kind],parameters)
            for kind in ('ordinary','mixed')}
