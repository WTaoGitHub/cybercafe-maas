"""Decode validated OCR images to the BGR array expected by RapidOCR."""
import io

import cv2
import numpy as np
from PIL import Image


def decode_bgr(raw):
    with Image.open(io.BytesIO(raw)) as image:
        width, height = image.size
        if width * height > 20_000_000:
            raise ValueError('image exceeds 20 megapixels')

        # Plain RGB8 PNG requires no Pillow mode conversion or NumPy channel copy.
        if image.format == 'PNG' and image.mode == 'RGB' and raw[24:26] == b'\x08\x02' and not image.info:
            try:
                array = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
            except cv2.error:
                array = None
            if array is not None and array.shape == (height, width, 3):
                return array

        return np.asarray(image.convert('RGB'))[:, :, ::-1].copy()
