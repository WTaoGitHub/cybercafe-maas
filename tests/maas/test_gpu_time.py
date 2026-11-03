import io
import json
import unittest
from datetime import date, datetime
from unittest.mock import patch

from maas_platform.gpu_time import BEIJING, daily_report, open_db, pages, save, snapshot


class GPUTimeTest(unittest.TestCase):
    def test_paginated_snapshot(self):
        replies = [
            io.StringIO(json.dumps({'items': [{'id': i}],
                                     'pagination': {'page': i, 'totalPage': 2, 'total': 2}}))
            for i in (1, 2)
        ]
        with patch('maas_platform.gpu_time.urlopen', side_effect=replies):
            self.assertEqual(pages('http://localhost', 'test', 'workers'), [{'id': 1}, {'id': 2}])

    def test_midnight_failures_and_model_deduplication(self):
        day = date(2026, 11, 3)
        start = int(datetime(2026, 11, 3, tzinfo=BEIJING).timestamp())
        worker = {'id': 1, 'state': 'ready',
                  'status': {'gpu_devices': [{'uuid': 'gpu-a', 'index': 0, 'type': 'cuda'}]}}
        replica = {'state': 'running', 'worker_id': 1, 'gpu_indexes': [0], 'gpu_type': 'cuda',
                   'model_id': 5, 'model_name': 'OCR'}
        ready = snapshot([worker], [replica, replica])
        conflict = snapshot([worker, {**worker, 'id': 2}], [])
        self.assertIsNone(conflict['cards']['gpu-a']['online'])
        offline = snapshot([{**worker, 'state': 'unreachable'}], [])
        db = open_db(':memory:')
        self.addCleanup(db.close)
        for offset, payload in [(-30, ready), (30, offline), (60, offline), (90, None),
                                (120, ready), (300, ready), (86380, ready), (86440, ready)]:
            save(db, start + offset, payload)
        row = daily_report(db, day, start + 86440)['gpus']['gpu-a']
        self.assertEqual(row['online_seconds'], 50)
        self.assertEqual(row['not_ready_seconds'], 30)
        self.assertEqual(row['unknown_seconds'], 86400 - 80)
        self.assertEqual(row['models'], {'5': {'name': 'OCR', 'seconds': 50}})
