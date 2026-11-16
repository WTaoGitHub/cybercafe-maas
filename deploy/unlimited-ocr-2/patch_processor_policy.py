"""Restrict the pinned pilot wheel to its built-in OCR processor."""
import hashlib
import importlib.util
from pathlib import Path


def patch(root):
    processor = root / 'srt/sampling/custom_logit_processor.py'
    tokenizer = root / 'srt/managers/tokenizer_manager.py'
    expected = {
        processor: '4081c08d62bc917c1704febf3ee2ef71f3e092c288a348b08364ccccc2dfa665',
        tokenizer: '952fdfcf54b13af6851993d4b7599d4b1efeb6aa630a7554584a1a047eda94f2',
    }
    for path, digest in expected.items():
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f'unexpected source: {path.name}')

    source = processor.read_text()
    start = source.index('@lru_cache(maxsize=None)\ndef _cache_from_str(')
    end = source.index('\n\nclass CustomLogitProcessor', start)
    source = source[:start] + '''def _cache_from_str(json_str: str):
    # Client input selects a built-in class; it is never unpickled.
    if not isinstance(json_str, str) or len(json_str) > 4096:
        raise ValueError("Only the fixed Unlimited-OCR-2 processor is allowed")
    if json_str == "maas.unlimited-ocr-2.ngram.v1":
        return DeepseekOCRNoRepeatNGramLogitProcessor
    try:
        supplied = orjson.loads(json_str)
    except (ValueError, TypeError):
        raise ValueError("Only the fixed Unlimited-OCR-2 processor is allowed") from None
    expected = orjson.loads(DeepseekOCRNoRepeatNGramLogitProcessor.to_str())
    if supplied != expected:
        raise ValueError("Only the fixed Unlimited-OCR-2 processor is allowed")
    return DeepseekOCRNoRepeatNGramLogitProcessor
''' + source[end:]
    processor.write_text(source)

    source = tokenizer.read_text()
    before = 'from sglang.srt.sampling.custom_logit_processor import supports_sampling_mask'
    after = 'from sglang.srt.sampling.custom_logit_processor import CustomLogitProcessor, supports_sampling_mask'
    anchor = '            if (\n                obj.return_sampling_mask\n                and obj.custom_logit_processor\n'
    if source.count(before) != 1 or source.count(anchor) != 1:
        raise ValueError('unexpected tokenizer validation layout')
    source = source.replace(before, after).replace(anchor,
        '            if obj.custom_logit_processor is not None:\n'
        '                CustomLogitProcessor.from_str(obj.custom_logit_processor)\n' + anchor)
    tokenizer.write_text(source)
    for path in expected:
        print(path.name, hashlib.sha256(path.read_bytes()).hexdigest())


if __name__ == '__main__':
    spec = importlib.util.find_spec('sglang')
    patch(Path(next(iter(spec.submodule_search_locations))))
