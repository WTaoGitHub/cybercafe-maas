import io
from pathlib import Path
import sys

import pytest

np = pytest.importorskip('numpy')
pytest.importorskip('cv2')
Image = pytest.importorskip('PIL.Image')

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'gpu-ocr'))
from image_decode import decode_bgr


@pytest.mark.parametrize('mode,media', [('RGB', 'PNG'), ('L', 'PNG'), ('RGBA', 'PNG'), ('RGB', 'JPEG')])
def test_decode_preserves_bgr_pixels(mode, media):
    sample = Image.fromarray(np.random.default_rng(7).integers(0, 256, (97, 143, 3), dtype=np.uint8)).convert(mode)
    with io.BytesIO() as out:
        sample.save(out, format=media)
        raw = out.getvalue()
    with Image.open(io.BytesIO(raw)) as image:
        expected = np.asarray(image.convert('RGB'))[:, :, ::-1].copy()
    decoded = decode_bgr(raw)
    assert decoded.flags.c_contiguous and decoded.flags.writeable
    np.testing.assert_array_equal(decoded, expected)


def test_dimension_limit_is_checked_before_decoding():
    with io.BytesIO() as out:
        Image.new('1', (5000, 4001)).save(out, format='PNG')
        with pytest.raises(ValueError, match='20 megapixels'):
            decode_bgr(out.getvalue())
