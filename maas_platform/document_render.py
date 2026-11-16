"""Bounded PDF/image inspection and rendering, run only in a disposable process."""
import ctypes
import json
import math
import os
from pathlib import Path
import resource
import signal
import sys

MAX_PAGES = 10
MAX_PAGE_PIXELS = 10_000_000
MAX_TOTAL_PIXELS = 50_000_000
MAX_RENDER_BYTES = 64 * 1024**2


class DocumentError(Exception):
    pass


def isolate(parent):
    os.umask(0o077)
    if sys.platform == 'linux':
        if ctypes.CDLL(None).prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
            raise DocumentError('DOCUMENT_RESOURCE_LIMIT')
        if os.getppid() != parent:
            raise DocumentError('DOCUMENT_RESOURCE_LIMIT')
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))
    resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_RENDER_BYTES, MAX_RENDER_BYTES))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))


def pdf_pages(path, output, dpi, inspect_only):
    import pypdfium2 as pdfium
    from pypdfium2 import raw
    if not path.read_bytes()[:8].startswith(b'%PDF-'):
        raise DocumentError('PDF_INVALID')
    try:
        document = pdfium.PdfDocument(path)
    except pdfium.PdfiumError:
        code = 'PDF_ENCRYPTED' if raw.FPDF_GetLastError() == raw.FPDF_ERR_PASSWORD else 'PDF_INVALID'
        raise DocumentError(code) from None
    try:
        if raw.FPDF_GetSecurityHandlerRevision(document) >= 0:
            raise DocumentError('PDF_ENCRYPTED')
        if not raw.FPDF_DocumentHasValidCrossReferenceTable(document):
            raise DocumentError('PDF_INVALID')
        if not 1 <= len(document) <= MAX_PAGES:
            raise DocumentError('PDF_PAGE_LIMIT')
        sizes = [document.get_page_size(i) for i in range(len(document))]
        if any(not all(math.isfinite(n) and n > 0 for n in size) for size in sizes):
            raise DocumentError('PDF_INVALID')
        if inspect_only:
            return {'page_count': len(document)}
        dimensions = [(math.ceil(w * dpi / 72), math.ceil(h * dpi / 72)) for w, h in sizes]
        if any(w * h > MAX_PAGE_PIXELS for w, h in dimensions) or sum(w * h for w, h in dimensions) > MAX_TOTAL_PIXELS:
            raise DocumentError('DOCUMENT_PIXELS')
        pages, total_bytes = [], 0
        for index, (source_width, source_height) in enumerate(sizes):
            page = document[index]
            try:
                bitmap = page.render(scale=dpi / 72, rev_byteorder=True)
                try:
                    image = bitmap.to_pil()
                    try:
                        width, height = image.size
                        if width * height > MAX_PAGE_PIXELS:
                            raise DocumentError('DOCUMENT_PIXELS')
                        filename = f'page-{index + 1:04}.png'
                        image.save(output / filename, format='PNG')
                    finally:
                        image.close()
                finally:
                    bitmap.close()
                total_bytes += (output / filename).stat().st_size
                if total_bytes > MAX_RENDER_BYTES:
                    raise DocumentError('DOCUMENT_RENDER_SIZE')
                pages.append({'number': index + 1, 'width': width, 'height': height,
                    'source_width': source_width, 'source_height': source_height,
                    'source_unit': 'pdf_point', 'rotation_degrees': page.get_rotation(),
                    'media_type': 'image/png', 'filename': filename})
            finally:
                page.close()
        if sum(p['width'] * p['height'] for p in pages) > MAX_TOTAL_PIXELS:
            raise DocumentError('DOCUMENT_PIXELS')
        return {'page_count': len(pages), 'pages': pages}
    finally:
        document.close()


def image_page(path, output, media):
    from PIL import Image, ImageOps
    from .consumer_ocr import validate_image
    data = path.read_bytes()
    validate_image(data, media)
    with Image.open(path) as image:
        source_width, source_height = image.size
        orientation = image.getexif().get(274, 1)
        with ImageOps.exif_transpose(image) as upright:
            with upright.convert('RGB') as normalized:
                width, height = normalized.size
                filename = 'page-0001.png'
                normalized.save(output / filename, format='PNG')
    if (output / filename).stat().st_size > MAX_RENDER_BYTES:
        raise DocumentError('DOCUMENT_RENDER_SIZE')
    return {'page_count': 1, 'pages': [{'number': 1, 'width': width, 'height': height,
        'source_width': source_width, 'source_height': source_height, 'source_unit': 'pixel',
        'source_exif_orientation': orientation, 'orientation_normalized': True,
        'media_type': 'image/png', 'filename': filename}]}


def main():
    parent, mode, source, output, media, dpi = sys.argv[1:]
    output = Path(output)
    try:
        isolate(int(parent))
        if media == 'application/pdf':
            value = pdf_pages(Path(source), output, int(dpi), mode == 'inspect')
        else:
            value = image_page(Path(source), output, media)
    except DocumentError as error:
        value = {'error': str(error)}
    except Exception as error:
        # Native parser diagnostics and document contents never enter API errors.
        from .errors import ServiceError
        value = {'error': error.code if isinstance(error, ServiceError) else 'DOCUMENT_RESOURCE_LIMIT' if isinstance(error, MemoryError) else 'PDF_INVALID'}
    (output / 'metadata.json').write_text(json.dumps(value))


if __name__ == '__main__':
    main()
