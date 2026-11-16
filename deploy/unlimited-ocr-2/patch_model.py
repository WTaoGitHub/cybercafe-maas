from pathlib import Path
import hashlib
import importlib.util

spec = importlib.util.find_spec('sglang')
path = Path(next(iter(spec.submodule_search_locations))) / 'srt/models/unlimited_ocr_v2.py'
assert hashlib.sha256(path.read_bytes()).hexdigest() == '18b6edfdb97ac30ee93f06318ea4c7e450d3820d1c282f7cd765d1c7ec1e89ab'
s = path.read_text()
a = 'import logging\n'
before = '        # Weight loading runs later, so replacing the base tower only discards\n        # uninitialized parameters.\n'
assert s.count(a) == 1 and s.count(before) == 1
s = s.replace(a, 'import gc\n' + a).replace(before, before + '        del self.model\n        gc.collect()\n')
path.write_text(s)
print('patched_model_sha256', hashlib.sha256(path.read_bytes()).hexdigest())
