"""Read GPUStack v2.2.3 worker watch events from outside the server process."""
import argparse
import json
import sys
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:18080')
    parser.add_argument('--name', help='Exact worker name filter on the server')
    parser.add_argument('--cluster-id', type=int)
    parser.add_argument('--limit', type=int, default=4, help='Stop after this many matching events')
    parser.add_argument('--state-changes-only', action='store_true',
                        help='Print only UPDATED events whose changed_fields includes state')
    parser.add_argument('--raw', action='store_true', help='Print the complete event JSON')
    args = parser.parse_args()
    if args.limit < 1:
        parser.error('--limit must be positive')
    key = sys.stdin.readline().strip()
    if not key:
        parser.error('provide the GPUStack worker-list credential on standard input')
    params = {'watch': 'true'}
    if args.name:
        params['name'] = args.name
    if args.cluster_id is not None:
        params['cluster_id'] = args.cluster_id
    url = args.base_url.rstrip('/') + '/v2/workers?' + urlencode(params)
    request = Request(url, headers={'Authorization': f'Bearer {key}'})
    with urlopen(request, timeout=30) as response:
        print(f'HTTP {response.status} {response.headers.get("Content-Type")}', file=sys.stderr)
        count = 0
        for line in response:
            if not line.strip():
                continue
            event = json.loads(line)
            changed = event.get('changed_fields') or {}
            if args.state_changes_only and (event['type'] != 2 or 'state' not in changed):
                continue
            worker = event.get('data') or {}
            output = {
                'type': event['type'],
                'worker_id': event.get('id'),
                'state': worker.get('state'),
                'changed_fields': list(changed),
                'gpu_count': len((worker.get('status') or {}).get('gpu_devices') or []),
            }
            print(line.decode('utf-8').rstrip('\r\n') if args.raw else json.dumps(output), flush=True)
            count += 1
            if count >= args.limit:
                break


if __name__ == '__main__':
    main()
