"""Pilot worker trust and read-only evidence; never restart Docker/VM/worker.

The operator verifies the public SSH endpoint and expected boot before prepare.
Only a previously absent certs.d directory is added; existing trust is not replaced.
"""
import argparse,hashlib,json,socket,subprocess
from pathlib import Path
ROOT=Path('/root/wtaobj')
REGISTRY='10.11.31.1:18443'


def command(args):return subprocess.check_output(args,text=True).strip()


def save(name,data):
    p=ROOT/name;p.write_text(json.dumps(data,indent=2)+'\n');p.chmod(0o600)


def prepare(args):
    assert socket.gethostname()=='XZ-31-002' and command(['uptime','-s'])==args.expected_boot
    data=(ROOT/'registry-pilot.crt').read_bytes()
    assert hashlib.sha256(data).hexdigest()==args.certificate_sha256
    trust=Path('/etc/docker/certs.d')/REGISTRY
    assert not trust.exists() and not (ROOT/'e5-registry-baseline.json').exists()
    rows=json.loads(command(['docker','inspect',*command(['docker','ps','-q']).split()]))
    for image in [args.image,args.expected_image_id]:
        assert subprocess.run(['docker','image','inspect',image],capture_output=True).returncode!=0
    baseline={'boot':command(['uptime','-s']),'docker_pid':command(['systemctl','show','docker','-p','MainPID','--value']),
        'containers':[{'name':r['Name'],'id':r['Id'],'started_at':r['State']['StartedAt'],'restarts':r['RestartCount']} for r in rows],
        'canonical_image_absent_before':True,'source_config_absent_before':True}
    save('e5-registry-baseline.json',baseline)
    trust.mkdir(parents=True,mode=0o755);(trust/'ca.crt').write_bytes(data);(trust/'ca.crt').chmod(0o644)
    print(json.dumps({'trust_installed':True,'cache_absent_before':True,'docker_restarted':False}))


def check_image(args):
    image=json.loads(command(['docker','image','inspect',args.image]))[0]
    assert image['Id']==args.expected_image_id and args.image in image['RepoDigests']
    value={'image_id':image['Id'],'canonical_digest_present':True,'registry_repo_digest':args.image,'native_cold_pull_verified':True}
    save('e5-registry-cold-image.json',value);print(json.dumps(value))


def verify():
    before=json.loads((ROOT/'e5-registry-baseline.json').read_text());checks=[]
    for c in before['containers']:
        r=json.loads(command(['docker','inspect',c['id']]))[0]
        assert r['State']['Running'] and r['State']['StartedAt']==c['started_at'] and r['RestartCount']==c['restarts']
        checks.append({'name':c['name'],'id_and_start_unchanged':True})
    assert before['docker_pid']==command(['systemctl','show','docker','-p','MainPID','--value'])
    assert before['boot']==command(['uptime','-s'])
    remaining=[x for x in command(['docker','ps','-a','--format','{{.Names}}']).splitlines() if x.startswith('maas-e5-registry-pilot')]
    assert not remaining
    value={'original_containers':checks,'docker_pid_unchanged':True,'boot_unchanged':True,'remaining_probe_containers':remaining}
    save('e5-registry-final.json',value);print(json.dumps(value))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('phase',choices=['prepare','image','verify'])
    p.add_argument('--expected-boot');p.add_argument('--certificate-sha256');p.add_argument('--image');p.add_argument('--expected-image-id')
    a=p.parse_args()
    if a.phase=='prepare':prepare(a)
    elif a.phase=='image':check_image(a)
    else:verify()
