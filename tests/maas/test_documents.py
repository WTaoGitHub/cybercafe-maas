from concurrent.futures import ThreadPoolExecutor
import io
from pathlib import Path
import tempfile
from threading import Event
import pytest
from PIL import Image
from maas_platform.documents import RENDER_SLOT, prepare_document, validate_pdf
from maas_platform.errors import ServiceError

pdfium = pytest.importorskip('pypdfium2', reason='Install requirements-document.txt for PDF tests')


def pdf(pages=1, width=300, height=400):
    output = io.BytesIO()
    document = pdfium.PdfDocument.new()
    try:
        for _ in range(pages):
            document.new_page(width, height).close()
        document.save(output)
    finally:
        document.close()
    return output.getvalue()


def test_pdf_render_and_private_cleanup(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    data = pdf(2)
    validate_pdf(data)
    with prepare_document(data, 'application/pdf') as (root, metadata):
        assert metadata['page_count'] == 2
        assert [p['number'] for p in metadata['pages']] == [1, 2]
        page = metadata['pages'][0]
        assert (page['width'], page['height']) == (625, 834)
        assert page['source_unit'] == 'pdf_point'
        assert root.stat().st_mode & 0o077 == 0
        with Image.open(root / page['filename']) as image:
            assert image.size == (625, 834)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('data,code', [
    (b'not a PDF', 'PDF_INVALID'), (b'%PDF-1.7\ninvalid', 'PDF_INVALID'),
    (pdf(11), 'PDF_PAGE_LIMIT'),
])
def test_invalid_and_oversized_pdf(data, code):
    with pytest.raises(ServiceError) as error:
        validate_pdf(data)
    assert error.value.code == code


def test_password_protected_pdf_rejected():
    # Synthetic blank AES-256 PDF; password is only a fixture, never accepted by the API.
    data = (Path(__file__).parent / 'fixtures/document-encrypted.pdf').read_bytes()
    with pytest.raises(ServiceError) as error:
        validate_pdf(data)
    assert error.value.code == 'PDF_ENCRYPTED'


@pytest.mark.parametrize('data,dpi', [(pdf(width=10000, height=10000), 150),
                                    (pdf(10, 600, 600), 300)])
def test_pixels_checked_before_render(data, dpi):
    with pytest.raises(ServiceError) as error:
        with prepare_document(data, 'application/pdf', dpi):
            pytest.fail('oversized PDF rendered')
    assert error.value.code == 'DOCUMENT_PIXELS'


def test_cancellation_kills_preprocess_and_cleans_files(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    checked = False
    def cancelled():
        nonlocal checked
        if checked:
            raise ServiceError('INTERRUPTED', 'cancelled')
        checked = True
    with pytest.raises(ServiceError) as error:
        with prepare_document(pdf(10), 'application/pdf', check=cancelled):
            pytest.fail('cancelled work completed')
    assert error.value.code == 'INTERRUPTED'
    assert not list(tmp_path.iterdir())


def test_task_waits_for_busy_document_slot_and_upload_still_fails_fast():
    data = pdf()
    waiting = Event()

    def render():
        with prepare_document(data, 'application/pdf', check=waiting.set) as (_, metadata):
            return metadata['page_count']

    with ThreadPoolExecutor(max_workers=1) as executor:
        with RENDER_SLOT:
            future = executor.submit(render)
            assert waiting.wait(2)
            assert not future.done()
            with pytest.raises(ServiceError) as error:
                validate_pdf(data)
            assert error.value.code == 'DOCUMENT_PREPROCESS_CAPACITY'
        assert future.result(timeout=5) == 1


def test_image_orientation_and_media_validation():
    image = Image.new('RGB', (80, 40), 'white')
    exif = Image.Exif();exif[274] = 6
    output = io.BytesIO();image.save(output, format='JPEG', exif=exif)
    with prepare_document(output.getvalue(), 'image/jpeg') as (root, metadata):
        page = metadata['pages'][0]
        assert (page['width'], page['height']) == (40, 80)
        assert page['source_exif_orientation'] == 6
        assert page['orientation_normalized'] is True
    with pytest.raises(ServiceError) as error:
        with prepare_document(output.getvalue(), 'image/png'):
            pytest.fail('mislabeled image accepted')
    assert error.value.code == 'IMAGE_FORMAT'
