"""OpenAI-compatible SSE framing; OCR has completed before these events are sent."""
import json


def completion_events(result, completion_id, created, model):
    common = {'id': completion_id, 'object': 'chat.completion.chunk',
              'created': created, 'model': model}
    for delta, finish in [({'role': 'assistant', 'content': ''}, None),
                          ({'content': result['text']}, None), ({}, 'stop')]:
        chunk = {**common, 'choices': [{'index': 0, 'delta': delta,
                                       'finish_reason': finish}]}
        if finish:
            chunk['ocr_metadata'] = {'node': result['node'], 'elapsed_ms': result['elapsed_ms']}
        yield 'data: ' + json.dumps(chunk, ensure_ascii=False) + '\n\n'
    yield 'data: [DONE]\n\n'
