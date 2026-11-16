"""Limit the pinned M1 pilot to non-streaming document requests."""
import hashlib
import importlib.util
from pathlib import Path

spec = importlib.util.find_spec('sglang')
root = Path(next(iter(spec.submodule_search_locations)))
paths = {
    root / 'srt/entrypoints/openai/serving_chat.py': 'ab3ebb0032cca4513c88452bcb70581912a58c4c594952e40093882965f25fe4',
    root / 'srt/managers/tokenizer_manager.py': 'e3de17cf2c3924cc5feb15ac7c549883f9c538b053f1b123c7de99878217453f',
}
for path, digest in paths.items():
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError(f'unexpected source: {path.name}')

chat, tokenizer = paths
source = chat.read_text()
anchor = '        if request.n != 1:\n'
if source.count(anchor) != 1:
    raise ValueError('unexpected document handler layout')
source = source.replace(anchor,
    '        if request.stream:\n'
    '            return self.create_error_response("M1 pilot requires stream=false")\n' + anchor)
chat.write_text(source)

source = tokenizer.read_text()
anchor = '            if (\n                obj.custom_logit_processor\n                and not get_exec().features.enable_custom_logit_processor\n'
if source.count(anchor) != 1:
    raise ValueError('unexpected tokenizer validation layout')
source = source.replace(anchor,
    '            if obj.stream:\n'
    '                raise ValueError("M1 pilot requires stream=false")\n' + anchor)
tokenizer.write_text(source)
for path in paths:
    compile(path.read_text(), str(path), 'exec')
    print(path.name, hashlib.sha256(path.read_bytes()).hexdigest())
