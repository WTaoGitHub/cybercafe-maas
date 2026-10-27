"""Tiny deterministic PNG fixtures generated without Pillow or external datasets."""
import struct
import zlib


def blank_png(width=64, height=64):
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
    header = struct.pack('!2I5B', width, height, 8, 2, 0, 0, 0)
    pixels = (b'\0' + b'\xff\xff\xff' * width) * height
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b'')


def oversized_pixel_png():
    """Only dimensions differ; service must reject before attempting full pixel decoding."""
    raw = blank_png()
    data = struct.pack('!2I5B', 5000, 5000, 8, 2, 0, 0, 0)
    chunk = b'IHDR' + data
    return raw[:8] + struct.pack('!I', len(data)) + chunk + struct.pack('!I', zlib.crc32(chunk)) + raw[33:]
