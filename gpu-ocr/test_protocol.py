import json
import unittest
from ocr_protocol import completion_events


class ProtocolTest(unittest.TestCase):
    def test_sse_unicode_newlines_and_terminal_event(self):
        result = {'text': '中文 "引号"\nEnglish', 'node': 'gpu-test', 'elapsed_ms': 12.5}
        events = list(completion_events(result, 'ocr-test', 123, 'rapidocr-gpu'))
        self.assertEqual(events[-1], 'data: [DONE]\n\n')
        chunks = [json.loads(e.removeprefix('data: ').strip()) for e in events[:-1]]
        self.assertTrue(all(c['id'] == 'ocr-test' for c in chunks))
        self.assertEqual(chunks[0]['choices'][0]['delta']['role'], 'assistant')
        text = ''.join(c['choices'][0]['delta'].get('content', '') for c in chunks)
        self.assertEqual(text, result['text'])
        self.assertEqual(chunks[-1]['choices'][0]['finish_reason'], 'stop')
        self.assertNotIn('usage', chunks[-1])

    def test_empty_ocr_is_still_a_finished_stream(self):
        events = list(completion_events({'text': '', 'node': 'gpu', 'elapsed_ms': 0}, 'id', 0, 'm'))
        self.assertEqual(len(events), 4)
        self.assertIn('"finish_reason": "stop"', events[-2])


if __name__ == '__main__':
    unittest.main()
