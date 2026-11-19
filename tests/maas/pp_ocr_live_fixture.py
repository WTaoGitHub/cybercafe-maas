"""Generate an instrumented PP-OCRv4 entry point for the dual-GPU acceptance.

The fixture requires the real execution guard and delays only the finish receipt.
Use it with synthetic requests on dedicated, guarded test endpoints.
"""
import argparse
from pathlib import Path
import sys


def instrument(source):
    marker='app = FastAPI()'
    replacement='''import json as _trial_json
if not guard:
    raise RuntimeError('The current pilot requires the original execution guard')
_original_report = guard.report

def _trial_event(attempt, phase, **data):
    print('test_agent_ppocr_event '+_trial_json.dumps({'attempt':attempt,'phase':phase,**data}),flush=True)

def _trial_report(attempt, *, finished):
    if finished:
        time.sleep(7)
    then=time.perf_counter()
    ok=False
    try:
        result=_original_report(attempt,finished=finished)
        ok=True
        return result
    finally:
        _trial_event(attempt,'finish_callback' if finished else 'start_callback',
                     ok=ok,milliseconds=round((time.perf_counter()-then)*1000,2))

guard.report=_trial_report

def _trial_execute(attempt, array):
    def measured_engine(image):
        _trial_event(attempt,'ocr_engine_enter')
        output=engine(image)
        _trial_event(attempt,'ocr_engine_return')
        return output
    return guard.run(attempt, measured_engine, array)
'''
    assert source.count(marker)==1
    source=source.replace(marker,replacement+'\n'+marker)
    original='result, _ = guard.run(attempt, engine, array) if guard else engine(array)'
    assert source.count(original)==1
    return source.replace(original, 'result, _ = _trial_execute(attempt, array)')


def instrument_gateway(source, *, finish_delay=8):
    """Inject a 503 after the grant while the real GPU completes independently."""
    if type(finish_delay) is not int or not 1 <= finish_delay <= 30:
        raise ValueError('Finish delay must be an integer from 1 to 30 seconds')
    marker = 'app = FastAPI()'
    routes = '''
import hashlib as _trial_hashlib
import json as _trial_json
from collections import defaultdict as _TrialEvents
from concurrent.futures import ThreadPoolExecutor as _TrialPool

if guard is None:
    raise RuntimeError('The gateway trial requires the real execution guard')
_gateway_events = _TrialEvents(list)
_gateway_lock = threading.Lock()
_gateway_pool = _TrialPool(max_workers=1)


def _gateway_event(label, phase, attempt):
    with _gateway_lock:
        _gateway_events[label].append({
            'phase': phase, 'marker_sha256': _trial_hashlib.sha256(attempt.encode()).hexdigest()})


@app.get('/probe/stats')
def _gateway_stats():
    with _gateway_lock:
        return {label: list(entries) for label, entries in _gateway_events.items()}


def _gateway_execute(label, encoded, attempt):
    try:
        result = recognize(encoded, attempt, gateway_label=label)
        if not result['text']:
            raise RuntimeError('Empty trial OCR result')
        # Keep the disconnect window observable after real CUDA execution returns.
        time.sleep(__TRIAL_FINISH_DELAY__)
        guard.report_finished(attempt)
        _gateway_event(label, 'finish', attempt)
    except Exception:
        _gateway_event(label, 'incomplete', attempt)
'''
    assert source.count(marker) == 1
    routes = routes.replace('__TRIAL_FINISH_DELAY__', str(finish_delay))
    source = source.replace(marker, marker + '\n' + routes)
    replacements = {
        'def recognize(encoded, attempt):': 'def recognize(encoded, attempt, gateway_label=None):',
        '        result, _ = guard.run(attempt, engine, array) if guard else engine(array)':
            '''        if gateway_label is None:
            result, _ = guard.run(attempt, engine, array)
        else:
            _gateway_event(gateway_label, 'engine_enter', attempt)
            result, _ = engine(array)
            _gateway_event(gateway_label, 'engine_return', attempt)''',
        '    result = recognize(encoded, attempt)':
            '''    label = body['messages'][0].get('content')
    if not isinstance(label, str) or not label.startswith('test_agent_retry_') or len(label) > 96:
        raise HTTPException(400, 'Only marked synthetic requests are accepted')
    with _gateway_lock:
        if sum(map(len, _gateway_events.values())) >= 200:
            raise HTTPException(429, 'Trial storage ceiling reached')
    _gateway_event(label, 'delivery', attempt)
    if label.endswith('_normal'):
        result = recognize(encoded, attempt)
        _gateway_event(label, 'normal_finished', attempt)
        return {'choices': [{'message': {'content': _trial_json.dumps(result)}, 'finish_reason': 'stop'}]}
    try:
        guard.report(attempt, finished=False)
    except HTTPException as exc:
        _gateway_event(label, f'denied_{exc.status_code}', attempt)
        raise
    _gateway_pool.submit(_gateway_execute, label, encoded, attempt)
    raise HTTPException(503, 'Injected response after execution grant')''',
    }
    for before, after in replacements.items():
        assert source.count(before) == 1
        source = source.replace(before, after)
    return source


def verify_events(record, winner):
    assert len(record) == 2 and winner in record
    for host,entries in record.items():
        phases=[x['phase'] for x in entries]
        assert phases.count('ocr_engine_enter')==(host==winner)
        assert phases.count('ocr_engine_return')==(host==winner)
        if host==winner:
            assert sum(x['phase']=='finish_callback' and x['ok'] for x in entries)==1

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gateway', action='store_true')
    parser.add_argument('--finish-delay', type=int, default=8, help='Seconds before reporting the gateway fault terminal')
    args = parser.parse_args()
    source = (Path(__file__).resolve().parents[2] / 'gpu-ocr' / 'server.py').read_text()
    code = instrument_gateway(source, finish_delay=args.finish_delay) if args.gateway else instrument(source)
    sys.stdout.write(code)
