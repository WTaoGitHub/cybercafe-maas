"""Build reproducible capacity boundary fixtures, not new human OCR gold labels."""
import argparse
import hashlib
import json
from pathlib import Path
from PIL import Image


def build(base,output):
    base=Path(base);output=Path(output);output.mkdir(mode=0o700,exist_ok=False)
    manifest=[json.loads(line) for line in (base/'manifest.jsonl').read_text().splitlines()]
    selected={source:next(r for r in manifest if r['source']==source) for source in ('doclaynet','rctw17')};images={}
    for source,row in selected.items():
        path=base/row['image'];raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=row['sha256']:raise ValueError('Source image checksum changed')
        with Image.open(path) as im:images[source]=im.convert('RGB')
    doc=images['doclaynet'];scene=images['rctw17'];dense=Image.new('RGB',(3000,3000),'white');tall=Image.new('RGB',(1500,6000),'white')
    for x in (0,1500):
        for y in (0,1500):dense.paste(doc.resize((1500,1500)),(x,y))
    for y in (0,1500,3000,4500):tall.paste(doc.resize((1500,1500)),(0,y))
    cases=[('doc-3mp',doc.resize((1732,1732)),'PNG','doclaynet','resize'),
        ('scene-3mp',scene.resize((1500,2000)),'JPEG','rctw17','resize'),
        ('doc-9mp',doc.resize((3000,3000)),'PNG','doclaynet','resize'),
        ('scene-9mp',scene.resize((2598,3464)),'JPEG','rctw17','resize'),
        ('dense-9mp',dense,'PNG','doclaynet','tile2x2'),('long-9mp',tall,'JPEG','doclaynet','tile1x4'),
        ('doc-180',doc.transpose(Image.Transpose.ROTATE_180),'PNG','doclaynet','rotate180'),
        ('scene-90',scene.transpose(Image.Transpose.ROTATE_90),'JPEG','rctw17','rotate90'),
        ('blank-9mp',Image.new('RGB',(3000,3000),'white'),'PNG',None,'blank'),
        ('near-20mp',doc.resize((4000,4999)),'JPEG','doclaynet','resize')]
    rows=[]
    for name,image,fmt,source,transform in cases:
        path=output/(name+('.png' if fmt=='PNG' else '.jpg'));image.save(path,format=fmt,**({'quality':90} if fmt=='JPEG' else {}));path.chmod(0o600)
        raw=path.read_bytes();w,h=image.size
        if w*h>20000000 or len(raw)>10485760:raise ValueError('Generated fixture exceeds existing input envelope')
        rows.append({'id':name,'source':'boundary','file':path.name,'media_type':'image/png' if fmt=='PNG' else 'image/jpeg',
            'width':w,'height':h,'pixels':w*h,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),
            'transform':transform,'source_sha256':selected[source]['sha256'] if source else None,
            'reference_type':'model regression reference to be measured; not manually labeled accuracy gold'})
    path=output/'manifest.json';path.write_text(json.dumps(rows,indent=2));path.chmod(0o600)
    return {'fixture_count':len(rows),'max_pixels':max(r['pixels'] for r in rows),'max_bytes':max(r['bytes'] for r in rows),
        'manifest_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--base',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    print(json.dumps(build(a.base,a.output)))
