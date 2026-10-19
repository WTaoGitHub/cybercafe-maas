"""Integrity checks for the fixed evaluation artifact; no model calls."""
import hashlib,json
from pathlib import Path
from PIL import Image
ROOT=Path(__file__).resolve().parent/'datasets';D=ROOT/'ocr-mixed-v1'
rows=[json.loads(x) for x in (D/'manifest.jsonl').read_text().splitlines()]
splits=json.loads((D/'splits.json').read_text()); hashes=set();ids=set();counts={}
for r in rows:
 p=D/r['image']; b=p.read_bytes();assert hashlib.sha256(b).hexdigest()==r['sha256']
 assert r['id'] not in ids and r['sha256'] not in hashes
 ids.add(r['id']);hashes.add(r['sha256']);counts[r['source']]=counts.get(r['source'],0)+1
 with Image.open(p) as im:
  im.verify()
 assert p.stat().st_size<=10*1024*1024 and r['width']*r['height']<=20_000_000
 a=json.loads((D/r['annotation']).read_text());assert a['regions']
 assert (D/r['ground_truth']).read_text().strip()
assert counts=={'doclaynet':100,'rctw17':100}
assert len(splits['calibration'])==40 and len(splits['evaluation'])==160
assert not(set(splits['calibration'])&set(splits['evaluation']))
assert set(splits['calibration']+splits['evaluation'])==ids
# Remove unused candidate pages, retaining the selected original bytes and labels.
selected={r['original_id'] for r in rows if r['source']=='doclaynet'}
for sub in ('images','annotations'):
 for p in (ROOT/'sources/doclaynet'/sub).iterdir():
  if p.stem not in selected:p.unlink()
report={'ok':True,'images':len(rows),'source_counts':counts,'unique_sha256':len(hashes),'calibration':40,'evaluation':160,'limits':'all files <=10MB, decoded image <=20MP','note':'integrity only; reference text not manually audited'}
(D/'validation.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report))
