import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from maas_platform.replica_contract import ControlPolicy, ControlPolicyFile
from maas_platform.replica_status import inspect
from maas_replica_policy import Policy


def config(version=2, mode='observe', pressure_mode='disabled'):
    raw = {
        'schema_version': version, 'revision': 1,
        'scope': {'cluster_id': 1, 'owner_principal_id': 1},
        'models': {'1': {'vram_claim_bytes': 4, 'max_replicas': 2}},
        'execution': {'mode': mode, 'scale_up_batch_max': 1,
                      'max_inflight_global': 2, 'max_inflight_per_cluster': 2},
    }
    pressure = {'max_sample_age_s': 20, 'recovery_hold_s': 10,
                'require_worker_coverage': True,
                'signals': {'network_rx_mib_s': {'low': 40, 'high': 150}}}
    if version == 2:
        raw.update(reconcile={'interval_s': 30, 'stability_s': 10},
                   pressure={'mode': pressure_mode, **(pressure if pressure_mode != 'disabled' else {})})
    else:
        raw['pressure'] = pressure
    return raw


class ContractTests(unittest.TestCase):
    def test_v1_keeps_strict_pressure_and_original_event_policy(self):
        raw = config(1)
        actual = ControlPolicy(raw)
        expected = Policy(raw)
        self.assertEqual(actual.pressure_mode, 'required')
        self.assertIsNone(actual.reconcile)
        self.assertEqual(actual.effective(None), expected.effective(None))
        self.assertEqual(actual.pressure, expected.pressure)

    def test_v2_modes_are_explicit_and_legacy_loader_still_rejects_v2(self):
        for mode in ('disabled', 'advisory', 'required'):
            with self.subTest(mode=mode):
                raw = config(pressure_mode=mode)
                policy = ControlPolicy(raw)
                self.assertEqual(policy.pressure_mode, mode)
                self.assertEqual(policy.pressure is None, mode == 'disabled')
                self.assertEqual(policy.reconcile.interval_s, 30)
                with self.assertRaises(ValueError):
                    Policy(raw)

    def test_apply_still_requires_explicit_authorization(self):
        for version in (1, 2):
            with self.subTest(version=version):
                raw = config(version, 'apply')
                with self.assertRaises(ValueError):
                    ControlPolicy(raw)
                self.assertEqual(ControlPolicy(raw, allow_apply=True).execution['mode'], 'apply')

    def test_invalid_fields_types_and_limits_are_rejected(self):
        cases = [
            ('schema_version', 3), ('unknown', 1),
            ('reconcile', {'interval_s': 0}), ('reconcile', {'interval_s': True}),
            ('reconcile', {'stability_s': 121}), ('reconcile', {'unknown': 1}),
            ('startup', {'first_replica_fastpath': 1}),
            ('startup', {'parallel_startup': 'true'}),
            ('startup', {'degraded_write_interval_s': 0}),
            ('startup', {'degraded_inflight_max': 3}),
            ('startup', {'degraded_grace_s': -1}),
            ('pressure', {'mode': 'maybe'}),
            ('pressure', {'mode': 'disabled', 'signals': {}}),
            ('pressure', {'mode': 'required'}),
            ('pressure', {'mode': 'advisory', 'unknown': True}),
            ('pressure', {'mode': 'required', 'max_sample_age_s': 20,
                          'recovery_hold_s': 10, 'signals': ['invalid']}),
            ('models', ['invalid-model-map']),
            ('execution', {'mode': 'observe'}),
        ]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                raw = config(); raw[key] = value
                with self.assertRaises((ValueError, KeyError)):
                    ControlPolicy(raw)

    def test_event_overrides_cannot_change_hard_startup_authority(self):
        raw = config()
        raw['event_overrides'] = {'gpu_added': {'execution': {'max_inflight_global': 8}}}
        with self.assertRaises(ValueError):
            ControlPolicy(raw)

    def test_parallel_startup_requires_explicit_v2_opt_in(self):
        self.assertFalse(ControlPolicy(config()).startup.parallel_startup)
        raw = config(); raw['startup'] = {'parallel_startup': True}
        self.assertTrue(ControlPolicy(raw).startup.parallel_startup)

    def test_required_mode_cannot_authorize_first_replica_pressure_bypass(self):
        raw = config(pressure_mode='required')
        raw['startup'] = {'first_replica_fastpath': True}
        with self.assertRaises(ValueError):
            ControlPolicy(raw)

    def test_hot_reload_identity_revision_and_private_file_safety(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / 'policy.json'; raw = config()
            p.write_text(json.dumps(raw)); p.chmod(0o600)
            loader = ControlPolicyFile(p)
            first = loader.refresh()
            p.write_text(json.dumps(raw, indent=4))
            self.assertIs(loader.refresh(), first)
            raw['startup'] = {'first_replica_fastpath': True}
            p.write_text(json.dumps(raw))
            with self.assertRaises(ValueError):
                loader.refresh()
            raw['revision'] = 2; p.write_text(json.dumps(raw))
            self.assertTrue(loader.refresh().startup.first_replica_fastpath)
            p.chmod(0o644)
            with self.assertRaises(ValueError):
                loader.refresh()
            p.chmod(0o600)
            link = Path(tmp) / 'link'; link.symlink_to(p)
            with self.assertRaises(OSError):
                ControlPolicyFile(link).refresh()

    def test_incident_diagnosis_exposes_both_staleness_and_missing_new_workers(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / 'policy.json'; sample = Path(tmp) / 'pressure.json'
            p.write_text(json.dumps(config(1))); p.chmod(0o600)
            sample.write_text(json.dumps({'observed_at': 100, 'worker_ids': [3],
                                         'signals': {'network_rx_mib_s': 0}})); sample.chmod(0o600)
            out = inspect(p, sample, [4, 5], clock=lambda: 200)
            self.assertEqual(out['pressure_mode'], 'required')
            self.assertEqual(out['pressure']['state'], 'pressure_unavailable')
            self.assertEqual(out['pressure']['reasons'], ['sample_stale', 'missing_workers'])
            self.assertEqual(out['pressure']['missing_worker_ids'], [4, 5])
            self.assertEqual(out['pressure']['sample_age_s'], 100)
            self.assertTrue(out['read_only'])
            self.assertFalse(out['runtime_authorization_checked'])
            self.assertNotIn('signals', json.dumps(out))

    def test_disabled_inspection_does_not_read_pressure_file(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / 'policy.json'; p.write_text(json.dumps(config())); p.chmod(0o600)
            out = inspect(p, Path(tmp) / 'missing', [4])
            self.assertEqual(out['pressure']['state'], 'disabled')
            with self.assertRaises(ValueError):
                inspect(p, worker_ids=[True])

    def test_no_secret_or_source_path_in_diagnostic(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / 'policy.json'; sample = Path(tmp) / 'private-sample.json'
            p.write_text(json.dumps(config(pressure_mode='required'))); p.chmod(0o600)
            sample.write_text('sensitive-invalid-content'); sample.chmod(0o600)
            out = inspect(p, sample, [4])
            self.assertEqual(out['pressure']['reasons'], ['sample_invalid'])
            text = json.dumps(out)
            self.assertNotIn('sensitive-invalid-content', text)
            self.assertNotIn(str(sample), text)

    def test_private_cli_emits_json_and_sanitizes_invalid_input_errors(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / 'policy.json'
            p.write_text(json.dumps(config())); p.chmod(0o600)
            command = [sys.executable, '-m', 'maas_platform.replica_status', '--policy', str(p)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)['read_only'])
            p.write_text('private-secret-marker')
            result = subprocess.run(command, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, '')
            self.assertNotIn('private-secret-marker', result.stderr)
            self.assertNotIn(str(p), result.stderr)


if __name__ == '__main__':
    unittest.main()
