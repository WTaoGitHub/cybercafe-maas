"""Start vLLM after the shared test environment's container caps are applied."""
import json
import os
import sys

from resource_guard import wait_for_limits


def main():
    if not sys.argv[1:]:
        raise SystemExit('Pass the fixed vLLM model path and server arguments')
    caps = wait_for_limits()
    print(json.dumps({'ovisocr3_resource_caps_verified': caps}), flush=True)
    # shortcut: each replacement container needs caps; add runner support before unattended recovery.
    os.execvp('vllm', ['vllm', 'serve', *sys.argv[1:]])


if __name__ == '__main__':
    main()
