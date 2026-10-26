"""Pilot-only public local_path marker on host and existing worker filesystem.

Weights remain inside the verified OCR image. Do not copy worker credentials or
mount a broad host parent. This manual staging is not automatic model delivery:
recreating the worker container requires staging again until a model-volume
contract is implemented. No worker/VM restart is performed.
"""
import json,subprocess
from pathlib import Path


def main():
    root=Path('/opt/maas-models/ocr-e5-r1/model')
    value={'model':'maas-ocr-e5-r1','weights_policy':'image-contained',
           'image_id':'sha256:8eed873554282ec02ba4d8d3a78f8230178cef2ee4f6f94ca6dfb858336e89e5'}
    root.mkdir(parents=True,mode=0o755,exist_ok=True)
    marker=root/'deployment.json'
    if marker.exists():assert json.loads(marker.read_text())==value
    else:marker.write_text(json.dumps(value)+'\n');marker.chmod(0o644)
    subprocess.run(['docker','exec','maas-gpustack-worker','mkdir','-p',str(root)],check=True)
    subprocess.run(['docker','cp',str(marker),'maas-gpustack-worker:'+str(marker)],check=True)
    print(json.dumps({'host_and_worker_path':str(root),'public_marker_only':True,'worker_recreated':False}))


if __name__=='__main__':main()
