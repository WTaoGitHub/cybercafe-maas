"""Reproducible source downloads; no archive code is executed."""
import concurrent.futures as cf
import hashlib, io, json, os, random, struct, subprocess, sys, time, zipfile, zlib
from pathlib import Path
ROOT=Path(__file__).resolve().parent/'datasets'
RAW=ROOT/'raw'; RAW.mkdir(parents=True,exist_ok=True)
CORE='https://codait-cos-dax.s3.us.cloud-object-storage.appdomain.cloud/dax-doclaynet/1.0.0/DocLayNet_core.zip'
EXTRA=CORE.replace('_core.zip','_extra.zip')
RCTW='https://modelscope.cn/datasets/OpenDataLab/RCTW-17/resolve/master/raw/RCTW-17.tar.gz'

def fetch(url,start,end):
    data=subprocess.check_output(['curl','-fsSL','--retry','3','--connect-timeout','15','--max-time','180','--range',f'{start}-{end}',url])
    if len(data)!=end-start+1: raise ValueError(f'invalid range length {len(data)} != {end-start+1}')
    return data

def ranged(url,start,end):
    parts=[(p,min(p+262144-1,end)) for p in range(start,end+1,262144)]
    with cf.ThreadPoolExecutor(max_workers=16) as pool:
        return b''.join(pool.map(lambda p:fetch(url,*p),parts))

class RemoteZip:
    def __init__(self,url,tag):
        self.url=url
        h=subprocess.check_output(['curl','-fsSI',url],text=True)
        self.size=int(next(l.split(':',1)[1] for l in h.splitlines() if l.lower().startswith('content-length:')))
        tail=fetch(url,self.size-65536,self.size-1)
        pos=tail.rfind(b'PK\x05\x06'); e=struct.unpack_from('<4s4H2LH',tail,pos)
        cdsize,cdoffset=e[5:7]
        if cdsize==0xffffffff or cdoffset==0xffffffff:
            loc=struct.unpack_from('<4sLQL',tail,pos-20)
            z=fetch(url,loc[2],loc[2]+55)
            v=struct.unpack('<4sQ2H2L4Q',z)
            cdsize,cdoffset=v[-2:]
        cache=RAW/(tag+'-central-directory.bin')
        if not cache.exists() or cache.stat().st_size!=cdsize:
            cache.write_bytes(ranged(url,cdoffset,cdoffset+cdsize-1))
        cd=cache.read_bytes()
        # Relocate directory to a small in-memory ZIP for standard metadata parsing.
        if cdsize>0xffffffff: raise ValueError('directory too large')
        end=struct.pack('<4s4H2LH',b'PK\x05\x06',0,0, min(65534,e[3]),min(65534,e[4]),len(cd),0,0)
        self.entries={i.filename:i for i in zipfile.ZipFile(io.BytesIO(cd+end)).infolist()}
        print(tag,'indexed',len(self.entries),'members',flush=True)
    def read(self,name):
        i=self.entries[name]
        header=fetch(self.url,i.header_offset,i.header_offset+29)
        v=struct.unpack('<4s5H3L2H',header)
        off=i.header_offset+30+v[-2]+v[-1]
        data=fetch(self.url,off,off+i.compress_size-1)
        out=zlib.decompress(data,-15) if i.compress_type==8 else data
        if len(out)!=i.file_size or zlib.crc32(out)&0xffffffff!=i.CRC:raise ValueError('CRC mismatch '+name)
        return out

def docsample():
    core=RemoteZip(CORE,'doclaynet-core'); extra=RemoteZip(EXTRA,'doclaynet-extra')
    dest=ROOT/'sources'/'doclaynet'; (dest/'images').mkdir(parents=True,exist_ok=True);(dest/'annotations').mkdir(exist_ok=True)
    candidates=sorted(n for n in core.entries if n.endswith('.png') and '/PNG/' in '/'+n)
    random.Random(20261016).shuffle(candidates)
    # Download a larger pool; builder filters by text and language.
    chosen=candidates[:180]
    def one(name):
        stem=Path(name).stem
        ann=next((n for n in extra.entries if n.endswith('/'+stem+'.json')),None)
        if ann is None:return
        p=dest/'images'/(stem+'.png'); a=dest/'annotations'/(stem+'.json')
        if not a.exists():a.write_bytes(extra.read(ann))
        if not p.exists():p.write_bytes(core.read(name))
        return stem
    with cf.ThreadPoolExecutor(max_workers=12) as pool:
        for j,s in enumerate(pool.map(one,chosen)):
            if j%10==0:print('doclaynet downloaded',j+1,flush=True)
    (dest/'source.json').write_text(json.dumps({'core':CORE,'extra':EXTRA,'seed':20261016,'selection':'180 shuffled full pages, original PNG and matching PDF text JSON; not full archive'},indent=2))

def rctwsample():
    import pyarrow.parquet as pq
    metadata = [
        ('rctw-dsdl.zip', 'https://modelscope.cn/datasets/OpenDataLab/RCTW-17/resolve/fb019735755d1bb79c057329ef7cf23fcdefad8e/dsdl/dsdl_OCR_full.zip', '7d98dc7a577ecf79a2aad34f43a802338fd0b35b36984265d2b084248796f11e'),
        ('rctw-media-map.parquet', 'https://hf-mirror.com/datasets/NVEagle/LocateAnything-Data/resolve/main/mappings/media/locany--rctw--train--source_image--v000001/part-00000.parquet', '04d0f023b51576d9f672b5af23204a101791d9a670714f2a1ef10c16dfcd019a')]
    for name,url,expected in metadata:
        target=RAW/name
        if not target.exists():
            subprocess.run(['curl','-fsSL','--retry','3','--max-time','180',url,'-o',str(target)],check=True)
        if hashlib.sha256(target.read_bytes()).hexdigest()!=expected:
            raise ValueError('metadata SHA256 mismatch '+name)
    archive=zipfile.ZipFile(RAW/'rctw-dsdl.zip')
    samples=json.loads(archive.read('dsdl_OCR_full/set-train/train_samples.json'))['samples']
    mappings=pq.read_table(RAW/'rctw-media-map.parquet').to_pylist()
    mappings={r['source_media_name']:r for r in mappings}
    import re
    candidates=[x for x in samples if any(not r['difficult'] and re.search('[\u4e00-\u9fff]',r['text']) for r in x['instances'])
                and x['image']['image_shape'][0]*x['image']['image_shape'][1]<=20_000_000]
    random.Random(20261016).shuffle(candidates)
    chosen=candidates[:100]
    dest=ROOT/'sources/rctw17'
    (dest/'images').mkdir(parents=True,exist_ok=True);(dest/'annotations').mkdir(exist_ok=True)
    def one(sample):
        name=Path(sample['image']['img_path']).name
        m=mappings[name]
        url='https://hf-mirror.com/datasets/NVEagle/LocateAnything-Data/resolve/main/'+m['tar_relpath']
        image=dest/'images'/name
        if not image.exists():
            data=fetch(url,m['payload_offset'],m['payload_offset']+m['payload_length']-1)
            if hashlib.sha256(data).hexdigest()!=m['source_sha256']:raise ValueError('source hash mismatch '+name)
            image.write_bytes(data)
        if hashlib.sha256(image.read_bytes()).hexdigest()!=m['source_sha256']:raise ValueError('cached hash mismatch')
        sample['image_transport']={k:m[k] for k in ['source_relative_path','source_sha256','content_sha256','tar_relpath','payload_offset','payload_length']}
        (dest/'annotations'/(Path(name).stem+'.json')).write_text(json.dumps(sample,ensure_ascii=False,indent=2))
        return name
    with cf.ThreadPoolExecutor(max_workers=12) as pool:
        for j,name in enumerate(pool.map(one,chosen)):
            if j%10==0:print('rctw downloaded',j+1,name,flush=True)
    (dest/'source.json').write_text(json.dumps({'original':'https://rctw.vlrlab.net/dataset',
        'annotations':'https://modelscope.cn/datasets/OpenDataLab/RCTW-17/resolve/master/dsdl/dsdl_OCR_full.zip',
        'images':'https://huggingface.co/datasets/NVEagle/LocateAnything-Data',
        'mirror':'https://hf-mirror.com/datasets/NVEagle/LocateAnything-Data',
        'verification':'all selected original image bytes verified against source_sha256 == content_sha256 in media map',
        'seed':20261016,'license':'unconfirmed; public availability is not a commercial permission grant'},indent=2))

if __name__=='__main__': {'docsample':docsample,'rctwsample':rctwsample}[sys.argv[1]]()
