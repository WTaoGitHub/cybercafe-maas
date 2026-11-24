import unittest
from types import SimpleNamespace

from scripts.maas.runtime.observe_replica_startup import controller_evidence


class ControllerEvidenceTest(unittest.TestCase):
    def test_records_timing_and_retry_without_copying_whole_private_records(self):
        status = {
            'state': 'observe_ready', 'write_attempted': False,
            'pending_control': {
                '1': {'state': 'reanalyzing', 'observed_for_s': 165.5, 'qualified': False},
                '2': {'state': 'holding', 'target': 1},
            },
            'downscale_retry': {'model_id': 1, 'reason': 'downscale_preflight_unavailable',
                                'failures': 1, 'delay_s': 5, 'extra': 'unused'},
        }
        receipt = SimpleNamespace(model_id=1, before=4, after=3, fingerprint='unused')
        view = controller_evidence(status, {1}, receipt)
        self.assertEqual(view['pending_control'], {'1': status['pending_control']['1']})
        self.assertEqual(view['downscale_retry'], {'model_id': 1, 'failures': 1, 'delay_s': 5,
                                                  'reason': 'downscale_preflight_unavailable'})
        self.assertEqual(view['pending_action'], {'model_id': 1, 'before': 4, 'after': 3})
        self.assertFalse(view['write_attempted'])
        other = controller_evidence(status, {2}, receipt)
        self.assertNotIn('downscale_retry', other)
        self.assertNotIn('pending_action', other)

    def test_incomplete_snapshot_preserves_reason_without_implying_any_write(self):
        view = controller_evidence({'state': 'blocked', 'reason': 'TimeoutError'}, {1}, None)
        self.assertEqual(view, {'state': 'blocked', 'reason': 'TimeoutError', 'pending_control': {}})
