# OvisOCR3 deployment checks

`test_entrypoint.py` checks that the launcher waits for the shared cgroup guard and does not start vLLM if the guard rejects startup. It needs pytest, not a GPU or vLLM.

```sh
PYTHONDONTWRITEBYTECODE=1 python -m pytest tests/ovisocr3/test_entrypoint.py -q -p no:cacheprovider
```

`fixtures/service-report.png` is a synthetic 1280×900 English document with no personal or production data. Use it for a minimal real-image deployment check, not as an accuracy benchmark. Plain OCR should include `Quarterly Service Report`, `QSR-2026-0410`, `128`, and `12 minutes`. For specified-key extraction, request `Report ID`, `Ticket count`, `Average resolution time`, and `Status`; expected values are `QSR-2026-0410`, `128`, `12 minutes`, and `Complete`. Structured output must be a nonempty array with valid categories and 0–999 integer bounding boxes.

To regenerate the fixture, use the existing environment with the project's pinned `Pillow==13.0.0`:

```sh
PYTHONDONTWRITEBYTECODE=1 python tests/ovisocr3/make_fixture.py
```

Actual API probes must use an authenticated route, bounded timeouts and no automatic retries. Save temporary requests/responses in the task's cleanup directory, not here. The runtime is intentionally retained when it is the requested deployment.
