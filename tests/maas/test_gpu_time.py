import io
import json
import unittest
from unittest.mock import patch

from maas_platform.gpu_time import pages, snapshot, summarize


class GPUTimeTest(unittest.TestCase):
    def test_paginated_workers(self):
        replies = [
            io.StringIO(json.dumps({'items': [{'id': i}],
                                     'pagination': {'page': i, 'totalPage': 2, 'total': 2}}))
            for i in (1, 2)
        ]
        with patch('maas_platform.gpu_time.urlopen', side_effect=replies) as fetch:
            self.assertEqual(pages('http://localhost', 'test'), [{'id': 1}, {'id': 2}])
            self.assertTrue(all('/v2/workers?' in call.args[0].full_url for call in fetch.call_args_list))

    def test_duplicate_uuid_counts_once(self):
        worker = {'id': 1, 'state': 'ready', 'status': {'gpu_devices': [{'uuid': 'gpu-a'}]}}
        self.assertEqual(snapshot([worker]), {'gpu-a': True})
        self.assertEqual(snapshot([worker, {**worker, 'id': 2}]), {'gpu-a': True})
        old_worker = {**worker, 'id': 2, 'state': 'not_ready'}
        self.assertEqual(snapshot([worker, old_worker]), {'gpu-a': True})
        self.assertEqual(snapshot([old_worker, {**old_worker, 'id': 3}]), {'gpu-a': False})

    def test_clipping_failures_and_session_gaps(self):
        online, not_ready = {'gpu-a': True}, {'gpu-a': False}
        samples = [(-30, 1, online), (30, 1, not_ready), (60, 1, not_ready),
                   (90, 1, None), (120, 1, online), (300, 1, online),
                   (320, 2, online), (380, 2, online)]
        intervals = []
        day = summarize({'gpu-a': -30}, samples, 0, 350, intervals)['gpu-a']
        self.assertEqual(intervals, [{'start_at': 0, 'end_at': 60}, {'start_at': 320, 'end_at': 350}])
        following = summarize({'gpu-a': -30}, samples, 350, 400)['gpu-a']
        total = summarize({'gpu-a': -30}, samples, 0, 400)['gpu-a']
        self.assertEqual(day, {'online_seconds': 60, 'offline_seconds': 30, 'uncovered_seconds': 260})
        self.assertEqual(total, {key: day[key] + following[key] for key in day})

    def test_first_seen_absence_and_legacy_ambiguity(self):
        samples = [(0, 1, {}), (60, 1, {'gpu-a': True}),
                   (120, 1, {}), (180, 1, {'gpu-a': None}), (240, 1, {})]
        self.assertEqual(summarize({'gpu-a': 60}, samples, 0, 240)['gpu-a'],
                         {'online_seconds': 60, 'offline_seconds': 60, 'uncovered_seconds': 60})
