"""Fetch official version-pinned ONNX weights/configs and verify LFS SHA256."""
import concurrent.futures,hashlib,json,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parent
REVISIONS={'PP-OCRv6_small_det_onnx':'28fe5895c24fd108c19eb3e8479f4ab385fbfc62','PP-OCRv6_small_rec_onnx':'b8f84f0b80c529de40b4fbb3544b84fa7233a513','PP-OCRv6_medium_det_onnx':'61323801669c338b7891481ec7bac61ce31b576a','PP-OCRv6_medium_rec_onnx':'50c7eacafc52fa7bcf4194e8cd08e46f8558504b','PP-OCRv5_server_det_onnx':'dcf248c4dcc064c6d030db241255f9e8fbc6733a','PP-OCRv5_server_rec_onnx':'b70df217f4fd99d14f970bad092cebe7d74cc4d1'}
def download(task):
    model,rev,item=task;name=item['rfilename'];dest=ROOT/'models'/model/name;dest.parent.mkdir(parents=True,exist_ok=True);expected=item.get('lfs',{}).get('sha256');url=f'https://huggingface.co/PaddlePaddle/{model}/resolve/{rev}/{name}'
    if not dest.exists() or expected and hashlib.sha256(dest.read_bytes()).hexdigest()!=expected:
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url,timeout=60) as src,dest.with_suffix(dest.suffix+'.part').open('wb') as out:
                    while True:
                        chunk=src.read(1024*1024)
                        if not chunk:break
                        out.write(chunk)
                dest.with_suffix(dest.suffix+'.part').replace(dest);break
            except Exception:
                if attempt==2:raise
                time.sleep(2)
    digest=hashlib.sha256(dest.read_bytes()).hexdigest()
    if expected:assert digest==expected,(model,name,'SHA256 mismatch')
    print(model,name,dest.stat().st_size,flush=True)
    return {'model':model,'revision':rev,'file':name,'url':url,'bytes':dest.stat().st_size,'sha256':digest,'expected_lfs_sha256':expected}
def main():
    tasks=[]
    for model,rev in REVISIONS.items():
        with urllib.request.urlopen(f'https://huggingface.co/api/models/PaddlePaddle/{model}/revision/{rev}?blobs=true',timeout=30) as r:metadata=json.load(r)
        (ROOT/'models'/model).mkdir(parents=True,exist_ok=True)
        (ROOT/'models'/model/'hub-metadata.json').write_text(json.dumps(metadata,indent=2)+'\n')
        for item in metadata['siblings']:
            if item['rfilename'] in ['inference.onnx','inference.yml','inference.json','README.md']:tasks.append((model,rev,item))
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(download,tasks))
    (ROOT/'model-provenance.json').write_text(json.dumps(results,indent=2)+'\n')
if __name__=='__main__':main()
