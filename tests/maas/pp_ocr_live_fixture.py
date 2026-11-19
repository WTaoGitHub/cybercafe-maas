"""Generate an instrumented PP-OCRv4 entry point for the dual-GPU acceptance.

The fixture requires the real execution guard and delays only the finish receipt.
Use it with synthetic requests on dedicated, guarded test endpoints.
"""
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


def verify_events(record, winner):
    assert len(record) == 2 and winner in record
    for host,entries in record.items():
        phases=[x['phase'] for x in entries]
        assert phases.count('ocr_engine_enter')==(host==winner)
        assert phases.count('ocr_engine_return')==(host==winner)
        if host==winner:
            assert sum(x['phase']=='finish_callback' and x['ok'] for x in entries)==1

if __name__ == '__main__':
    server = Path(__file__).resolve().parents[2] / 'gpu-ocr' / 'server.py'
    sys.stdout.write(instrument(server.read_text()))
