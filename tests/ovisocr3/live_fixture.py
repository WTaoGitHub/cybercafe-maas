"""Build a test-only Ovis guard with Core event log markers and a delayed receipt."""
from pathlib import Path
import sys


def instrument(source):
    replacements = {
        '        await add_request(client, request)': (
            "        await add_request(client, request)\n"
            "        print('test_agent_ovis_submitted '+attempt.token,flush=True)"
        ),
        '                attempt.finish()\n\n    client_type.add_request_async': (
            "                print('test_agent_ovis_terminal '+attempt.token,flush=True)\n"
            '                attempt.finish()\n\n    client_type.add_request_async'
        ),
        '    async def _report(self):\n        try:': (
            '    async def _report(self):\n        await asyncio.sleep(5)\n        try:'
        ),
    }
    for original, updated in replacements.items():
        if source.count(original) != 1:
            raise ValueError('Ovis guard source moved, check the trial instrumentation')
        source = source.replace(original, updated)
    return source


def event_counts(logs, attempt):
    lines = logs.splitlines()
    return {event: sum(line.endswith(f'test_agent_ovis_{event} {attempt}') for line in lines)
            for event in ('submitted', 'terminal')}


if __name__ == '__main__':
    source = Path(__file__).resolve().parents[2] / 'deploy/ovisocr3/vllm_guard.py'
    sys.stdout.write(instrument(source.read_text()))
