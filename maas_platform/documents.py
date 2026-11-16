"""Private temporary rendering with cancellation and a wall-clock budget."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from .errors import ServiceError

MAX_PDF_BYTES = 10 * 1024**2
RENDER_SLOT = threading.BoundedSemaphore(1)


@contextmanager
def _process(data, media, dpi, check, mode):
    if not RENDER_SLOT.acquire(blocking=False):
        raise ServiceError('DOCUMENT_PREPROCESS_CAPACITY', 'Document preprocessing is busy', 429, True)
    try:
        with tempfile.TemporaryDirectory(prefix='maas-document-') as directory:
            root = Path(directory)
            source = root / 'input'
            source.write_bytes(data)
            process = subprocess.Popen([sys.executable, '-m', 'maas_platform.document_render',
                str(os.getpid()), mode, str(source), str(root), media, str(dpi)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + (5 if mode == 'inspect' else 30)
            try:
                while process.poll() is None:
                    check()
                    if time.monotonic() >= deadline:
                        raise ServiceError('DOCUMENT_RESOURCE_LIMIT', 'Document preprocessing exceeded its budget', 413)
                    time.sleep(.05)
                check()
                metadata_path = root / 'metadata.json'
                limit = 20 * 1024**2 if mode == 'parse' else 65536
                if process.returncode != 0 or not metadata_path.exists() or metadata_path.stat().st_size > limit:
                    raise ServiceError('DOCUMENT_RESOURCE_LIMIT', 'Document preprocessing exceeded its budget', 413)
                metadata = json.loads(metadata_path.read_text())
                if 'error' in metadata:
                    code = metadata['error']
                    status = 413 if code in ('PDF_PAGE_LIMIT', 'DOCUMENT_PIXELS', 'DOCUMENT_RENDER_SIZE', 'DOCUMENT_RESOURCE_LIMIT') else 422
                    raise ServiceError(code, 'Document does not match the approved format or processing limits', status)
                yield root, metadata
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
    finally:
        RENDER_SLOT.release()


@contextmanager
def prepare_document(data, media, dpi=150, check=lambda: None, *, inspect_only=False):
    if media not in ('application/pdf', 'image/png', 'image/jpeg'):
        raise ServiceError('MEDIA_TYPE', 'Document OCR requires PDF, PNG or JPEG', 415)
    if not data or len(data) > MAX_PDF_BYTES:
        raise ServiceError('INPUT_SIZE', 'Document must be between 1 byte and 10 MiB', 413)
    with _process(data, media, dpi, check, 'inspect' if inspect_only else 'render') as result:
        yield result


def parse_document(data, check):
    with _process(data, 'application/json', 150, check, 'parse') as (_, metadata):
        return metadata


def validate_pdf(data):
    with prepare_document(data, 'application/pdf', inspect_only=True):
        pass
