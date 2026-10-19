"""Check completeness, identity, CUDA configuration and coordinate mapping."""
import json,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];BASE=ROOT/'results/ocr-model-v3';DATA=ROOT/'datasets/ocr-mixed-v1'
def main():
    manifest={r['id']:r for r in map(json.loads,(DATA/'manifest.jsonl').read_text().splitlines())};splits=json.loads((DATA/'splits.json').read_text());validation={};total=0
    for variant in ['A_v4_cls_recheck','A_v4_no_cls','M1_v6_small','M2_v6_medium','M3_v5_server']:
        for split,expected in [('calibration',40),('evaluation',160)]:
            folder=BASE/'raw'/variant/split;rows=[json.loads(f.read_text()) for f in folder.glob('*.json') if f.name!='run-info.json']
            assert len(rows)==expected,(variant,split,len(rows));assert {r['id'] for r in rows}==set(splits[split])
            info=json.loads((folder/'run-info.json').read_text());assert all('CUDAExecutionProvider' in s['providers'] for s in info['providers'])
            for r in rows:
                assert r['ok'],r;meta=manifest[r['id']];assert r['image_sha256']==meta['sha256']
                assert r['result']['elapsed_ms']>0
                for line in r['result']['lines']:
                    assert len(line['box'])==4 and isinstance(line['text'],str) and 0<=line['score']<=1
                    for x,y in line['box']:assert math.isfinite(x) and math.isfinite(y) and -1<=x<=meta['width']+1 and -1<=y<=meta['height']+1,(r['id'],x,y,meta['width'],meta['height'])
            validation[variant+'/'+split]={'total':expected,'success':len(rows),'cuda_providers_verified':True,'coordinates_in_bounds':True};total+=len(rows)
    assert total==1000
    output={'new_image_runs':total,'historical_reference_image_runs':200,'checks':validation}
    (BASE/'validation.json').write_text(json.dumps(output,indent=2)+'\n');print('PASS: 1000 new image runs; fixed splits, image hashes, CUDA sessions, scores and coordinate bounds verified.')
if __name__=='__main__':main()
