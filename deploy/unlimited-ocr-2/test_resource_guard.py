import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('resource_guard', Path(__file__).with_name('resource_guard.py'))
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class ResourceGuardTest(unittest.TestCase):
    def test_only_bounded_cgroups_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for memory, cpu, accepted in [
                (str(guard.MEMORY_BYTES), '400000 100000', True),
                ('max', '400000 100000', False),
                (str(guard.MEMORY_BYTES + 1), '400000 100000', False),
                (str(guard.MEMORY_BYTES), 'max 100000', False),
                (str(guard.MEMORY_BYTES), '400001 100000', False),
                (str(guard.MEMORY_BYTES), '0 100000', False),
                (str(guard.MEMORY_BYTES), '400000 0', False),
                ('invalid', 'invalid', False),
            ]:
                with self.subTest(memory=memory, cpu=cpu):
                    (root / 'memory.max').write_text(memory)
                    (root / 'cpu.max').write_text(cpu)
                    self.assertEqual(guard.limits(root) is not None, accepted)
            (root / 'memory.max').unlink()
            self.assertIsNone(guard.limits(root))

    def test_missing_caps_never_exec_engine(self):
        with patch.object(guard, 'limits', return_value=None), patch.object(guard.os, 'execv') as execute:
            with patch.object(guard.sys, 'argv', ['guard', '--help']):
                with patch.object(guard.time, 'monotonic', side_effect=[0, 121]):
                    with self.assertRaisesRegex(SystemExit, 'engine was not started'):
                        guard.main()
            execute.assert_not_called()

    def test_caps_are_checked_before_exact_argument_forwarding(self):
        with patch.object(guard, 'limits', return_value={'memory_bytes': guard.MEMORY_BYTES}), patch.object(guard.os, 'execv') as execute:
            with patch.object(guard.sys, 'argv', ['guard', '--model-path', '/model', '--port', '18090']):
                guard.main()
            execute.assert_called_once_with(guard.sys.executable, [guard.sys.executable, '-m', 'sglang.launch_server', '--model-path', '/model', '--port', '18090'])


if __name__ == '__main__':
    unittest.main()
