from datetime import datetime, timezone
import hashlib

from scripts.maas.runtime.summarize_pp_handoff import summarize


def test_handoff_assigns_slot_time_and_reports_missing_traces():
    ledger = {'period': [100, 110], 'attempts': []}
    events = []
    gateway = []
    for kind in ('task', 'sync'):
        ledger['attempts'].append(dict(
            kind=kind, token=kind, probe=False, queued=100.5, started=101,
            authorized=102, gpu_finished=104, completed=105,
        ))
        events.append(dict(kind=kind, token=kind, started=101, granted=103))
        gateway.append(dict(
            tag='phase-' + hashlib.sha256(f'{kind}.{kind}'.encode()).hexdigest()[:24],
            ingress=datetime.fromtimestamp(102.25, timezone.utc).isoformat(),
            upstream_tx_ms=250, attempts=1, code=200,
        ))

    result = summarize(ledger, events, gateway)
    assert result['coverage_ok']
    for kind, total in [('task', 30), ('sync', 20)]:
        assert result['by_kind'][kind]['slot_seconds'] == dict(
            preparation=1, gateway_forwarding=0.5, waiting_gpu_grant=0.5,
            gpu_held=1, result_finalization=1, unassigned=total - 4,
        )
    assert not summarize(ledger, events, gateway[:-1])['coverage_ok']
