"""Integrate synchronized board-power samples over actual HTTP load windows."""
import bisect,collections,csv,json,statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];BASE=ROOT/'results/ocr-service-v4'
def energy(samples,start,end):
    xs=[r['unix_time'] for r in samples]
    if not xs or start<xs[0] or end>xs[-1]:raise ValueError('Power telemetry does not cover complete test window')
    def value(t,key):
        j=bisect.bisect_right(xs,t);i=max(0,j-1);j=min(j,len(xs)-1)
        if xs[j]==xs[i]:return samples[i][key]
        return samples[i][key]+(samples[j][key]-samples[i][key])*(t-xs[i])/(xs[j]-xs[i])
    ts=[start]+xs[bisect.bisect_right(xs,start):bisect.bisect_left(xs,end)]+[end]
    joules=sum((value(a,'power_w')+value(b,'power_w'))*.5*(b-a) for a,b in zip(ts,ts[1:]))
    util=sum((value(a,'gpu_util_pct')+value(b,'gpu_util_pct'))*.5*(b-a) for a,b in zip(ts,ts[1:]))/(end-start)
    inside=[r for r in samples if start<=r['unix_time']<=end]
    return {'energy_wh':joules/3600,'average_power_w':joules/(end-start),'average_gpu_util_pct':util,'max_memory_mib':max((r['memory_mib'] for r in inside),default=None),'samples':len(inside),'max_sample_gap_s':max((b-a for a,b in zip(ts,ts[1:])),default=0)}
def cost_per1000(energy_wh,success,price):
    """Wh/request * 1000 requests / 1000 Wh per kWh * price."""
    return energy_wh/max(success,1)*price

def main():
    assumptions=json.loads((BASE/'cost-assumptions.json').read_text());price=assumptions['electricity_price_per_kwh'];hours=assumptions['available_hours_per_node_per_day'];offsets=collections.defaultdict(list)
    for path in BASE.glob('clocks-r*.json'):
        for row in json.loads(path.read_text()):offsets[row['node']].append(row['worker_minus_controller_s'])
    telemetry={};audit={}
    for node in ['001','002']:
        rows=[json.loads(x) for x in (BASE/f'telemetry-{node}.jsonl').read_text().splitlines()];good=[r for r in rows if 'power_w' in r]
        uuid=good[0]['gpu_uuid'];offset=statistics.median(offsets.get(uuid,[0]))
        for r in good:r['unix_time']-=offset
        telemetry[uuid]=sorted(good,key=lambda r:r['unix_time']);audit[node]={'rows':len(rows),'valid_rows':len(good),'clock_offset_subtracted_s':offset,'uuid':uuid}
    phases=[];idle={}
    for f in (BASE/'load-results').glob('idle-r*.json'):
        meta=json.loads(f.read_text());idle[meta['replicas']]={uuid:energy(rs,meta['start_unix'],meta['end_unix']) for uuid,rs in telemetry.items()}
    for path in sorted((BASE/'load-results').glob('*.json')):
        data=json.loads(path.read_text())
        if 'summary' not in data:continue
        s=dict(data['summary']);s['source_file']=path.name;start=s['start_unix'];end=s['end_unix'];power={uuid:energy(rs,start,end) for uuid,rs in telemetry.items()};s['gpu_power']=power
        total_wh=sum(r['energy_wh'] for r in power.values());active_wh=sum(power[uuid]['energy_wh'] for uuid in s['nodes']);baseline_w=sum(r['average_power_w'] for r in idle[s['replicas']].values());incremental_wh=max(0,total_wh-baseline_w*(end-start)/3600)
        s.update(platform_gpu_average_w=total_wh*3600/(end-start),gpu_energy_wh=total_wh,active_gpu_energy_wh=active_wh,gpu_cost_cny_per_1000=cost_per1000(total_wh,s['success'],price),active_gpu_cost_cny_per_1000=cost_per1000(active_wh,s['success'],price),incremental_gpu_cost_cny_per_1000=cost_per1000(incremental_wh,s['success'],price),daily_images_at_8h_full_load=s['images_per_second']*hours*3600,daily_gpu_cost_cny_at_8h_full_load=total_wh*3600/(end-start)*hours/1000*price,source_counts=dict(collections.Counter(r['source'] for r in data['requests'])))
        groups=collections.defaultdict(set)
        for r in data['requests']:
            if r['ok']:groups[(r['variant'],r['id'])].add(r['text_sha256'])
        s['within_phase_text_inconsistent_images']=sum(len(values)>1 for values in groups.values());phases.append(s)
    best=[]
    groups=collections.defaultdict(list)
    for s in phases:
        if s['phase']=='matrix':groups[(s['replicas'],s['variant'],s['source'])].append(s)
    for key,ss in sorted(groups.items()):
        peak=max(x['images_per_second'] for x in ss);candidate=min((s for s in ss if s['images_per_second']>=peak*.95),key=lambda s:s['concurrency']);best.append(candidate)
    result={'assumptions':assumptions,'telemetry_audit':audit,'idle':idle,'phases':phases,'best_matrix_within_95pct_of_max':best,'sustained':[s for s in phases if s['phase']=='sustain'],'cost_scope':'Gross power of BOTH GPU boards, including idle controller GPU in one-worker tests; active-worker and incremental figures provided separately. No CPU/wall power.'}
    (BASE/'service-summary.json').write_text(json.dumps(result,indent=2)+'\n')
    columns=['phase','replicas','variant','source','concurrency','requests','success','seconds','images_per_second','client_p50_ms','client_p95_ms','processing_p50_ms','queue_p95_ms','platform_gpu_average_w','gpu_cost_cny_per_1000','daily_images_at_8h_full_load','daily_gpu_cost_cny_at_8h_full_load','within_phase_text_inconsistent_images']
    with (BASE/'service-phases.csv').open('w',encoding='utf-8-sig',newline='') as f:
        out=csv.DictWriter(f,fieldnames=columns,extrasaction='ignore');out.writeheader();out.writerows(phases)
    print('Matrix best within95% max: replicas variant source concurrency images/s p95 GPU-CNY/1000')
    for s in best:print(s['replicas'],s['variant'],s['source'],s['concurrency'],round(s['images_per_second'],3),s['client_p95_ms'],round(s['gpu_cost_cny_per_1000'],5))
    print('Sustained:',len(result['sustained']),'Total measured HTTP requests',sum(s['requests'] for s in phases))
if __name__=='__main__':main()
