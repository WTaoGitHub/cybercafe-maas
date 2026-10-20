"""Forced-command SSH endpoint. Reject all commands other than the fixed inventory verb."""
import json,os,sys
from pathlib import Path
sys.path.insert(0,'/root/wtaobj/maas-framework')
from maas_platform.inventory import collect_inventory
if os.environ.get('SSH_ORIGINAL_COMMAND','')!='inventory':raise SystemExit(64)
path=Path('/root/wtaobj/maas-inventory/config.json')
if path.stat().st_mode & 0o077:raise SystemExit(65)
try:
    config=json.loads(path.read_text())
    print(json.dumps(collect_inventory(config['worker'],config['image'],config['container_prefix'])))
except Exception:
    print('Inventory unavailable',file=sys.stderr);raise SystemExit(66)
