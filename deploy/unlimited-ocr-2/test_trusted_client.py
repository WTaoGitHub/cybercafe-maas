"""Check curl request generation and document completion without SGLang."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

CLIENT = Path(__file__).with_name('trusted_client.py')
spec = importlib.util.spec_from_file_location('trusted_client', CLIENT)
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


class ClientTests(unittest.TestCase):
    def test_curl_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'config.json').write_text(json.dumps({
                'sparse_attention_config': {'page_token_id': 10}, 'eos_token_id': 11,
            }))
            (root / 'page.png').write_bytes(b'fixture')
            subprocess.run([sys.executable, str(CLIENT), '--config', str(root / 'config.json'),
                '--request-json', str(root / 'request.json'), '--max-tokens', '100',
                str(root / 'page.png')], check=True)
            payload = json.loads((root / 'request.json').read_text())
            self.assertEqual(payload['custom_logit_processor'], 'maas.unlimited-ocr-2.ngram.v1')
            self.assertEqual(payload['messages'][0]['content'][0]['image_url']['url'],
                             'data:image/png;base64,Zml4dHVyZQ==')
            self.assertFalse(payload['stream'])
            self.assertNotIn('Authorization', payload)

    def test_incomplete_document_is_not_success(self):
        with self.assertRaises(ValueError):
            client.collect({'choices': [{'finish_reason': 'length', 'message': {'content': 'partial'}}]}, [])
        chunks = []
        client.collect({'choices': [{'finish_reason': 'stop', 'message': {'content': 'ok'}}]}, chunks)
        self.assertEqual(chunks, ['ok'])


if __name__ == '__main__':
    unittest.main()
