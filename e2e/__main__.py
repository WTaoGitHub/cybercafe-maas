"""Run with python3 -m e2e list|run; no application or third-party imports."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import uuid
from .core import CASES, Context, execute, select
from . import cases


def values(groups):
    return [item for group in (groups or []) for value in group for item in value.split(',') if item]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['list', 'run'])
    parser.add_argument('--all', action='store_true', help='Select the whole catalog (effect gates still apply)')
    parser.add_argument('--case-id', action='append', nargs='+')
    parser.add_argument('--module', action='append', nargs='+')
    parser.add_argument('--label', action='append', nargs='+')
    parser.add_argument('--exclude-label', action='append', nargs='+')
    parser.add_argument('--json', action='store_true', help='List metadata as JSON')
    parser.add_argument('--config', type=Path)
    parser.add_argument('--output', type=Path, help='New directory; never overwrites a previous run')
    parser.add_argument('--allow-writes', action='store_true', help='Allow uploads, task submission and own-task cancellation')
    parser.add_argument('--allow-inference', action='store_true', help='Allow real model execution and successful-usage events')
    parser.add_argument('--allow-load', action='store_true', help='Allow optional bounded concurrency checks')
    parser.add_argument('--require-all', action='store_true', help='Nonzero exit if any selected case skips')
    args = parser.parse_args(argv)
    ids, modules, labels, excluded = [values(v) for v in
                                     (args.case_id, args.module, args.label, args.exclude_label)]
    if args.all and any((ids, modules, labels)):
        parser.error('--all cannot be combined with positive selectors; exclusions are allowed')
    if args.command == 'run' and not args.all and not any((ids, modules, labels)):
        parser.error('Choose --all or at least one positive selector')
    try:
        selected = select(CASES, ids, modules, labels, excluded)
        if not selected:
            raise ValueError('Selection is empty')
    except ValueError as exc:
        parser.error(str(exc))
    if args.command == 'list':
        if args.json:
            print(json.dumps([c.public() for c in selected], indent=2, ensure_ascii=False))
        else:
            for c in selected:
                print(f'{c.case_id:18} {c.module:14} {",".join(c.labels):36} '
                      f'effects={",".join(c.effects) or "read"}  {c.title}')
            print(f'Total: {len(selected)}')
        return 0
    if not args.config:
        parser.error('run requires --config (key contents are never CLI arguments)')
    output = args.output or Path('.maas/e2e') / (
        datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:8])
    try:
        config_path = args.config.expanduser().resolve()
        config = json.loads(config_path.read_text())
        # Resolve input paths against the config location, not an ambiguous current directory.
        for name in ('key_file', 'secondary_key_file', 'restricted_key_file', 'cert_file',
                     'image_file', 'jpeg_file'):
            if config.get(name):
                p = Path(config[name]).expanduser()
                config[name] = str(p if p.is_absolute() else config_path.parent / p)
        if config.get('operator'):
            p = Path(config['operator']['key_file']).expanduser()
            config['operator']['key_file'] = str(p if p.is_absolute() else config_path.parent / p)
        effects = [name for name, allowed in [('write', args.allow_writes),
                    ('inference', args.allow_inference), ('load', args.allow_load)] if allowed]
        ctx = Context(config, output, effects)
    except Exception as exc:
        print('Configuration/output initialization failed (' + type(exc).__name__ +
              '); check config paths, private key permissions, HTTPS certificate and new output directory.', file=sys.stderr)
        return 2
    report = execute(ctx, selected)
    print(json.dumps(report['summary']), 'Report:', output / 'report.json')
    if report['interrupted']:
        return 130
    if report['summary']['FAIL'] or report['summary']['ERROR']:
        return 1
    if ctx.blocked or (args.require_all and report['summary']['SKIP']):
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
