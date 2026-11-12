import asyncio
import os
from pathlib import Path
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest

from maas_platform.gpu_daily_watch import run_service


class DailyShutdownTest(unittest.IsolatedAsyncioTestCase):
    async def test_collector_failure_still_propagates_and_restores_handler(self):
        previous = signal.getsignal(signal.SIGTERM)

        async def failed():
            raise ValueError('collector failure')

        with self.assertRaises(ValueError):
            await run_service(failed)
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)

    async def test_external_cancellation_is_not_misreported_as_sigterm_success(self):
        entered, cleaned = asyncio.Event(), asyncio.Event()
        previous = signal.getsignal(signal.SIGTERM)

        async def worker():
            try:
                entered.set()
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        task = asyncio.create_task(run_service(worker))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(cleaned.is_set())
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)


class DailySignalProcessTest(unittest.TestCase):
    def test_real_sigterm_drains_child_tasks_and_exits_zero(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            code = '''import asyncio,sys
from pathlib import Path
from maas_platform.gpu_daily_watch import run_service
root=Path(sys.argv[1])
async def child():
 try:
  await asyncio.Event().wait()
 finally:
  (root/'child-cleaned').write_text('yes')
async def worker():
 task=asyncio.create_task(child())
 try:
  await asyncio.sleep(0)
  (root/'ready').write_text('yes')
  await asyncio.Event().wait()
 finally:
  (root/'cleaning').write_text('yes')
  await asyncio.sleep(0.3)
  task.cancel()
  await asyncio.gather(task,return_exceptions=True)
  (root/'cleaned').write_text('yes')
asyncio.run(run_service(worker))
'''
            process = subprocess.Popen([sys.executable, '-c', code, tmp],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                deadline = time.monotonic() + 5
                while not (root/'ready').exists() and time.monotonic() < deadline:
                    if process.poll() is not None:
                        self.fail('Child exited before receiving SIGTERM')
                    time.sleep(0.02)
                self.assertTrue((root/'ready').exists())
                os.kill(process.pid, signal.SIGTERM)
                deadline = time.monotonic() + 2
                while not (root/'cleaning').exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue((root/'cleaning').exists())
                # A second stop signal must not interrupt the first cleanup.
                os.kill(process.pid, signal.SIGTERM)
                out, err = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0, (out, err))
                self.assertTrue((root/'cleaned').exists())
                self.assertTrue((root/'child-cleaned').exists())
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)


if __name__ == '__main__':
    unittest.main()
