import importlib.util
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('trusted_client', Path(__file__).resolve().parents[1] / 'deploy/unlimited-ocr-2/trusted_client.py')
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


def event(content='', finish=None):
    return 'data: ' + json.dumps({'choices': [{'delta': {'content': content}, 'finish_reason': finish}]})


class StreamTest(unittest.TestCase):
    def test_successful_stream(self):
        chunks = []
        client.collect([event('<PAGE>text'), event(finish='stop'), 'data: [DONE]'], chunks)
        self.assertEqual(''.join(chunks), '<PAGE>text')

    def test_incomplete_or_truncated_stream_keeps_partial_text(self):
        for lines in ([event('<PAGE>partial')], [event('<PAGE>partial', 'length'), 'data: [DONE]']):
            chunks = []
            with self.assertRaises(ValueError):
                client.collect(lines, chunks)
            self.assertEqual(''.join(chunks), '<PAGE>partial')


if __name__ == '__main__':
    unittest.main()
