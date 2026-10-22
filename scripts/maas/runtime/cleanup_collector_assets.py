"""E6 private asset cleanup, explicit role. Never prints credentials/key contents.

001 rollback source/assets must already exist. 002 preserves all other SSH keys.
"""
import argparse,hashlib,json,os,shutil,subprocess
from pathlib import Path

def main():
    p=argparse.ArgumentParser();p.add_argument('role',choices=['controller','worker002']);a=p.parse_args()
    if a.role=='worker002':
        auth=Path('/root/.ssh/authorized_keys');rows=auth.read_text().splitlines(keepends=True)
        owned=[r for r in rows if 'maas-inventory-pilot-20261020' in r]
        if len(owned)!=1 or 'command="/usr/bin/python3 /root/wtaobj/maas-framework/scripts/maas/runtime/inventory_agent.py"' not in owned[0]:
            raise RuntimeError('Collector key ownership not unique; inspect without modifying SSH access')
        backup=Path('/root/wtaobj/maas-inventory-retired');backup.mkdir(mode=0o700)
        shutil.copy2(auth,backup/'authorized_keys.before');(backup/'authorized_keys.before').chmod(0o600)
        config=Path('/root/wtaobj/maas-inventory/config.json')
        shutil.copy2(config,backup/'config.json');(backup/'config.json').chmod(0o600)
        remaining=[r for r in rows if r not in owned]
        tmp=auth.with_suffix('.e6-new');tmp.write_text(''.join(remaining));tmp.chmod(0o600);os.replace(tmp,auth)
        assert auth.read_text().splitlines(keepends=True)==remaining
        config.unlink()
        print(json.dumps({'collector_key_rows_removed':1,'other_authorizations_preserved':True,'inventory_config_removed':True}))
    else:
        live=Path('/root/wtaobj/maas-live');report=json.loads((live/'e6-deployment.json').read_text())
        assert report['state']=='DEPLOYED'
        backup=Path(report['rollback_directory'])/'collector-assets';assert (backup/'inventory_ed25519').is_file()
        run=lambda *args:subprocess.check_output(args,text=True,stderr=subprocess.PIPE,timeout=30)
        assert run('systemctl','show','maas-collector.service','-p','ActiveState','--value').strip()=='inactive'
        root=Path('/root/wtaobj/maas-runtime');removed=[]
        for name in ('collector.json','inventory_ed25519','inventory_ed25519.pub','known_hosts','preflight.json','health.json','pin.json','consumer'):
            path=root/name
            if not path.exists():continue
            if not (backup/name).exists():raise RuntimeError('Rollback missing: '+name)
            if path.is_dir():shutil.rmtree(path)
            else:path.unlink()
            removed.append(name)
        unit=Path('/etc/systemd/system/maas-collector.service');unit.unlink();unit.symlink_to('/dev/null')
        run('systemctl','daemon-reload')
        run('systemctl','disable','maas-endurance-night-20261021.timer')
        print(json.dumps({'removed_collector_assets':removed,'collector_permanently_masked':True,'rollback_preserved':True}))
if __name__=='__main__':main()
