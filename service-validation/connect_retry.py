"""Establish one reusable authenticated SSH channel, without changing sshd."""
import subprocess,time
from pathlib import Path
root=Path(__file__).resolve().parent;sock=root/'ssh-001.sock'
command=['ssh','-M','-S',str(sock),'-NT','-o','ControlPersist=no','-o','ServerAliveInterval=15','-o','ServerAliveCountMax=4','-o','BatchMode=yes','-o','ConnectTimeout=8','-o','ProxyCommand=ssh -o BatchMode=yes -i /Users/4paradigm/.ssh/id_ed25519 -p 16721 -W %h:%p root@111.4.255.126','-o','HostKeyAlias=[111.4.255.126]:2023','-o','StrictHostKeyChecking=yes','-i','/Users/4paradigm/.ssh/id_ed25519','root@10.11.31.1']
for attempt in range(20):
    log=(root/'ssh-master.log').open('a')
    process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=log,stderr=log)
    for _ in range(30):
        if process.poll() is not None:break
        if sock.exists():
            check=subprocess.run(['ssh','-S',str(sock),'-O','check','root@10.11.31.1'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            if check.returncode==0:
                print('Foreground reusable controller SSH connection established.',flush=True)
                raise SystemExit(process.wait())
        time.sleep(.2)
    if process.poll() is None:process.terminate();process.wait()
    log.close();print('SSH capacity retry',attempt+1,flush=True);time.sleep(4)
raise SystemExit('Controller SSH connection limit persists')
