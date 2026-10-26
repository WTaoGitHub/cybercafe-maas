"""Import the pilot's public OCR image; run only on the independent new clone.

No credentials, registry configuration, Docker/VM restart or existing image
removal. Compare archive checksum and imported full image ID before deployment.
"""
import argparse,hashlib,json,os,socket,subprocess,urllib.request
from pathlib import Path


def main():
    p=argparse.ArgumentParser();p.add_argument('--sha256',required=True);a=p.parse_args()
    if socket.gethostname()!='XZ-31-002':raise RuntimeError('Pilot clone hostname mismatch')
    # The public endpoint/boot identity is independently checked by the operator.
    root=Path('/root/wtaobj/e5-ocr-staging');root.mkdir(mode=0o700,exist_ok=True)
    report=root/'transfer.json'
    if report.exists():raise RuntimeError('Transfer already recorded; inspect before retrying')
    archive=root/'ocr-image.tar.gz';digest=hashlib.sha256();size=0
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://10.11.31.1:18841/ocr-image.tar.gz',timeout=120) as source,archive.open('wb') as dest:
        while chunk:=source.read(1024*1024):dest.write(chunk);digest.update(chunk);size+=len(chunk)
        dest.flush();os.fsync(dest.fileno())
    assert digest.hexdigest()==a.sha256,'Image transfer checksum mismatch'
    subprocess.run(['docker','load','-i',str(archive)],check=True,timeout=900,stdout=subprocess.DEVNULL)
    image='quay.io/cybercafe-maas-ocr:p2-r1'
    actual=subprocess.check_output(['docker','image','inspect','--format','{{.Id}}',image],text=True).strip()
    assert actual=='sha256:8eed873554282ec02ba4d8d3a78f8230178cef2ee4f6f94ca6dfb858336e89e5'
    value={'archive_sha256':digest.hexdigest(),'archive_bytes':size,'image':image,'image_id':actual,'verified':True}
    report.write_text(json.dumps(value,indent=2)+'\n');report.chmod(0o600)
    archive.unlink()
    print(json.dumps(value))


if __name__=='__main__':main()
