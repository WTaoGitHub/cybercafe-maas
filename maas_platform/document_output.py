"""Approved layout parsing; no external files, image links or invented confidence."""
import re
from . import _unlimited_postprocess as upstream
from .errors import ServiceError

POSTPROCESSOR_REVISION = '64e6f066023b305ab03860603a9cf358a8b70148'
POSTPROCESSOR_SHA256 = 'fb38738cd5f09625fb1095d201533b6059a85820bca99c4262347cc7b078a782'


def markdown(text):
    # No crop artifacts are produced; remove only the upstream converter's placeholder links.
    return re.sub(r'!\[\]\(page\d+_\d+_\d+_\d+_\d+\.jpg\)', '[image omitted]', upstream.convert(text))


def parse_document_output(value):
    envelope, pages = value['envelope'], value['pages']
    try:
        choices = envelope['choices']
        if len(choices) != 1 or choices[0]['finish_reason'] != 'stop':
            raise ValueError('incomplete document')
        text = choices[0]['message']['content']
        if not isinstance(text, str) or not text or len(text.encode()) > 4 * 1024**2:
            raise ValueError('output limit')
        marks = list(upstream.PAGE_SPLIT_RE.finditer(text))
        if len(marks) != len(pages) or upstream.STRAY_TOKEN_RE.sub('', text[:marks[0].start()]).strip():
            raise ValueError('page count mismatch')
        metadata = envelope.get('metadata') or {}
        if len(pages) > 3 and metadata.get('doc_split_complete') is not True:
            raise ValueError('missing window completion evidence')
        if 'doc_split_complete' in metadata and metadata['doc_split_complete'] is not True:
            raise ValueError('incomplete windows')
        windows = metadata.get('doc_split_batches', 1)
        if type(windows) is not int or not 1 <= windows <= len(pages):
            raise ValueError('invalid window count')
        blocks = upstream._parse_blocks(text)
        if len(blocks) > 10000:
            raise ValueError('block limit')
        output_pages = [{**page, 'blocks': []} for page in pages]
        for block in blocks:
            number = block['page']
            if not 1 <= number <= len(pages):
                raise ValueError('invalid page reference')
            bbox = block['bbox']
            if bbox is not None:
                if not (len(bbox) == 4 and all(0 <= n <= 1000 for n in bbox)
                        and bbox[0] <= bbox[2] and bbox[1] <= bbox[3]):
                    raise ValueError('invalid layout coordinates')
                block['bbox'] = list(bbox)
            block = {k: v for k, v in block.items() if k != 'page'}
            output_pages[number - 1]['blocks'].append(block)
        for index, page in enumerate(output_pages):
            fragment = text[marks[index].start():marks[index + 1].start() if index + 1 < len(marks) else len(text)]
            page['markdown'] = markdown(fragment)
        return {'schema_version': 'unlimited-ocr-2.document.v1', 'provenance': value['provenance'],
            'parameters': value['parameters'], 'page_count': len(pages), 'pages': output_pages,
            'coordinate_space': 'normalized_0_1000_on_rendered_page',
            'raw_output': text, 'markdown': markdown(text), 'image_extraction': False,
            'window_count': windows}
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        raise ServiceError('OUTPUT_INVALID', 'Document output is incomplete or has invalid layout', 422) from None
