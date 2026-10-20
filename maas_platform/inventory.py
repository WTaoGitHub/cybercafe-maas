"""Read-only, narrowly scoped Docker inventory. No caller-supplied shell commands."""
import json,re,subprocess,time


def collect_inventory(worker, image, prefix, run=None, docker_socket=None):
    if not re.fullmatch(r'[A-Za-z0-9_./:@-]+',image) or not re.fullmatch(r'[a-z0-9-]{1,80}',prefix):
        raise ValueError('Invalid inventory scope')
    run=run or (lambda args:subprocess.check_output(args,text=True,timeout=8,stderr=subprocess.PIPE))
    docker=['docker']
    if docker_socket:
        if not str(docker_socket).startswith('/'):raise ValueError('Docker socket must be absolute')
        docker+=['-H','unix://'+str(docker_socket)]
    image_id=run(docker+['image','inspect','--format','{{.Id}}',image]).strip()
    if not re.fullmatch(r'sha256:[a-f0-9]{64}',image_id):raise ValueError('Invalid image ID')
    names=run(docker+['ps','--format','{{.Names}}']).splitlines()
    names=[n for n in names if n.startswith(prefix+'-') and n.endswith('-run-0')]
    if len(names)!=1:raise ValueError('Expected one scoped running model instance')
    # Select only content/state fields, never container environment or mounts.
    row=json.loads(run(docker+['inspect','--format',
        '{"image_id":{{json .Image}},"running":{{json .State.Running}},"paused":{{json .State.Paused}}}',names[0]]))
    if not row['running'] or row['paused'] or row['image_id']!=image_id:
        raise ValueError('Runtime image or state does not match approved scope')
    return {'worker':worker,'image_name':image,'image_id':image_id,'running_image_ids':[row['image_id']],
            'paused':False,'observed_at':time.time()}
