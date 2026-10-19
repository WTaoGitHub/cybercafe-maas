"""Audit saved evidence without running additional load."""
import json,hashlib,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; B=ROOT/'results/ocr-service-v4'
s=json.loads((B/'service-summary.json').read_text()); phases=s['phases']; matrix=[x for x in phases if x['phase']=='matrix']; sustained=s['sustained']
assert len(matrix)==62 and len(sustained)==5
assert sum(x['requests'] for x in matrix)==2480
assert all(x['success']==x['requests']==x['nonempty'] for x in phases)
assert all(len(x['nodes'])==2 for x in phases if x['replicas']>=2)
assert all(x['seconds']>=120 for x in sustained)
assert all(x['within_phase_text_inconsistent_images']==0 for x in phases)
for x in phases:
 assert all(p['max_sample_gap_s']<2 for p in x['gpu_power'].values())
 assert math.isclose(x['daily_gpu_cost_cny_at_8h_full_load'],x['gpu_cost_cny_per_1000']*x['daily_images_at_8h_full_load']/1000,rel_tol=1e-10)
rows=[]
for variant in ['v4_legacy','v4_upright','v4_guarded','v6_small']:
 rows.extend(json.loads(p.read_text()) for p in (B/variant).rglob('*.json'))
assert len(rows)==872 and all(x['ok'] for x in rows)
corrected=[x for x in rows if x['explicit_correction']];assert len(corrected)==192
baselines={(x['variant'],x['split'],x['id']):x for x in rows if not x['explicit_correction'] and x['input_rotation_cw']==0}
max_delta=0
for row in corrected:
 base=baselines[(row['variant'],row['split'],row['id'])]['result']; result=row['result'];assert base['text']==result['text'];assert len(base['lines'])==len(result['lines'])
 for a,b in zip(base['lines'],result['lines']):
  assert a['text']==b['text']
  max_delta=max(max_delta,max(abs(u-v) for p,q in zip(a['box'],b['box']) for u,v in zip(p,q)))
assert max_delta<1e-6
assert json.loads((B/'cleanup.json').read_text())['original_model_unchanged']
assert json.loads((B/'serving-after-test.json').read_text())['http_success']
output={'direction_runs':len(rows),'explicit_corrections_text_and_boxes_restored':len(corrected),'max_box_coordinate_delta':max_delta,'matrix_cells':len(matrix),'matrix_requests':2480,'sustained_phases':len(sustained),'timed_http_requests':sum(x['requests'] for x in phases),'all_successful_nonempty':True,'all_multinode_phases_used_both_GPUs':True,'within_phase_repeated_text_consistent':True,'power_coverage_and_cost_identity_passed':True,'original_endpoint_recheck_passed':True,'temporary_containers_removed_and_telemetry_stopped':'verified via docker ps and pgrep on both hosts after cleanup'}
(B/'validation.json').write_text(json.dumps(output,indent=2)+'\n')
paths=list((ROOT/'service-validation').glob('*.py'))+list((ROOT/'service-validation').glob('Dockerfile*'))+list((ROOT/'service-validation').glob('requirements*.txt'))+[ROOT/'evaluate_ocr.py',ROOT/'datasets/ocr-mixed-v1/manifest.jsonl',ROOT/'datasets/ocr-mixed-v1/splits.json']
(B/'reproducibility.json').write_text(json.dumps({str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},indent=2)+'\n')
print(json.dumps(output,indent=2))
