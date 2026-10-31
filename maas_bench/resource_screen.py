"""Bounded diagnostic selection, never a replacement for T5/T6 proof."""
from .contracts import Parameters


def points():
    return [Parameters(n,n,n,n,25,25,2147483648).to_dict() for n in (4,6,8)]


def memory_plateau(records,workers,window_end):
    """Compare successive 30s high-water marks, without claiming true peaks.

    Active buffers may fluctuate. Reject >256MiB upward high-water growth in
    the last minute, rather than requiring a constant instantaneous reading.
    Native gauge freshness and finite image coverage still limit this evidence.
    """
    result=[]
    for i,worker in enumerate(workers):
        rows=[(r['at_monotonic'],w['worker_node_gram_used_bytes']) for r in records
              for w in r['workers'] if w['worker_id']==worker['id']]
        first=[v for t,v in rows if window_end-60<=t<window_end-30]
        last=[v for t,v in rows if window_end-30<=t<=window_end]
        if len(first)<3 or len(last)<3:raise ValueError('Insufficient last-minute memory observations')
        growth=max(last)-max(first);total=worker['gpus'][0]['memory_total']
        result.append({'gpu':i+1,'max_logged_used_bytes':max(v for _,v in rows),
            'last_30s_high_water_bytes':max(last),'prior_30s_high_water_bytes':max(first),
            'high_water_growth_bytes':growth,'plateau_observed':growth<=256*1024**2,
            'total_bytes':total,'minimum_logged_free_bytes':total-max(v for _,v in rows)})
    return result
