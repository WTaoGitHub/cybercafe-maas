"""Framework self-tests, never connect to 001 or a real model."""
import io
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error
import xml.etree.ElementTree as ET
from e2e.core import Case, CheckFailed, Context, SkipCase, execute, select
from e2e.fixtures import blank_png


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.key = self.root / 'key'
        self.key.write_text('test-key-not-a-real-credential')
        self.key.chmod(0o600)

    def context(self, effects=(), **config):
        return Context({'base_url': 'http://127.0.0.1:18831', 'key_file': str(self.key), **config},
                       self.root / 'run', effects)

    def cases(self):
        return [Case('A', 'auth', 'a', ('smoke',), lambda c: None),
                Case('B', 'ocr', 'b', ('smoke', 'gpu'), lambda c: None),
                Case('C', 'ocr', 'c', ('negative',), lambda c: None)]

    def test_selection_union_intersection_exclusion_and_order(self):
        cases = self.cases()
        self.assertEqual([c.case_id for c in select(cases, ids=['B', 'A'])], ['A', 'B'])
        self.assertEqual([c.case_id for c in select(cases, modules=['ocr'], labels=['smoke'])], ['B'])
        self.assertEqual([c.case_id for c in select(cases, labels=['smoke', 'negative'], exclude_labels=['gpu'])], ['A', 'C'])
        self.assertEqual(select(cases, ids=['A'], modules=['ocr']), [])

    def test_unknown_selectors_never_silently_expand(self):
        for option in ('ids', 'modules', 'labels', 'exclude_labels'):
            with self.assertRaises(ValueError):
                select(self.cases(), **{option: ['typo']})

    def test_effects_and_requirements_skip_without_running(self):
        ctx = self.context()
        def must_not_run(c):
            raise AssertionError('Handler must not execute')
        cases = [Case('A', 'x', 'a', (), must_not_run, ('inference',)),
                 Case('B', 'x', 'b', (), must_not_run, (), ('jpeg_file',))]
        report = execute(ctx, cases)
        self.assertEqual(report['summary']['SKIP'], 2)
        self.assertEqual(len(ET.parse(ctx.output / 'junit.xml').findall('testcase/skipped')), 2)

    def test_reports_private_and_do_not_serialize_config_or_exceptions(self):
        ctx = self.context()
        def fail(c):
            raise RuntimeError('SECRET_MUST_NOT_APPEAR')
        report = execute(ctx, [Case('A', 'x', 'a', (), fail)])
        text = (ctx.output / 'report.json').read_text()
        self.assertNotIn('SECRET_MUST_NOT_APPEAR', text)
        self.assertNotIn(self.key.read_text(), text)
        self.assertEqual((ctx.output / 'report.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual(report['summary']['ERROR'], 1)

    def test_transport_failure_no_replay_and_blocks_next_mutation(self):
        ctx = self.context(('inference',))
        calls = []
        class Opener:
            def open(self, req, timeout):
                calls.append(req)
                checkpoint = json.loads((ctx.output / 'report.json').read_text())
                self_state = checkpoint['results'][0]['requests'][0]['state']
                assert self_state == 'SENDING'
                raise urllib.error.URLError('secret transport text')
        ctx.opener = Opener()
        def infer(c):
            c.request('POST', '/ocr', body={}, inference=True)
        report = execute(ctx, [Case('A', 'x', 'a', (), infer, ('inference',)),
                               Case('B', 'x', 'b', (), infer, ('inference',)),
                               Case('C', 'x', 'read', (), lambda c: None)])
        self.assertEqual(len(calls), 1)
        self.assertEqual([r['status'] for r in report['results']], ['FAIL', 'SKIP', 'PASS'])
        self.assertTrue(report['inference_blocked'])
        self.assertNotIn('secret transport text', (ctx.output / 'report.json').read_text())

    def test_existing_output_not_overwritten(self):
        self.context()
        with self.assertRaises(FileExistsError):
            self.context()

    def test_nonlocal_http_and_public_readable_key_rejected(self):
        with self.assertRaises(CheckFailed):
            self.context(base_url='http://example.com')
        self.key.chmod(0o644)
        with self.assertRaises(CheckFailed):
            Context({'base_url': 'http://127.0.0.1', 'key_file': str(self.key)}, self.root / 'other')

    def test_png_is_valid_and_deterministic(self):
        from PIL import Image
        raw = blank_png()
        self.assertEqual(raw, blank_png())
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
            self.assertEqual(image.size, (64, 64))
            self.assertEqual(image.getpixel((32, 32)), (255, 255, 255))

    def test_handler_cannot_send_undeclared_mutation(self):
        ctx = self.context(('write',))
        report = execute(ctx, [Case('A', 'x', 'a', (), lambda c: c.request('POST', '/ocr', body={}))])
        self.assertEqual(report['summary']['FAIL'], 1)
        self.assertEqual(report['results'][0]['requests'], [])

    def test_interrupt_has_final_report(self):
        ctx = self.context(('inference',))
        def interrupt(c):
            raise KeyboardInterrupt()
        report = execute(ctx, [Case('A', 'x', 'a', (), interrupt, ('inference',))])
        self.assertTrue(report['interrupted'])
        self.assertTrue(report['inference_blocked'])
        self.assertTrue((ctx.output / 'junit.xml').exists())


class CatalogTests(unittest.TestCase):
    def test_metadata_ids_are_unique_and_documented_effects(self):
        from e2e import cases
        from e2e.core import CASES
        self.assertGreaterEqual(len(CASES), 60)
        self.assertEqual(len(CASES), len({c.case_id for c in CASES}))
        for c in CASES:
            self.assertTrue(c.module and c.title and c.labels)
            self.assertTrue(set(c.effects) <= {'write', 'inference', 'load'})
        # Every deferred environment-dependent check must explicitly declare requirements.
        for c in CASES:
            if 'optional' in c.labels:
                self.assertTrue(c.requires, c.case_id)

    def test_cli_comma_and_repeated_selectors(self):
        from e2e.__main__ import values
        self.assertEqual(values([['A,B', 'C'], ['D']]), ['A', 'B', 'C', 'D'])

    def test_observer_uses_fixed_readonly_operation_and_no_shell(self):
        import subprocess
        from unittest.mock import patch
        from e2e.observer import observe
        class C:
            config = {'operator': {'host': '111.4.255.126', 'port': 2023,
                                    'key_file': '~/.ssh/id_ed25519'}}
        with patch('e2e.observer.subprocess.run') as run:
            run.return_value = subprocess.CompletedProcess([], 0, '{"success_events":1}', '')
            self.assertEqual(observe(C(), 'task_usage', task_id='task_test')['success_events'], 1)
            args, options = run.call_args
            self.assertIsInstance(args[0], list)
            self.assertNotIn('shell', options)
            self.assertIn('SET TRANSACTION READ ONLY', options['input'])
            compile(options['input'], '<remote-observer>', 'exec')


class RequestSafetyTests(unittest.TestCase):
    setUp = RunnerTests.setUp
    context = RunnerTests.context
    def fake_response(self, status, value):
        from email.message import Message
        response = io.BytesIO(json.dumps(value).encode())
        response.code = status
        response.headers = Message()
        response.headers['Content-Type'] = 'application/json'
        response.headers['X-Request-ID'] = 'req_framework_test'
        return response

    def test_unknown_error_blocks_mutations_and_report_has_no_payload(self):
        ctx = self.context(('inference',))
        response = self.fake_response(503, {'error': {'code': 'UNKNOWN_BACKEND_ACTIVITY',
                                                     'message': 'PRIVATE_RESPONSE_TEXT'}})
        class Opener:
            def open(self, req, timeout):
                return response
        ctx.opener = Opener()
        def infer(c):
            c.request('POST', '/ocr', body={'image_base64': 'PRIVATE_IMAGE_DATA'}, inference=True).expect(200)
        report = execute(ctx, [Case('A', 'x', 'a', (), infer, ('inference',)),
                               Case('B', 'x', 'b', (), infer, ('inference',))])
        self.assertEqual([r['status'] for r in report['results']], ['FAIL', 'SKIP'])
        saved = (ctx.output / 'report.json').read_text()
        self.assertNotIn('PRIVATE_RESPONSE_TEXT', saved)
        self.assertNotIn('PRIVATE_IMAGE_DATA', saved)
        self.assertIn('UNKNOWN_BACKEND_ACTIVITY', saved)

    def test_confirmed_maintenance_response_is_not_misclassified_unknown(self):
        ctx = self.context(('inference',))
        owner = self
        class Opener:
            def open(self, req, timeout):
                return owner.fake_response(503, {'error': {'code': 'MAINTENANCE_PAUSED'}})
        ctx.opener = Opener()
        def gate(c):
            c.request('POST', '/ocr', body={}, inference=True).expect(503, 'MAINTENANCE_PAUSED')
        report = execute(ctx, [Case('A', 'x', 'a', (), gate, ('inference',))])
        self.assertFalse(report['inference_blocked'])
        self.assertEqual(report['summary']['PASS'], 1)

    def test_partially_completed_upload_opens_circuit_without_replay(self):
        ctx = self.context(('write',))
        calls = []
        class Opener:
            def open(self, req, timeout):
                calls.append(req)
                raise TimeoutError()
        ctx.opener = Opener()
        def upload(c):
            c.upload()
        report = execute(ctx, [Case('A', 'x', 'a', (), upload, ('write',)),
                               Case('B', 'x', 'b', (), upload, ('write',))])
        self.assertEqual(len(calls), 1)
        self.assertTrue(report['inference_blocked'])
        self.assertEqual(report['summary'], {'PASS': 0, 'FAIL': 1, 'ERROR': 0, 'SKIP': 1})

    def test_failed_assertion_still_polls_own_known_task_without_resubmission(self):
        ctx = self.context()
        waited = []
        def wait(ident):
            waited.append(ident)
            ctx.pending_tasks.discard(ident)
        ctx.wait = wait
        def fail(c):
            c.pending_tasks.add('task_owned')
            raise CheckFailed('Known response assertion failed')
        execute(ctx, [Case('A', 'x', 'a', (), fail)])
        self.assertEqual(waited, ['task_owned'])
        self.assertFalse(ctx.pending_tasks)


if __name__ == '__main__':
    unittest.main()


def test_committed_fixture_hashes_and_catalog_are_consistent():
    import hashlib
    from e2e import cases
    from e2e.core import CASES
    root = Path(__file__).resolve().parents[2]
    directory = root / 'e2e/data'
    manifest = json.loads((directory / 'manifest.json').read_text())
    for row in manifest['files']:
        assert hashlib.sha256((directory / row['file']).read_bytes()).hexdigest() == row['sha256']
    catalog = (root / 'e2e/CATALOG.md').read_text()
    documented = [line.split('|')[1].strip() for line in catalog.splitlines() if line.startswith('| ')][1:]
    assert documented == [c.case_id for c in CASES]
