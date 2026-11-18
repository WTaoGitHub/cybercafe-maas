import importlib.util
from pathlib import Path
import sys
import types


def test_engine_executes_only_after_caps(monkeypatch):
    events = []
    guard = types.ModuleType('resource_guard')
    guard.wait_for_limits = lambda: events.append('caps') or {'memory_bytes': 16 * 1024**3}
    monkeypatch.setitem(sys.modules, 'resource_guard', guard)
    path = Path(__file__).resolve().parents[2] / 'deploy/ovisocr3/entrypoint.py'
    spec = importlib.util.spec_from_file_location('ovisocr3_entrypoint', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, 'argv', ['entrypoint.py', '/models/ovisocr3', '--port', '8000'])
    monkeypatch.setattr(module.os, 'execvp', lambda executable, args: events.append((executable, args)))
    module.main()
    assert events == ['caps', ('vllm', ['vllm', 'serve', '/models/ovisocr3', '--port', '8000'])]

    def reject_caps():
        raise SystemExit('caps missing')

    monkeypatch.setattr(module, 'wait_for_limits', reject_caps)
    events.clear()
    try:
        module.main()
    except SystemExit as exc:
        assert str(exc) == 'caps missing'
    else:
        raise AssertionError('The engine must not start without resource caps')
    assert events == []
