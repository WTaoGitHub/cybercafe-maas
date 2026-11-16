"""Run inside the pinned, patched image; no GPU or model weights required."""
import json
from unittest.mock import patch

import dill
from sglang.srt.sampling.custom_logit_processor import (
    CustomLogitProcessor,
    DeepseekOCRNoRepeatNGramLogitProcessor,
)


def main():
    legacy = DeepseekOCRNoRepeatNGramLogitProcessor.to_str()
    # Any accidental deserialization fails, including a valid legacy request.
    with patch.object(dill, 'loads', side_effect=AssertionError('untrusted loads')):
        for value in ('maas.unlimited-ocr-2.ngram.v1', legacy):
            assert type(CustomLogitProcessor.from_str(value)) is DeepseekOCRNoRepeatNGramLogitProcessor
        for value in ('', '{}', '{', 'x' * 4097, None, [],
                      json.dumps({'callable': dill.dumps(eval).hex()}),
                      json.dumps({**json.loads(legacy), 'extra': True})):
            try:
                CustomLogitProcessor.from_str(value)
            except ValueError:
                pass
            else:
                raise AssertionError('unsupported processor accepted')
    print('processor policy: fixed symbol and legacy accepted; arbitrary payloads rejected without dill.loads')


if __name__ == '__main__':
    main()
